package com.edunurse.quickfile

import android.content.ContentValues
import android.content.Context
import android.net.Uri
import android.os.Handler
import android.os.Looper
import android.os.PowerManager
import android.provider.DocumentsContract
import android.provider.MediaStore
import android.provider.OpenableColumns
import android.webkit.MimeTypeMap
import android.widget.Toast
import androidx.core.content.FileProvider
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.MutableStateFlow
import java.io.File

data class Picked(val uri: Uri, val name: String, val size: Long, val mime: String? = null) {
    val kind: String get() = kindOf(name, mime)
}

sealed interface JobState {
    data object Idle : JobState
    data class Running(val toolId: String, val label: String, val progress: Float) : JobState
    data class Done(val toolId: String, val files: List<File>, val note: String, val inSize: Long = 0L) : JobState
    data class Failed(val toolId: String, val msg: String) : JobState
}

/** مهمة في الطابور */
data class QJob(val id: Long, val tool: Tool, val picks: List<Picked>, val opts: Map<String, String>)

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

    fun pick(ctx: Context, uri: Uri): Picked {
        val (n, s) = info(ctx, uri)
        val mime = ctx.contentResolver.getType(uri)
        var name = n
        if ('.' !in name) {
            MimeTypeMap.getSingleton().getExtensionFromMimeType(mime)?.let { name = "$name.$it" }
        }
        return Picked(uri, name, s, mime)
    }

    fun copyToCache(ctx: Context, uri: Uri, name: String, sub: String): File =
        copyIn(ctx, uri, File(ctx.cacheDir, "$sub/${System.nanoTime()}"), name)

    fun mime(f: File): String =
        MimeTypeMap.getSingleton().getMimeTypeFromExtension(f.extension.lowercase()) ?: "application/octet-stream"

    fun uriFor(ctx: Context, f: File): Uri = FileProvider.getUriForFile(ctx, ctx.packageName + ".files", f)

    /** يحفظ في المجلد اللي اختاره المستخدم، أو في Download/QuickFile لو ما اختارش */
    fun save(ctx: Context, f: File): Boolean {
        val tree = Prefs.saveTree
        if (tree.isNotBlank()) {
            try { if (saveToTree(ctx, Uri.parse(tree), f)) return true } catch (_: Exception) { }
        }
        return saveToDownloads(ctx, f)
    }

    private fun saveToTree(ctx: Context, tree: Uri, f: File): Boolean {
        val parent = DocumentsContract.buildDocumentUriUsingTree(tree, DocumentsContract.getTreeDocumentId(tree))
        val out = DocumentsContract.createDocument(ctx.contentResolver, parent, mime(f), f.name) ?: return false
        ctx.contentResolver.openOutputStream(out)!!.use { o -> f.inputStream().use { it.copyTo(o) } }
        return true
    }

    fun treeLabel(tree: String): String = try {
        Uri.decode(DocumentsContract.getTreeDocumentId(Uri.parse(tree))).substringAfter(':').ifBlank { "/" }
    } catch (_: Exception) { "" }

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
    val queue = MutableStateFlow<List<QJob>>(emptyList())
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private var job: Job? = null
    private var seq = 0L
    private val main = Handler(Looper.getMainLooper())

    private fun toast(ctx: Context, msg: String) {
        main.post { Toast.makeText(ctx, msg, Toast.LENGTH_LONG).show() }
    }

    /** لو فيه مهمة شغالة بتتضاف للطابور وتشتغل بعدها تلقائي */
    fun start(app: Context, tool: Tool, picks: List<Picked>, opts: Map<String, String>) {
        val ctx = app.applicationContext
        synchronized(this) {
            val q = QJob(++seq, tool, picks, opts)
            if (state.value is JobState.Running) {
                queue.value = queue.value + q
                toast(ctx, tr("اتضافت للطابور"))
            } else runJob(ctx, q)
        }
    }

    private fun runJob(ctx: Context, q: QJob) {
        state.value = JobState.Running(q.tool.id, "تجهيز الملفات…", 0f)
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
                    q.picks.mapIndexed { i, p -> In(p.name, Fs.copyIn(ctx, p.uri, File(work, "$i"), p.name)) }
                }
                val c = Ctx(ctx, ins, q.opts, outDir) { p, l ->
                    state.value = JobState.Running(q.tool.id, l, p.coerceIn(0f, 1f))
                }
                val files = withContext(Dispatchers.IO) { q.tool.exec(c) }
                withContext(Dispatchers.Main) { History.add(files, q.tool.title) }
                state.value = JobState.Done(q.tool.id, files, c.note, q.picks.sumOf { it.size })
                var saved = ""
                if (Prefs.autoSave) {
                    val ok = withContext(Dispatchers.IO) { files.count { Fs.save(ctx, it) } }
                    saved = if (ok == files.size) tr(if (Prefs.saveTree.isBlank()) "اتحفظ في Download/QuickFile" else "اتحفظ في المجلد المحدد")
                    else tr("فشل حفظ") + ": ${files.size - ok}"
                }
                if (queue.value.isNotEmpty() || saved.isNotEmpty()) {
                    toast(ctx, "✓ " + tr(q.tool.title) + (if (saved.isNotEmpty()) " · $saved" else ""))
                }
            } catch (e: CancellationException) {
                outDir.deleteRecursively()
                state.value = JobState.Idle
                throw e
            } catch (e: Throwable) {
                outDir.deleteRecursively()
                ErrLog.add(q.tool.id, e.message ?: e.javaClass.simpleName, e)
                state.value = JobState.Failed(q.tool.id, (e.message ?: e.javaClass.simpleName).take(500))
            } finally {
                work.deleteRecursively()
                if (wl.isHeld) wl.release()
                val next = synchronized(this@Jobs) {
                    val n = queue.value.firstOrNull()
                    if (n != null) queue.value = queue.value.drop(1)
                    n
                }
                if (next != null) runJob(ctx, next) else KeepAliveService.stop(ctx)
            }
        }
    }

    fun cancel() {
        val s = state.value
        if (s is JobState.Running) state.value = s.copy(label = "جاري الإلغاء…")
        job?.cancel()
    }

    fun removeQueued(id: Long) {
        synchronized(this) { queue.value = queue.value.filter { it.id != id } }
    }

    fun reset() {
        if (state.value !is JobState.Running) state.value = JobState.Idle
    }

    fun cleanup(ctx: Context) {
        listOf("org", "view").forEach { File(ctx.cacheDir, it).deleteRecursively() }
        val keep = History.items.mapNotNull { File(it.path).parentFile?.absolutePath }.toSet()
        File(ctx.filesDir, "out").listFiles()?.forEach {
            if (it.absolutePath !in keep && System.currentTimeMillis() - it.lastModified() > 86400000L) it.deleteRecursively()
        }
    }
}
