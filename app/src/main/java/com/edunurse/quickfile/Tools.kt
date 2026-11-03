package com.edunurse.quickfile

import android.content.Context
import java.io.File

enum class Group(val title: String, val color: Long) {
    VIDEO("فيديو", 0xFFFFA177),
    AUDIO("صوت", 0xFFB7A4F5),
    PDF("PDF", 0xFFFF7B8A),
    IMAGE("صور", 0xFF6FD3A6),
    OCR("استخراج النص", 0xFF6CB6FF)
}

sealed class Opt(val key: String, val label: String) {
    class Choice(key: String, label: String, val choices: List<Pair<String, String>>, val def: String) : Opt(key, label)
    class Text(key: String, label: String, val hint: String, val def: String = "", val numeric: Boolean = false) : Opt(key, label)
    class Toggle(key: String, label: String, val def: Boolean) : Opt(key, label)
}

class In(val name: String, val file: File) {
    val base: String get() = name.substringBeforeLast('.')
    val ext: String get() = name.substringAfterLast('.', "").lowercase()
}

class Ctx(
    val app: Context,
    val inputs: List<In>,
    val o: Map<String, String>,
    val outDir: File,
    val progress: (Float, String) -> Unit
) {
    var note: String = ""
    fun s(key: String): String = o[key].orEmpty()
    fun on(key: String): Boolean = o[key] == "1"
    fun out(name: String): File {
        var f = File(outDir, name)
        var k = 1
        while (f.exists()) {
            f = File(outDir, name.substringBeforeLast('.') + "_" + k++ + "." + name.substringAfterLast('.'))
        }
        return f
    }
}

class Tool(
    val id: String,
    val title: String,
    val sub: String,
    val emoji: String,
    val group: Group,
    val mimes: Array<String>,
    val multi: Boolean,
    val minFiles: Int = 1,
    val ordered: Boolean = false,
    val opts: List<Opt> = emptyList(),
    val exec: suspend (Ctx) -> List<File>
)

private val VID = arrayOf("video/*")
private val AUD = arrayOf("audio/*")
private val MEDIA = arrayOf("video/*", "audio/*")
private val PDFM = arrayOf("application/pdf")
private val IMG = arrayOf("image/*")
private val PDF_IMG = arrayOf("application/pdf", "image/*")

private val AFMT = listOf("mp3" to "MP3", "m4a" to "M4A", "opus" to "Opus", "flac" to "FLAC", "wav" to "WAV")
private val AQ = listOf("high" to "عالية", "mid" to "متوسطة", "low" to "صغيرة")

val TOOLS: List<Tool> = listOf(
    Tool("vcompress", "ضغط الفيديو", "قلّل الحجم بأقل خسارة في الجودة", "🗜️", Group.VIDEO, VID, true, opts = listOf(
        Opt.Choice("level", "مستوى الضغط", listOf("light" to "خفيف", "mid" to "متوسط", "strong" to "قوي"), "mid"),
        Opt.Choice("res", "الدقة", listOf("orig" to "الأصلية", "720" to "720p", "480" to "480p"), "orig"),
        Opt.Choice("engine", "طريقة المعالجة", listOf("x264" to "جودة أعلى", "hw" to "أسرع (هاردوير)"), "x264")
    ), exec = { Media.vcompress(it) }),

    Tool("vconvert", "تحويل صيغة الفيديو", "MP4 / MKV / WebM", "🎞️", Group.VIDEO, VID, true, opts = listOf(
        Opt.Choice("to", "التحويل إلى", listOf("mp4" to "MP4", "mkv" to "MKV (سريع)", "webm" to "WebM"), "mp4")
    ), exec = { Media.vconvert(it) }),

    Tool("vaudio", "استخراج الصوت", "طلّع الصوت من أي فيديو", "🎧", Group.VIDEO, VID, true, opts = listOf(
        Opt.Choice("fmt", "الصيغة", AFMT, "mp3"),
        Opt.Choice("q", "الجودة", AQ, "mid")
    ), exec = { Media.toAudio(it) }),

    Tool("cut", "قص مقطع", "فيديو أو صوت، من وقت لوقت", "✂️", Group.VIDEO, MEDIA, false, opts = listOf(
        Opt.Text("start", "البداية", "00:00:10 أو 1:30", "0"),
        Opt.Text("end", "النهاية (اتركها فاضية للآخر)", "00:01:30"),
        Opt.Toggle("precise", "قص دقيق (أبطأ، بيعيد الترميز)", false)
    ), exec = { Media.cut(it) }),

    Tool("merge", "دمج ملفات", "ادمج فيديوهات أو ملفات صوت", "🔗", Group.VIDEO, MEDIA, true, minFiles = 2, ordered = true, opts = listOf(
        Opt.Choice("afmt", "صيغة الصوت (للدمج الصوتي فقط)", listOf("m4a" to "M4A", "mp3" to "MP3"), "m4a")
    ), exec = { Media.merge(it) }),

    Tool("aconvert", "تحويل وضغط الصوت", "MP3 / M4A / Opus / FLAC / WAV", "🎵", Group.AUDIO, AUD, true, opts = listOf(
        Opt.Choice("fmt", "الصيغة", AFMT + ("voice" to "رسالة صوتية"), "mp3"),
        Opt.Choice("q", "الجودة", AQ, "mid")
    ), exec = { Media.toAudio(it) }),

    Tool("pmerge", "دمج PDF", "اجمع أكتر من ملف في ملف واحد", "📚", Group.PDF, PDFM, true, minFiles = 2, ordered = true,
        exec = { Pdf.merge(it) }),

    Tool("psplit", "تقسيم واستخراج صفحات", "خد صفحات معينة أو قسّم الملف", "✂️", Group.PDF, PDFM, false, opts = listOf(
        Opt.Choice("mode", "الطريقة", listOf("extract" to "استخراج صفحات", "every" to "تقسيم كل N صفحة"), "extract"),
        Opt.Text("val", "الصفحات أو N", "مثال: 1-3,7,10-12  أو  5", "1")
    ), exec = { Pdf.split(it) }),

    Tool("punlock", "فك حماية PDF", "شيل الباسورد أو قيود التعديل", "🔓", Group.PDF, PDFM, true, opts = listOf(
        Opt.Text("pw", "الباسورد (لو موجود)", "اتركه فاضي لو مفيش")
    ), exec = { Pdf.unlock(it) }),

    Tool("pcompress", "ضغط PDF", "صغّر حجم الملفات الممسوحة", "🗜️", Group.PDF, PDFM, true, opts = listOf(
        Opt.Choice("level", "مستوى الضغط", listOf("light" to "خفيف", "mid" to "متوسط", "strong" to "قوي"), "mid")
    ), exec = { Pdf.compress(it) }),

    Tool("pimages", "PDF إلى صور", "كل صفحة صورة", "🖼️", Group.PDF, PDFM, false, opts = listOf(
        Opt.Choice("dpi", "الدقة", listOf("100" to "100", "150" to "150", "220" to "220"), "150"),
        Opt.Choice("fmt", "الصيغة", listOf("jpg" to "JPG", "png" to "PNG"), "jpg"),
        Opt.Text("pages", "الصفحات (فاضي = الكل)", "مثال: 1-5")
    ), exec = { Pdf.toImages(it) }),

    Tool("imgpdf", "صور إلى PDF", "رتّب الصور وحوّلها لملف واحد", "📄", Group.IMAGE, IMG, true, ordered = true, opts = listOf(
        Opt.Choice("page", "حجم الصفحة", listOf("fit" to "على قد الصورة", "a4" to "A4"), "fit"),
        Opt.Choice("q", "الجودة", listOf("85" to "عالية", "70" to "متوسطة", "50" to "صغيرة"), "70")
    ), exec = { Pdf.imagesToPdf(it) }),

    Tool("imgconv", "تحويل وضغط الصور", "JPG / PNG / WebP وتصغير الأبعاد", "🏞️", Group.IMAGE, IMG, true, opts = listOf(
        Opt.Choice("fmt", "الصيغة", listOf("jpg" to "JPG", "png" to "PNG", "webp" to "WebP"), "jpg"),
        Opt.Choice("q", "الجودة", listOf("90" to "عالية", "75" to "متوسطة", "55" to "صغيرة"), "75"),
        Opt.Choice("max", "أكبر بُعد", listOf("0" to "الأصلي", "2560" to "2560", "1600" to "1600", "1024" to "1024"), "0")
    ), exec = { Pdf.convertImages(it) }),

    Tool("ocr", "OCR: PDF قابل للبحث", "حوّل الممسوح لنص عربي/إنجليزي ويطلع PDF وTXT", "🔍", Group.OCR, PDF_IMG, true, opts = listOf(
        Opt.Choice("lang", "اللغة", listOf("ara+eng" to "عربي + إنجليزي", "eng" to "إنجليزي", "ara" to "عربي"), "ara+eng"),
        Opt.Choice("dpi", "دقة المسح", listOf("200" to "200 (أسرع)", "300" to "300 (أدق)"), "200"),
        Opt.Toggle("txt", "احفظ ملف نصي TXT كمان", true)
    ), exec = { Ocr.run(it) })
)
