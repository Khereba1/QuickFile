package com.edunurse.quickfile

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Matrix
import android.graphics.pdf.PdfRenderer
import android.os.Build
import android.os.ParcelFileDescriptor
import androidx.exifinterface.media.ExifInterface
import com.tom_roush.pdfbox.android.PDFBoxResourceLoader
import com.tom_roush.pdfbox.io.MemoryUsageSetting
import com.tom_roush.pdfbox.multipdf.PDFMergerUtility
import com.tom_roush.pdfbox.multipdf.Splitter
import com.tom_roush.pdfbox.pdmodel.PDDocument
import com.tom_roush.pdfbox.pdmodel.PDPage
import com.tom_roush.pdfbox.pdmodel.PDPageContentStream
import com.tom_roush.pdfbox.pdmodel.common.PDRectangle
import com.tom_roush.pdfbox.pdmodel.graphics.image.JPEGFactory
import com.tom_roush.pdfbox.pdmodel.graphics.image.PDImageXObject
import java.io.File
import java.io.FileOutputStream
import kotlin.math.max

object Img {
    fun decode(f: File, maxSide: Int): Bitmap {
        val o = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeFile(f.path, o)
        var s = 1
        while (max(o.outWidth, o.outHeight) / s > maxSide * 2) s *= 2
        var b = BitmapFactory.decodeFile(f.path, BitmapFactory.Options().apply { inSampleSize = s })
            ?: throw IllegalStateException("تعذّر قراءة الصورة: ${f.name}")
        val rot = try {
            when (ExifInterface(f.path).getAttributeInt(ExifInterface.TAG_ORIENTATION, 1)) {
                6 -> 90f; 3 -> 180f; 8 -> 270f; else -> 0f
            }
        } catch (_: Exception) { 0f }
        val m = Matrix()
        if (rot != 0f) m.postRotate(rot)
        val big = max(b.width, b.height)
        if (maxSide > 0 && big > maxSide) { val k = maxSide.toFloat() / big; m.postScale(k, k) }
        if (!m.isIdentity) {
            val nb = Bitmap.createBitmap(b, 0, 0, b.width, b.height, m, true)
            if (nb !== b) b.recycle()
            b = nb
        }
        return b
    }

    fun flatten(b: Bitmap): Bitmap {
        if (!b.hasAlpha()) return b
        val nb = Bitmap.createBitmap(b.width, b.height, Bitmap.Config.ARGB_8888)
        val cv = Canvas(nb)
        cv.drawColor(Color.WHITE)
        cv.drawBitmap(b, 0f, 0f, null)
        b.recycle()
        return nb
    }
}

object Pdf {
    private var ready = false

    fun ensure(ctx: Context) {
        if (!ready) { PDFBoxResourceLoader.init(ctx.applicationContext); ready = true }
    }

    /** "1-3,7,10-" -> sorted 1-based pages */
    fun ranges(s: String, total: Int): List<Int> {
        if (s.isBlank()) return (1..total).toList()
        val set = sortedSetOf<Int>()
        for (part in s.split(",", "،")) {
            val p = part.trim()
            if (p.isEmpty()) continue
            if ("-" in p) {
                val a = p.substringBefore("-").trim().toIntOrNull() ?: 1
                val b = p.substringAfter("-").trim().toIntOrNull() ?: total
                for (i in a..b) if (i in 1..total) set.add(i)
            } else p.toIntOrNull()?.let { if (it in 1..total) set.add(it) }
        }
        if (set.isEmpty()) throw IllegalStateException("أرقام الصفحات مش صحيحة (الملف فيه $total صفحة)")
        return set.toList()
    }

    fun render(r: PdfRenderer, i: Int, dpi: Int): Bitmap {
        val p = r.openPage(i)
        try {
            val k = dpi / 72f
            val bmp = Bitmap.createBitmap((p.width * k).toInt(), (p.height * k).toInt(), Bitmap.Config.ARGB_8888)
            bmp.eraseColor(Color.WHITE)
            p.render(bmp, null, null, PdfRenderer.Page.RENDER_MODE_FOR_DISPLAY)
            return bmp
        } finally { p.close() }
    }

    fun pageCount(f: File): Int {
        val pfd = ParcelFileDescriptor.open(f, ParcelFileDescriptor.MODE_READ_ONLY)
        val r = PdfRenderer(pfd)
        try { return r.pageCount } finally { r.close(); pfd.close() }
    }

    fun merge(c: Ctx): List<File> {
        ensure(c.app)
        c.progress(0.2f, "دمج الملفات")
        val out = c.out("merged.pdf")
        val m = PDFMergerUtility()
        c.inputs.forEach { m.addSource(it.file) }
        m.destinationFileName = out.path
        m.mergeDocuments(MemoryUsageSetting.setupTempFileOnly())
        return listOf(out)
    }

    fun split(c: Ctx): List<File> {
        ensure(c.app)
        val f = c.inputs[0]
        val doc = PDDocument.load(f.file)
        try {
            val total = doc.numberOfPages
            if (c.s("mode") == "every") {
                val n = c.s("val").trim().toIntOrNull()?.takeIf { it > 0 } ?: throw IllegalStateException("اكتب عدد صحيح للصفحات")
                val sp = Splitter()
                sp.setSplitAtPage(n)
                val parts = sp.split(doc)
                return parts.mapIndexed { i, p ->
                    c.progress(i.toFloat() / parts.size, "حفظ الجزء ${i + 1} من ${parts.size}")
                    val o = c.out("${f.base}_part${i + 1}.pdf")
                    p.save(o); p.close(); o
                }
            }
            val keep = ranges(c.s("val"), total).toSet()
            for (i in total - 1 downTo 0) if ((i + 1) !in keep) doc.removePage(i)
            val o = c.out("${f.base}_pages.pdf")
            doc.save(o)
            return listOf(o)
        } finally { doc.close() }
    }

    fun unlock(c: Ctx): List<File> = c.inputs.mapIndexed { i, f ->
        ensure(c.app)
        c.progress(i.toFloat() / c.inputs.size, "فك الحماية ${f.name}")
        val doc = try {
            PDDocument.load(f.file, c.s("pw"))
        } catch (e: Exception) {
            throw IllegalStateException("الباسورد غلط أو الملف محتاج باسورد")
        }
        try {
            doc.isAllSecurityToBeRemoved = true
            val o = c.out("${f.base}_unlocked.pdf")
            doc.save(o)
            o
        } finally { doc.close() }
    }

    fun compress(c: Ctx): List<File> = c.inputs.mapIndexed { fi, f ->
        ensure(c.app)
        val (maxSide, q) = when (c.s("level")) { "light" -> 2200 to 80; "strong" -> 1100 to 45; else -> 1600 to 62 }
        val doc = PDDocument.load(f.file)
        try {
            val done = HashSet<Any>()
            val pages = doc.numberOfPages
            var pi = 0
            for (page in doc.pages) {
                c.progress((fi + pi.toFloat() / pages) / c.inputs.size, "ضغط ${f.name} (${pi + 1}/$pages)")
                pi++
                val res = page.resources ?: continue
                for (name in res.xObjectNames.toList()) {
                    try {
                        val x = res.getXObject(name)
                        if (x !is PDImageXObject || x.isStencil || x.softMask != null) continue
                        if (!done.add(x.cosObject)) continue
                        val bmp = x.image ?: continue
                        val m = max(bmp.width, bmp.height)
                        val sc = if (m > maxSide) Bitmap.createScaledBitmap(bmp, bmp.width * maxSide / m, bmp.height * maxSide / m, true) else bmp
                        val ni = JPEGFactory.createFromImage(doc, sc, q / 100f)
                        res.put(name, ni)
                        done.add(ni.cosObject)
                    } catch (_: Throwable) { }
                }
            }
            val o = c.out("${f.base}_compressed.pdf")
            doc.save(o)
            if (o.length() >= f.file.length()) {
                f.file.copyTo(o, overwrite = true)
                c.note = "الملف كان مضغوط بالفعل، فرجّعت النسخة الأصلية لأن الضغط مكبّرش فرق."
            }
            o
        } finally { doc.close() }
    }

    fun toImages(c: Ctx): List<File> {
        val f = c.inputs[0]
        val dpi = c.s("dpi").toIntOrNull() ?: 150
        val png = c.s("fmt") == "png"
        val pfd = ParcelFileDescriptor.open(f.file, ParcelFileDescriptor.MODE_READ_ONLY)
        val r = PdfRenderer(pfd)
        try {
            val pages = ranges(c.s("pages"), r.pageCount)
            return pages.mapIndexed { k, pg ->
                c.progress(k.toFloat() / pages.size, "صفحة $pg")
                val bmp = render(r, pg - 1, dpi)
                val o = c.out("${f.base}_p${pg}.${if (png) "png" else "jpg"}")
                FileOutputStream(o).use { bmp.compress(if (png) Bitmap.CompressFormat.PNG else Bitmap.CompressFormat.JPEG, 90, it) }
                bmp.recycle()
                o
            }
        } finally { r.close(); pfd.close() }
    }

    fun imagesToPdf(c: Ctx): List<File> {
        ensure(c.app)
        val a4 = c.s("page") == "a4"
        val q = (c.s("q").toIntOrNull() ?: 70) / 100f
        val doc = PDDocument()
        try {
            c.inputs.forEachIndexed { i, f ->
                c.progress(i.toFloat() / c.inputs.size, "صورة ${i + 1} من ${c.inputs.size}")
                val bmp = Img.flatten(Img.decode(f.file, 2600))
                val pw = 595f
                val W = pw
                val H = if (a4) 842f else pw * bmp.height / bmp.width
                val page = PDPage(PDRectangle(W, H))
                doc.addPage(page)
                val img = JPEGFactory.createFromImage(doc, bmp, q)
                var dw = W
                var dh = bmp.height * W / bmp.width
                if (dh > H) { dh = H; dw = bmp.width * H / bmp.height }
                PDPageContentStream(doc, page).use { it.drawImage(img, (W - dw) / 2, (H - dh) / 2, dw, dh) }
                bmp.recycle()
            }
            val o = c.out("images.pdf")
            doc.save(o)
            return listOf(o)
        } finally { doc.close() }
    }

    @Suppress("DEPRECATION")
    fun convertImages(c: Ctx): List<File> = c.inputs.mapIndexed { i, f ->
        c.progress(i.toFloat() / c.inputs.size, "صورة ${i + 1} من ${c.inputs.size}")
        val max = c.s("max").toIntOrNull() ?: 0
        val q = c.s("q").toIntOrNull() ?: 75
        val fmt = c.s("fmt")
        var bmp = Img.decode(f.file, if (max > 0) max else 12000)
        if (fmt == "jpg") bmp = Img.flatten(bmp)
        val cf = when (fmt) {
            "png" -> Bitmap.CompressFormat.PNG
            "webp" -> if (Build.VERSION.SDK_INT >= 30) Bitmap.CompressFormat.WEBP_LOSSY else Bitmap.CompressFormat.WEBP
            else -> Bitmap.CompressFormat.JPEG
        }
        val o = c.out("${f.base}.$fmt")
        FileOutputStream(o).use { bmp.compress(cf, q, it) }
        bmp.recycle()
        o
    }
}
