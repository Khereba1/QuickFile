package com.edunurse.quickfile

import com.arthenica.ffmpegkit.FFmpegKit
import com.arthenica.ffmpegkit.FFprobeKit
import com.arthenica.ffmpegkit.ReturnCode
import kotlinx.coroutines.suspendCancellableCoroutine
import java.io.File
import java.util.Locale
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException

object FF {
    private var encs: Set<String>? = null

    fun encoders(): Set<String> {
        encs?.let { return it }
        val out = FFmpegKit.execute("-hide_banner -encoders").output ?: ""
        val set = Regex("""^\s*[VAS][.A-Z]{5}\s+(\S+)""", RegexOption.MULTILINE)
            .findAll(out).map { it.groupValues[1] }.toSet()
        encs = set
        return set
    }

    fun need(enc: String, what: String) {
        if (enc !in encoders()) throw IllegalStateException("$what غير مدعوم في نسخة FFmpeg المضمّنة. جرّب صيغة تانية.")
    }

    fun durationOf(f: File): Double = try {
        FFprobeKit.getMediaInformation(f.path).mediaInformation?.duration?.toDoubleOrNull() ?: 0.0
    } catch (_: Throwable) {
        0.0
    }

    private fun tail(s: String?): String =
        "فشلت المعالجة:\n" + (s ?: "").trim().lines().takeLast(4).joinToString("\n").take(400)

    suspend fun exec(args: List<String>, dur: Double, onP: (Float) -> Unit) = suspendCancellableCoroutine<Unit> { cont ->
        val session = FFmpegKit.executeWithArgumentsAsync(
            arrayOf("-y", "-hide_banner") + args.toTypedArray(),
            { s ->
                if (cont.isActive) {
                    val rc = s.returnCode
                    when {
                        ReturnCode.isSuccess(rc) -> cont.resume(Unit)
                        ReturnCode.isCancel(rc) -> cont.cancel()
                        else -> cont.resumeWithException(RuntimeException(tail(s.output)))
                    }
                }
            },
            null,
            { st -> if (dur > 0) onP((st.time.toDouble() / 1000.0 / dur).toFloat().coerceIn(0f, 1f)) }
        )
        cont.invokeOnCancellation { FFmpegKit.cancel(session.sessionId) }
    }
}

object Media {
    private val AUDIO_EXT = setOf("mp3", "m4a", "aac", "wav", "ogg", "opus", "flac", "amr", "wma", "oga")
    private val AAC = listOf("-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart")

    private fun toSec(t: String): Double =
        t.trim().split(":").map { it.toDoubleOrNull() ?: 0.0 }.fold(0.0) { a, v -> a * 60 + v }

    /** returns encoder args + output extension */
    private fun audioArgs(fmt: String, q: String, kbps: Int? = null): Pair<List<String>, String> {
        val br = if (kbps != null) "${kbps}k" else when (q) { "high" -> "192k"; "low" -> "64k"; else -> "128k" }
        return when (fmt) {
            "mp3" -> { FF.need("libmp3lame", "MP3"); listOf("-c:a", "libmp3lame", "-b:a", br) to "mp3" }
            "opus" -> { FF.need("libopus", "Opus"); listOf("-c:a", "libopus", "-b:a", if (kbps == null && q == "low") "32k" else br) to "ogg" }
            "voice" -> { FF.need("libopus", "الرسالة الصوتية"); listOf("-c:a", "libopus", "-b:a", "32k", "-ac", "1", "-ar", "48000", "-application", "voip") to "ogg" }
            "flac" -> listOf("-c:a", "flac") to "flac"
            "wav" -> listOf("-c:a", "pcm_s16le") to "wav"
            else -> listOf("-c:a", "aac", "-b:a", br) to "m4a"
        }
    }

    private fun vArgs(level: String, engine: String, res: String): List<String> {
        val e = FF.encoders()
        val crf = when (level) { "light" -> "23"; "strong" -> "31"; else -> "27" }
        val kbps = when (level) { "light" -> "2500k"; "strong" -> "800k"; else -> "1500k" }
        val vf = if (res == "orig" || res.isBlank()) emptyList() else listOf("-vf", "scale=-2:min($res\\,ih)")
        val enc = when {
            engine == "hw" && "h264_mediacodec" in e -> listOf("-c:v", "h264_mediacodec", "-b:v", kbps)
            "libx264" in e -> listOf("-c:v", "libx264", "-preset", "veryfast", "-crf", crf, "-pix_fmt", "yuv420p")
            "h264_mediacodec" in e -> listOf("-c:v", "h264_mediacodec", "-b:v", kbps)
            else -> listOf("-c:v", "mpeg4", "-q:v", "5")
        }
        return vf + enc
    }

    private fun vArgsBitrate(vK: Int, engine: String, height: Int): List<String> {
        val e = FF.encoders()
        val vf = listOf("-vf", "scale=-2:min($height\\,ih)")
        val enc = when {
            engine == "hw" && "h264_mediacodec" in e -> listOf("-c:v", "h264_mediacodec", "-b:v", "${vK}k")
            "libx264" in e -> listOf(
                "-c:v", "libx264", "-preset", "veryfast", "-b:v", "${vK}k", "-maxrate", "${(vK * 1.25).toInt()}k",
                "-bufsize", "${vK * 2}k", "-pix_fmt", "yuv420p"
            )
            "h264_mediacodec" in e -> listOf("-c:v", "h264_mediacodec", "-b:v", "${vK}k")
            else -> listOf("-c:v", "mpeg4", "-b:v", "${vK}k")
        }
        return vf + enc
    }

    private suspend fun one(c: Ctx, i: Int, f: In, label: String, args: List<String>) {
        val n = c.inputs.size
        val tag = if (n > 1) "$label (${i + 1}/$n)" else label
        c.progress(i.toFloat() / n, tag)
        FF.exec(args, FF.durationOf(f.file)) { p -> c.progress((i + p) / n, tag) }
    }

    suspend fun vcompress(c: Ctx): List<File> = c.inputs.mapIndexed { i, f ->
        if (c.isTarget()) vTarget(c, i, f) else {
            val out = c.out("${f.base}_compressed.mp4")
            val args = listOf("-i", f.file.path, "-map", "0:v:0", "-map", "0:a:0?") +
                vArgs(c.s("level"), c.s("engine"), c.s("res")) + AAC + out.path
            one(c, i, f, "ضغط الفيديو", args)
            out
        }
    }

    /** ضغط لحجم مستهدف: نحسب البت ريت من المدة، ولو الناتج كبير نعيد مرة بمعامل تصحيح */
    private suspend fun vTarget(c: Ctx, i: Int, f: In): File {
        val targetBytes = (c.targetMb() ?: throw IllegalStateException("اكتب الحجم المستهدف")) * 1048576.0
        val dur = FF.durationOf(f.file)
        if (dur <= 1.0) throw IllegalStateException("مش قادر أعرف مدة الملف")
        if (f.file.length() <= targetBytes) {
            c.note = "الملف أصغر من الحجم المطلوب أصلًا"
            val o = c.out("${f.base}_compressed.mp4")
            f.file.copyTo(o)
            return o
        }
        val audioK = 96
        var factor = 0.95
        var result: File? = null
        for (attempt in 1..2) {
            val totalK = targetBytes * 8.0 * factor / dur / 1000.0
            val vK = (totalK - audioK).toInt().coerceAtLeast(80)
            val h = when { vK < 300 -> 360; vK < 700 -> 480; vK < 1600 -> 720; else -> 1080 }
            val o = c.out("${f.base}_target.mp4")
            val args = listOf("-i", f.file.path, "-map", "0:v:0", "-map", "0:a:0?") +
                vArgsBitrate(vK, c.s("engine"), h) +
                listOf("-c:a", "aac", "-b:a", "${audioK}k", "-movflags", "+faststart", o.path)
            one(c, i, f, "ضغط لحجم مستهدف (محاولة $attempt)", args)
            val size = o.length().toDouble()
            if (size <= targetBytes * 1.03 || attempt == 2) {
                if (size > targetBytes * 1.03) c.note = "مقدرتش أوصل للحجم المطلوب بالظبط"
                result = o
                break
            }
            factor *= targetBytes / size * 0.97
            o.delete()
        }
        return result ?: throw IllegalStateException("فشل الضغط")
    }

    suspend fun vconvert(c: Ctx): List<File> = c.inputs.mapIndexed { i, f ->
        val t = c.s("to")
        val out = c.out("${f.base}.$t")
        val inp = listOf("-i", f.file.path)
        val args = when (t) {
            "mkv" -> inp + listOf("-map", "0:v", "-map", "0:a?", "-c", "copy", out.path)
            "webm" -> {
                FF.need("libvpx-vp9", "WebM"); FF.need("libopus", "WebM")
                inp + listOf("-c:v", "libvpx-vp9", "-b:v", "0", "-crf", "33", "-deadline", "realtime",
                    "-cpu-used", "8", "-row-mt", "1", "-c:a", "libopus", "-b:a", "96k", out.path)
            }
            else -> inp + listOf("-map", "0:v:0", "-map", "0:a:0?") + vArgs("light", "x264", "orig") + AAC + out.path
        }
        one(c, i, f, "تحويل الصيغة", args)
        out
    }

    suspend fun toAudio(c: Ctx): List<File> = c.inputs.mapIndexed { i, f ->
        var kb: Int? = null
        if (c.isTarget() && c.s("fmt") !in setOf("flac", "wav", "voice")) {
            val mb = c.targetMb() ?: throw IllegalStateException("اكتب الحجم المستهدف")
            val dur = FF.durationOf(f.file)
            if (dur <= 1.0) throw IllegalStateException("مش قادر أعرف مدة الملف")
            kb = (mb * 1048576.0 * 8.0 * 0.95 / dur / 1000.0).toInt().coerceIn(16, 320)
            if (kb <= 24) c.note = "الجودة هتبقى منخفضة جدًا عشان الحجم صغير"
        }
        val (a, ext) = audioArgs(c.s("fmt"), c.s("q"), kb)
        val out = c.out("${f.base}.$ext")
        one(c, i, f, "معالجة الصوت", listOf("-i", f.file.path, "-vn") + a + out.path)
        out
    }

    suspend fun cut(c: Ctx): List<File> {
        val f = c.inputs[0]
        val st = c.s("start").ifBlank { "0" }
        val en = c.s("end").trim()
        val audio = f.ext in AUDIO_EXT
        val total = if (en.isBlank()) FF.durationOf(f.file) else toSec(en)
        val dur = total - toSec(st)
        val range = listOf("-ss", st) + (if (en.isNotBlank()) listOf("-to", en) else emptyList())
        val out: File
        val args: List<String>
        if (!c.on("precise")) {
            out = c.out("${f.base}_cut.${f.ext.ifBlank { "mp4" }}")
            args = range + listOf("-i", f.file.path, "-c", "copy", "-avoid_negative_ts", "make_zero", out.path)
        } else if (audio) {
            val fmt = when (f.ext) { "mp3" -> "mp3"; "flac" -> "flac"; "wav" -> "wav"; "ogg", "opus", "oga" -> "opus"; else -> "m4a" }
            val (a, ext) = audioArgs(fmt, "high")
            out = c.out("${f.base}_cut.$ext")
            args = listOf("-i", f.file.path) + range + a + out.path
        } else {
            out = c.out("${f.base}_cut.mp4")
            args = listOf("-i", f.file.path) + range + vArgs("light", "x264", "orig") + AAC + out.path
        }
        c.progress(0f, "قص المقطع")
        FF.exec(args, dur) { p -> c.progress(p, "قص المقطع") }
        return listOf(out)
    }

    /** يلقط الشرايح من فيديو محاضرة ويبنيها PDF */
    suspend fun slides(c: Ctx): List<File> = c.inputs.mapIndexed { i, f ->
        val dir = File(c.outDir, ".frames_$i").also { it.mkdirs() }
        val vf = if (c.s("mode") == "interval") {
            "fps=1/${c.s("every").ifBlank { "10" }},scale=1280:-2"
        } else {
            val th = when (c.s("sens")) { "low" -> "0.15"; "high" -> "0.03"; else -> "0.06" }
            "select=gt(scene\\,$th)+eq(n\\,0),scale=1280:-2"
        }
        val args = listOf("-i", f.file.path, "-vf", vf, "-fps_mode", "vfr", "-q:v", "4", File(dir, "f_%04d.jpg").path)
        one(c, i, f, "استخراج الشرايح", args)
        val frames = (dir.listFiles()?.filter { it.extension == "jpg" } ?: emptyList()).sortedBy { it.name }
        if (frames.isEmpty()) throw IllegalStateException("مالقيتش شرايح. جرّب حساسية أعلى أو طريقة «كل فترة ثابتة»")
        c.progress((i + 0.9f) / c.inputs.size, "بناء الـ PDF…")
        val out = c.out("${f.base}_slides.pdf")
        Pdf.framesToPdf(c.app, frames, out)
        dir.deleteRecursively()
        out
    }

    /** تسريع / حذف صمت / تقليل ضوضاء / توحيد مستوى الصوت */
    suspend fun tune(c: Ctx): List<File> = c.inputs.mapIndexed { i, f ->
        val speed = c.s("speed").toDoubleOrNull() ?: 1.0
        val filters = ArrayList<String>()
        if (c.on("denoise")) filters.add("afftdn=nf=-25")
        if (c.on("silence")) filters.add("silenceremove=start_periods=1:start_threshold=-45dB:stop_periods=-1:stop_duration=0.6:stop_threshold=-45dB")
        var s = speed
        while (s > 2.0 + 1e-6) { filters.add("atempo=2.0"); s /= 2.0 }
        if (Math.abs(s - 1.0) > 1e-6) filters.add("atempo=" + "%.3f".format(Locale.US, s))
        if (c.on("norm")) filters.add("dynaudnorm")
        val (a, ext) = audioArgs(c.s("fmt"), c.s("q"))
        val out = c.out("${f.base}_tuned.$ext")
        val args = listOf("-i", f.file.path, "-vn") +
            (if (filters.isEmpty()) emptyList() else listOf("-af", filters.joinToString(","))) + a + out.path
        val n = c.inputs.size
        val tag = if (n > 1) "${tr("تعديل الصوت")} (${i + 1}/$n)" else "تعديل الصوت"
        c.progress(i.toFloat() / n, tag)
        FF.exec(args, FF.durationOf(f.file) / speed) { p -> c.progress((i + p) / n, tag) }
        out
    }

    private fun dims(f: File): Pair<Int, Int> = try {
        val s = FFprobeKit.getMediaInformation(f.path).mediaInformation.streams.first { it.type == "video" }
        var w = s.width.toInt()
        var h = s.height.toInt()
        val rot = s.allProperties?.optJSONArray("side_data_list")?.optJSONObject(0)?.optInt("rotation", 0) ?: 0
        if (Math.abs(rot) % 180 == 90) { val t = w; w = h; h = t }
        w to h
    } catch (_: Throwable) {
        1280 to 720
    }

    suspend fun merge(c: Ctx): List<File> {
        val n = c.inputs.size
        val total = c.inputs.sumOf { FF.durationOf(it.file) }
        val ins = c.inputs.flatMap { listOf("-i", it.file.path) }
        val allAudio = c.inputs.all { it.ext in AUDIO_EXT }
        c.progress(0f, "دمج الملفات")
        val out: File
        val args: List<String>
        if (allAudio) {
            val (a, ext) = audioArgs(c.s("afmt").ifBlank { "m4a" }, "high")
            out = c.out("merged.$ext")
            val fc = (0 until n).joinToString("") { "[$it:a]" } + "concat=n=$n:v=0:a=1[a]"
            args = ins + listOf("-filter_complex", fc, "-map", "[a]") + a + out.path
        } else {
            val (w0, h0) = dims(c.inputs[0].file)
            val k = maxOf(w0, h0).let { if (it > 1280) 1280.0 / it else 1.0 }
            val w = (w0 * k).toInt() / 2 * 2
            val h = (h0 * k).toInt() / 2 * 2
            val sb = StringBuilder()
            for (i in 0 until n) {
                sb.append("[$i:v:0]scale=$w:$h:force_original_aspect_ratio=decrease,pad=$w:$h:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30[v$i];")
                sb.append("[$i:a:0]aresample=44100[a$i];")
            }
            for (i in 0 until n) sb.append("[v$i][a$i]")
            sb.append("concat=n=$n:v=1:a=1[v][a]")
            out = c.out("merged.mp4")
            args = ins + listOf("-filter_complex", sb.toString(), "-map", "[v]", "-map", "[a]") +
                vArgs("light", "x264", "orig") + listOf("-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart") + out.path
        }
        FF.exec(args, total) { p -> c.progress(p, "دمج الملفات") }
        return listOf(out)
    }
}
