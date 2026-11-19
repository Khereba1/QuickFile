package com.edunurse.quickfile

import android.content.Context
import android.content.Intent
import android.os.Build
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/** سجل أخطاء محلي بسيط (بيتصدّر يدويًا من الإعدادات، مفيش حاجة بتتبعت لأي مكان) */
object ErrLog {
    private var file: File? = null

    fun init(ctx: Context) {
        file = File(ctx.filesDir, "errors.log")
    }

    fun add(tool: String, msg: String, t: Throwable?) {
        val f = file ?: return
        try {
            val sb = StringBuilder()
            sb.append(SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.US).format(Date())).append("  [").append(tool).append("]\n")
            sb.append(msg).append('\n')
            t?.stackTrace?.take(8)?.forEach { sb.append("  at ").append(it).append('\n') }
            sb.append('\n')
            f.appendText(sb.toString())
            if (f.length() > 120_000) f.writeText(f.readText().takeLast(60_000))
        } catch (_: Exception) { }
    }

    fun clear() {
        try { file?.delete() } catch (_: Exception) { }
    }

    private fun header(ctx: Context): String {
        val ver = try { ctx.packageManager.getPackageInfo(ctx.packageName, 0).versionName } catch (_: Exception) { "?" }
        return "QuickFile $ver\nAndroid ${Build.VERSION.SDK_INT} · ${Build.MANUFACTURER} ${Build.MODEL} · ${Build.SUPPORTED_ABIS.joinToString()}\n" +
            "lang=${Prefs.lang} font=${Prefs.font} motion=${Prefs.motion} termux=${Prefs.termux}/${Prefs.termuxEnv}\n\n"
    }

    /** بيجهّز التقرير ويفتح قايمة المشاركة */
    fun share(ctx: Context) {
        val body = try { file?.takeIf { it.exists() }?.readText() } catch (_: Exception) { null }
        val out = File(ctx.cacheDir, "QuickFile-log.txt")
        out.writeText(header(ctx) + (body ?: "(no errors recorded)\n"))
        val i = Intent(Intent.ACTION_SEND).setType("text/plain").putExtra(Intent.EXTRA_STREAM, Fs.uriFor(ctx, out))
            .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        ctx.startActivity(Intent.createChooser(i, "share").addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
    }
}
