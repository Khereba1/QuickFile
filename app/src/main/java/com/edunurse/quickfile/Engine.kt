package com.edunurse.quickfile

import android.content.ContentValues
import android.content.Context
import android.net.Uri
import android.os.PowerManager
import android.provider.MediaStore
import android.provider.OpenableColumns
import android.webkit.MimeTypeMap
import androidx.core.content.FileProvider
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.MutableStateFlow
import java.io.File

data class Picked(val uri: Uri, val name: String, val size: Long)

sealed interface JobState {
    data object Idle : JobState
    data class Running(val toolId: String, val label: String, val progress: Float) : JobState
    data class Done(val toolId: String, val files: List<File>, val note: String) : JobState
    data class Failed(val toolId: String, val msg: String) : JobState
}

object Fs {
    fun info(ctx: Context, uri: Uri): Pair<String, Long> {
        var n = "file"
        var s = 0L
        ctx.contentResolver.query(uri, null, null, null, null)?.use { c ->
            if (c.moveToFirst()) {
                val i = c.getColumnIndex(OpenableColumns.DISPLAY_NAME)
                if (i >= 0) n = c.getString(i) ?: n
                val j = c.getColumnIndex(OpenableColumns.SIZE)
                if (j >= 0 && !c.isNull(j)) s = c.getLong(j)
            }
        }
        return n to s
    }

    fun copyIn(ctx: Context, uri: Uri, dir: File, name: String): File {
        dir.mkdirs()
        val f = File(dir, name.replace(Regex("[\\\\/:*?\"<>|]"), "_"))
        ctx.contentResolver.openInputStream(uri)!!.use { i -> f.outputStream().use { o -> i.copyTo(o) } }
        return f
    }

    fun mime(f: File): String =
        MimeTypeMap.getSingleton().getMimeTypeFromExtension(f.extension.lowercase()) ?: "application/octet-stream"

    fun uriFor(ctx: Context, f: File): Uri = FileProvider.getUriForFile(ctx, ctx.packageName + ".files", f)

    fun saveToDownloads(ctx: Context, f: File): Boolean = try {
        val v = ContentValues().apply {
            put(MediaStore.Downloads.DISPLAY_NAME, f.name)
            put(MediaStore.Downloads.MIME_TYPE, mime(f))
            put(MediaStore.Downloads.RELATIVE_PATH, "Download/QuickFile")
            put(MediaStore.Downloads.IS_PENDING, 1)
        }
        val r = ctx.contentResolver
        val uri = r.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, v)!!
        r.openOutputStream(uri)!!.use { o -> f.inputStream().use { it.copyTo(o) } }
        v.clear()
        v.put(MediaStore.Downloads.IS_PENDING, 0)
        r.update(uri, v, null, null)
        true
    } catch (e: Exception) {
        false
    }

    fun size(n: Long): String = when {
        n >= 1L shl 30 -> "%.2f GB".format(n / 1073741824.0)
        n >= 1L shl 20 -> "%.1f MB".format(n / 1048576.0)
        n >= 1L shl 10 -> "%d KB".format(n shr 10)
        else -> "$n B"
    }
}

object Jobs {
    val state = MutableStateFlow<JobState>(JobState.Idle)
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private var job: Job? = null

    fun start(app: Context, tool: Tool, picks: List<Picked>, opts: Map<String, String>) {
        if (state.value is JobState.Running) return
        val ctx = app.applicationContext
        state.value = JobState.Running(tool.id, "تجهيز الملفات…", 0f)
        KeepAliveService.start(ctx)
        job = scope.launch {
            val stamp = System.currentTimeMillis().toString()
            val work = File(ctx.cacheDir, "job_$stamp")
            val outDir = File(ctx.filesDir, "out/$stamp").also { it.mkdirs() }
            val wl = (ctx.getSystemService(Context.POWER_SERVICE) as PowerManager)
                .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "quickfile:job")
            wl.acquire(3 * 60 * 60 * 1000L)
            try {
                val ins = withContext(Dispatchers.IO) {
                    picks.mapIndexed { i, p -> In(p.name, Fs.copyIn(ctx, p.uri, File(work, "$i"), p.name)) }
                }
                val c = Ctx(ctx, ins, opts, outDir) { p, l ->
                    state.value = JobState.Running(tool.id, l, p.coerceIn(0f, 1f))
                }
                val files = withContext(Dispatchers.IO) { tool.exec(c) }
                state.value = JobState.Done(tool.id, files, c.note)
            } catch (e: CancellationException) {
                outDir.deleteRecursively()
                state.value = JobState.Idle
                throw e
            } catch (e: Throwable) {
                outDir.deleteRecursively()
                state.value = JobState.Failed(tool.id, (e.message ?: e.javaClass.simpleName).take(500))
            } finally {
                work.deleteRecursively()
                if (wl.isHeld) wl.release()
                KeepAliveService.stop(ctx)
            }
        }
    }

    fun cancel() {
        val s = state.value
        if (s is JobState.Running) state.value = s.copy(label = "جاري الإلغاء…")
        job?.cancel()
    }

    fun reset() {
        if (state.value !is JobState.Running) state.value = JobState.Idle
    }

    fun cleanup(ctx: Context) {
        File(ctx.filesDir, "out").listFiles()?.forEach {
            if (System.currentTimeMillis() - it.lastModified() > 3 * 86400000L) it.deleteRecursively()
        }
    }
}
