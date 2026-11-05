package com.edunurse.quickfile

import com.chaquo.python.PyException
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.currentCoroutineContext
import org.json.JSONObject
import java.io.File

/** كلاس public عشان Python (Chaquopy) يستدعي الدوال دي مباشرة */
class ExeCallback(private val onProgress: (Int, Int) -> Unit, private val isCancelled: () -> Boolean) {
    fun progress(done: Int, total: Int) = onProgress(done, total)
    fun cancelled(): Boolean = isCancelled()
}

object Exe {
    suspend fun convert(c: Ctx): List<File> {
        if (!Python.isStarted()) Python.start(AndroidPlatform(c.app))
        val bridge = Python.getInstance().getModule("exe_bridge")
        val job = currentCoroutineContext()[Job]
        val level = c.s("level").ifBlank { "bal" }
        val outs = ArrayList<File>()
        val notes = ArrayList<String>()
        for ((i, f) in c.inputs.withIndex()) {
            val dst = c.out("${f.base}.pdf")
            val cb = ExeCallback(
                { done, total -> c.progress((i + done.toFloat() / total) / c.inputs.size, "تحويل الكتاب: $done/$total") },
                { job?.isActive == false }
            )
            try {
                val json = JSONObject(bridge.callAttr("run", f.file.path, dst.path, level, cb).toString())
                val issues = json.optJSONArray("issues")?.length() ?: 0
                if (issues > 0) notes.add("${f.name}: $issues مشاكل بسيطة في بعض الصفحات")
            } catch (e: PyException) {
                val m = e.message ?: ""
                if (m.contains("Cancelled")) throw CancellationException("cancelled")
                throw IllegalStateException("الكتاب ده مش مدعوم أو الملف تالف:\n" + m.substringAfter(": ", m).take(300))
            }
            outs.add(dst)
        }
        if (notes.isNotEmpty()) c.note = "ملاحظات: " + notes.joinToString(" · ")
        return outs
    }
}
