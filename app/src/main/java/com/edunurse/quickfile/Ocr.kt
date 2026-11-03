package com.edunurse.quickfile

import android.content.Context
import android.graphics.Bitmap
import android.graphics.Rect
import android.graphics.pdf.PdfRenderer
import android.os.ParcelFileDescriptor
import com.googlecode.tesseract.android.TessBaseAPI
import com.tom_roush.pdfbox.pdmodel.PDDocument
import com.tom_roush.pdfbox.pdmodel.PDPage
import com.tom_roush.pdfbox.pdmodel.PDPageContentStream
import com.tom_roush.pdfbox.pdmodel.common.PDRectangle
import com.tom_roush.pdfbox.pdmodel.font.PDFont
import com.tom_roush.pdfbox.pdmodel.font.PDType0Font
import com.tom_roush.pdfbox.pdmodel.font.PDType1Font
import com.tom_roush.pdfbox.pdmodel.graphics.image.JPEGFactory
import com.tom_roush.pdfbox.pdmodel.graphics.state.RenderingMode
import com.tom_roush.pdfbox.util.Matrix
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import java.io.File
import kotlin.math.max

object Ocr {
    private fun dataRoot(ctx: Context): File {
        val root = File(ctx.filesDir, "ocr")
        val td = File(root, "tessdata").also { it.mkdirs() }
        for (n in listOf("ara.traineddata", "eng.traineddata")) {
            val f = File(td, n)
            if (!f.exists() || f.length() == 0L) {
                try { ctx.assets.open("tessdata/$n").use { i -> f.outputStream().use { o -> i.copyTo(o) } } } catch (_: Exception) { }
            }
        }
        return root
    }

    private fun arabicFont(ctx: Context, doc: PDDocument): PDFont? {
        for (subset in listOf(true, false)) {
            try { return ctx.assets.open("NotoNaskhArabic.ttf").use { PDType0Font.load(doc, it, subset) } } catch (_: Throwable) { }
        }
        return null
    }

    private fun isArabic(w: String) = w.any { it.code in 0x0600..0x06FF || it.code in 0xFB50..0xFEFF }

    private fun word(cs: PDPageContentStream, w: String, r: Rect, k: Float, H: Float, ar: PDFont?) {
        try {
            val font: PDFont = if (isArabic(w)) (ar ?: return) else PDType1Font.HELVETICA
            val fs = max(r.height() * k * 0.85f, 2f)
            val nat = font.getStringWidth(w) / 1000f * fs
            if (nat <= 0f) return
            val sx = (r.width() * k) / nat
            cs.setFont(font, fs)
            cs.setTextMatrix(Matrix(sx, 0f, 0f, 1f, r.left * k, H - r.bottom * k))
            cs.showText(w)
        } catch (_: Exception) { }
    }

    private fun addPage(doc: PDDocument, api: TessBaseAPI, bmp: Bitmap, dpi: Int, ar: PDFont?, text: StringBuilder) {
        api.setImage(bmp)
        text.append(api.getUTF8Text() ?: "").append("\n\n")
        val k = 72f / dpi
        val W = bmp.width * k
        val H = bmp.height * k
        val page = PDPage(PDRectangle(W, H))
        doc.addPage(page)
        val jpg = JPEGFactory.createFromImage(doc, bmp, 0.7f)
        PDPageContentStream(doc, page).use { cs ->
            cs.drawImage(jpg, 0f, 0f, W, H)
            val iter = api.getResultIterator()
            if (iter != null) {
                iter.begin()
                cs.beginText()
                cs.setRenderingMode(RenderingMode.NEITHER)
                do {
                    val w = iter.getUTF8Text(TessBaseAPI.PageIteratorLevel.RIL_WORD)?.trim().orEmpty()
                    if (w.isNotEmpty()) word(cs, w, iter.getBoundingRect(TessBaseAPI.PageIteratorLevel.RIL_WORD), k, H, ar)
                } while (iter.next(TessBaseAPI.PageIteratorLevel.RIL_WORD))
                cs.endText()
            }
        }
    }

    suspend fun run(c: Ctx): List<File> {
        Pdf.ensure(c.app)
        val lang = c.s("lang")
        val dpi = c.s("dpi").toIntOrNull() ?: 200
        val root = dataRoot(c.app)
        for (l in lang.split("+")) {
            if (!File(root, "tessdata/$l.traineddata").exists()) throw IllegalStateException("ملف لغة OCR ($l) مش موجود جوه التطبيق")
        }
        val api = TessBaseAPI()
        val outs = ArrayList<File>()
        try {
            if (!api.init(root.path, lang)) throw IllegalStateException("تعذّر تشغيل محرك OCR")
            api.setPageSegMode(TessBaseAPI.PageSegMode.PSM_AUTO)
            api.setVariable("user_defined_dpi", dpi.toString())
            val total = c.inputs.sumOf { if (it.ext == "pdf") Pdf.pageCount(it.file) else 1 }
            var done = 0
            for (f in c.inputs) {
                val doc = PDDocument()
                val text = StringBuilder()
                try {
                    val ar = arabicFont(c.app, doc)
                    if (ar == null) c.note = "خط العربي مش موجود، فالنص العربي مش هيتضاف لطبقة البحث في الـ PDF (لكن موجود في ملف TXT)."
                    fun step(bmp: Bitmap) {
                        addPage(doc, api, bmp, dpi, ar, text)
                        bmp.recycle()
                        done++
                        c.progress(done.toFloat() / total, "OCR: صفحة $done من $total")
                    }
                    if (f.ext == "pdf") {
                        val pfd = ParcelFileDescriptor.open(f.file, ParcelFileDescriptor.MODE_READ_ONLY)
                        val r = PdfRenderer(pfd)
                        try {
                            for (i in 0 until r.pageCount) {
                                currentCoroutineContext().ensureActive()
                                step(Pdf.render(r, i, dpi))
                            }
                        } finally { r.close(); pfd.close() }
                    } else {
                        currentCoroutineContext().ensureActive()
                        step(Img.flatten(Img.decode(f.file, 3500)))
                    }
                    val o = c.out("${f.base}_ocr.pdf")
                    doc.save(o)
                    outs.add(o)
                    if (c.on("txt")) {
                        val t = c.out("${f.base}_ocr.txt")
                        t.writeText(text.toString())
                        outs.add(t)
                    }
                } finally { doc.close() }
            }
        } finally { api.recycle() }
        return outs
    }
}
