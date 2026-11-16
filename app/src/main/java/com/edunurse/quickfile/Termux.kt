package com.edunurse.quickfile

import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Environment
import android.provider.DocumentsContract
import androidx.core.content.ContextCompat
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.delay
import kotlinx.coroutines.ensureActive
import java.io.File

/**
 * جسر تجريبي لتشغيل أوامر في Termux (البيئة الأساسية أو Ubuntu عبر proot-distro).
 * تبادل الملفات بيتم في مجلد بيختاره المستخدم (SAF) وTermux بيقراه من نفس المسار الحقيقي.
 */
object Termux {
    const val PERM = "com.termux.permission.RUN_COMMAND"
    private const val BASH = "/data/data/com.termux/files/usr/bin/bash"
    private const val PROOT = "/data/data/com.termux/files/usr/bin/proot-distro"
    private const val HOME = "/data/data/com.termux/files/home"

    fun installed(ctx: Context): Boolean = try { ctx.packageManager.getPackageInfo("com.termux", 0); true } catch (_: Exception) { false }
    fun hasPerm(ctx: Context): Boolean = ContextCompat.checkSelfPermission(ctx, PERM) == PackageManager.PERMISSION_GRANTED

    /** أوامر الإعداد اللي بيلصقها المستخدم في Termux مرة واحدة */
    fun setupCommands(env: String): String {
        val common = "termux-setup-storage\nmkdir -p ~/.termux && echo 'allow-external-apps=true' >> ~/.termux/termux.properties && termux-reload-settings\n"
        return if (env == "ubuntu") {
            common + "pkg install -y proot-distro\nproot-distro install ubuntu\n" +
                "proot-distro login ubuntu -- bash -c \"apt update && apt install -y libreoffice-writer libreoffice-impress libreoffice-calc fonts-noto-core fonts-hosny-amiri\"\n"
        } else {
            common + "pkg install -y tur-repo && pkg update -y && pkg install -y libreoffice\n"
        }
    }

    private fun docUri(tree: Uri, id: String): Uri = DocumentsContract.buildDocumentUriUsingTree(tree, id)

    private fun create(ctx: Context, tree: Uri, parentId: String, mime: String, name: String): String {
        val u = DocumentsContract.createDocument(ctx.contentResolver, docUri(tree, parentId), mime, name)
            ?: throw IllegalStateException("تعذّر إنشاء ملف في مجلد التبادل")
        return DocumentsContract.getDocumentId(u)
    }

    private fun children(ctx: Context, tree: Uri, parentId: String): List<Triple<String, String, String>> {
        val out = ArrayList<Triple<String, String, String>>()
        val cols = arrayOf(
            DocumentsContract.Document.COLUMN_DOCUMENT_ID,
            DocumentsContract.Document.COLUMN_DISPLAY_NAME,
            DocumentsContract.Document.COLUMN_MIME_TYPE
        )
        ctx.contentResolver.query(DocumentsContract.buildChildDocumentsUriUsingTree(tree, parentId), cols, null, null, null)?.use { c ->
            while (c.moveToNext()) out.add(Triple(c.getString(0), c.getString(1), c.getString(2)))
        }
        return out
    }

    private fun readText(ctx: Context, tree: Uri, id: String): String =
        try { ctx.contentResolver.openInputStream(docUri(tree, id))?.bufferedReader()?.use { it.readText() } ?: "" } catch (_: Exception) { "" }

    private fun asset(ctx: Context, name: String): String = ctx.assets.open("termux/$name").bufferedReader().use { it.readText() }

    private fun mmss(sec: Long) = "%d:%02d".format(sec / 60, sec % 60)

    /** يشغّل `body` (سكريبت shell بيشتغل جوه مجلد المهمة: in/ للمدخلات و out/ للمخرجات) ويرجّع ملفات out/ */
    suspend fun exec(ctx: Context, body: String, inputs: List<File>, outDir: File, progress: (Float, String) -> Unit): List<File> {
        if (!installed(ctx)) throw IllegalStateException("Termux مش متثبت على الموبايل")
        if (!hasPerm(ctx)) throw IllegalStateException("لازم تدي التطبيق إذن «تشغيل أوامر في بيئة Termux» من الإعدادات")
        if (Prefs.termuxDir.isBlank()) throw IllegalStateException("اختار مجلد التبادل من إعدادات Termux")
        val tree = Uri.parse(Prefs.termuxDir)
        val rootId = DocumentsContract.getTreeDocumentId(tree)
        if (!rootId.startsWith("primary:")) throw IllegalStateException("مجلد التبادل لازم يكون في الذاكرة الداخلية")
        val jobName = "job_" + System.currentTimeMillis()
        val jobId = create(ctx, tree, rootId, DocumentsContract.Document.MIME_TYPE_DIR, jobName)
        try {
            val inId = create(ctx, tree, jobId, DocumentsContract.Document.MIME_TYPE_DIR, "in")
            val outId = create(ctx, tree, jobId, DocumentsContract.Document.MIME_TYPE_DIR, "out")
            progress(0.02f, "نقل الملفات لـ Termux…")
            for (f in inputs) {
                currentCoroutineContext().ensureActive()
                val id = create(ctx, tree, inId, "application/octet-stream", f.name)
                ctx.contentResolver.openOutputStream(docUri(tree, id))!!.use { o -> f.inputStream().use { it.copyTo(o) } }
            }
            val script = asset(ctx, "run_wrapper.sh").replace("@BODY@", body)
            val sid = create(ctx, tree, jobId, "application/octet-stream", "run.sh")
            ctx.contentResolver.openOutputStream(docUri(tree, sid))!!.use { it.write(script.toByteArray()) }

            // المسار الحقيقي لمجلد المهمة (Termux بيقراه من هنا)
            val rel = rootId.substringAfter(':', "")
            val hostJob = listOf(Environment.getExternalStorageDirectory().absolutePath, rel, jobName).filter { it.isNotEmpty() }.joinToString("/")
            val ubuntu = Prefs.termuxEnv == "ubuntu"
            val intent = Intent().setClassName("com.termux", "com.termux.app.RunCommandService").setAction("com.termux.RUN_COMMAND")
            if (ubuntu) {
                intent.putExtra("com.termux.RUN_COMMAND_PATH", PROOT)
                intent.putExtra("com.termux.RUN_COMMAND_ARGUMENTS", arrayOf("login", "ubuntu", "--bind", "$hostJob:/qf", "--", "bash", "/qf/run.sh"))
            } else {
                intent.putExtra("com.termux.RUN_COMMAND_PATH", BASH)
                intent.putExtra("com.termux.RUN_COMMAND_ARGUMENTS", arrayOf("$hostJob/run.sh"))
            }
            intent.putExtra("com.termux.RUN_COMMAND_WORKDIR", HOME)
            intent.putExtra("com.termux.RUN_COMMAND_BACKGROUND", true)
            progress(0.05f, "تشغيل Termux…")
            try { ctx.startService(intent) } catch (e: SecurityException) {
                throw IllegalStateException("Termux رفض الأمر: اتأكد من منح الإذن ومن allow-external-apps=true")
            } catch (e: Exception) {
                throw IllegalStateException("مقدرتش أشغّل Termux (ممكن موفّر البطارية بيمنعه): " + (e.message ?: ""))
            }

            // استنى علامة البدء، وبعدها علامة الانتهاء
            val t0 = System.currentTimeMillis()
            var started = false
            while (true) {
                currentCoroutineContext().ensureActive()
                delay(1500)
                val sec = (System.currentTimeMillis() - t0) / 1000
                val names = children(ctx, tree, jobId).associate { it.second to it.first }
                if (!started && names.containsKey("started.txt")) started = true
                if (names.containsKey("done.txt")) break
                if (!started && sec > 45) {
                    throw IllegalStateException("Termux ما ردّش. اتأكد من: allow-external-apps=true، إذن التخزين لـ Termux، واستثناءه من موفّر البطارية.")
                }
                if (sec > 30 * 60) throw IllegalStateException("العملية أخدت وقت طويل جدًا فوقفتها")
                progress((sec.toFloat() / (sec + 90f)).coerceIn(0.06f, 0.92f), "Termux: " + tr(if (started) "جاري التحويل…" else "بنستنى Termux يبدأ…") + " " + mmss(sec))
            }
            val names = children(ctx, tree, jobId)
            val code = names.firstOrNull { it.second == "done.txt" }?.let { readText(ctx, tree, it.first).trim() } ?: "?"
            val log = names.firstOrNull { it.second == "log.txt" }?.let { readText(ctx, tree, it.first) }.orEmpty()
            progress(0.95f, "نقل النتيجة…")
            outDir.mkdirs()
            val outs = ArrayList<File>()
            for ((id, name, mime) in children(ctx, tree, outId)) {
                if (mime == DocumentsContract.Document.MIME_TYPE_DIR) continue
                val dst = File(outDir, name)
                ctx.contentResolver.openInputStream(docUri(tree, id))?.use { i -> dst.outputStream().use { o -> i.copyTo(o) } }
                outs.add(dst)
            }
            if (outs.isEmpty()) {
                val tail = log.trim().lines().takeLast(6).joinToString("\n").take(500)
                throw IllegalStateException("التحويل فشل (كود $code):\n$tail")
            }
            return outs
        } finally {
            try { DocumentsContract.deleteDocument(ctx.contentResolver, docUri(tree, jobId)) } catch (_: Exception) { }
        }
    }

    /** فحص الجاهزية: بيرجّع تقرير نصي */
    suspend fun check(ctx: Context): String {
        val tmp = File(ctx.cacheDir, "tcheck").also { it.mkdirs() }
        val outs = exec(ctx, asset(ctx, "check.sh"), emptyList(), tmp) { _, _ -> }
        return outs.firstOrNull { it.name == "check.txt" }?.readText()?.trim().orEmpty().ifBlank { "تمام" }
    }
}

object Office {
    /** تحويل مستندات Office بـ LibreOffice جوه Termux */
    suspend fun convert(c: Ctx, pdfImport: Boolean): List<File> {
        val target = if (pdfImport) "docx:\"MS Word 2007 XML\"" else c.s("to").ifBlank { "pdf" }
        val infilter = if (pdfImport) "--infilter=\"writer_pdf_import\"" else ""
        val body = c.app.assets.open("termux/office.sh").bufferedReader().use { it.readText() }
            .replace("@TARGET@", target).replace("@INFILTER@", infilter)
        c.progress(0f, "تحويل عبر Termux…")
        return Termux.exec(c.app, body, c.inputs.map { it.file }, c.outDir) { p, l -> c.progress(p, l) }
    }
}
