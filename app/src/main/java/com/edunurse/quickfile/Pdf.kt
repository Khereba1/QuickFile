package com.edunurse.quickfile

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Matrix
import android.graphics.Paint
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
import com.tom_roush.pdfbox.pdmodel.encryption.AccessPermission
import com.tom_roush.pdfbox.pdmodel.encryption.StandardProtectionPolicy
import com.tom_roush.pdfbox.pdmodel.font.PDType1Font
import com.tom_roush.pdfbox.pdmodel.graphics.image.JPEGFactory
import com.tom_roush.pdfbox.pdmodel.graphics.image.LosslessFactory
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

    fun split(c: Ctx, forced: String? = null): List<File> {
        ensure(c.app)
        val f = c.inputs[0]
        val mode = forced ?: c.s("mode")
        val spec = c.s("val")
        val doc = PDDocument.load(f.file)
        return try {
            val total = doc.numberOfPages
            when (mode) {
                "every" -> {
                    val n = spec.trim().toIntOrNull()?.takeIf { it > 0 } ?: throw IllegalStateException("اكتب عدد صحيح للصفحات")
                    val sp = Splitter()
                    sp.setSplitAtPage(n)
                    val parts = sp.split(doc)
                    parts.mapIndexed { i, p ->
                        c.progress(i.toFloat() / parts.size, "حفظ الجزء ${i + 1} من ${parts.size}")
                        val o = c.out("${f.base}_part${i + 1}.pdf")
                        p.save(o); p.close(); o
                    }
                }
                "ranges" -> {
                    val groups = spec.split(",", "،").map { it.trim() }.filter { it.isNotEmpty() }
                    if (groups.isEmpty()) throw IllegalStateException("اكتب المجموعات، مثال: 1-3,4-6,7")
                    groups.mapIndexed { gi, g ->
                        c.progress(gi.toFloat() / groups.size, "جزء ${gi + 1} من ${groups.size}")
                        val keep = ranges(g, total).toSet()
                        val d = PDDocument.load(f.file)
                        try {
                            for (i in total - 1 downTo 0) if ((i + 1) !in keep) d.removePage(i)
                            val o = c.out("${f.base}_${g.replace(Regex("[^0-9-]"), "")}.pdf")
                            d.save(o)
                            o
                        } finally { d.close() }
                    }
                }
                else -> {
                    if (mode == "delete" && spec.isBlank()) throw IllegalStateException("اكتب أرقام الصفحات اللي عايز تحذفها")
                    val sel = ranges(spec, total).toSet()
                    val keep = if (mode == "delete") (1..total).filter { it !in sel }.toSet() else sel
                    if (keep.isEmpty()) throw IllegalStateException("مينفعش تحذف كل الصفحات")
                    for (i in total - 1 downTo 0) if ((i + 1) !in keep) doc.removePage(i)
                    val o = c.out("${f.base}_${if (mode == "delete") "edited" else "pages"}.pdf")
                    doc.save(o)
                    listOf(o)
                }
            }
        } finally { doc.close() }
    }

    fun rotate(c: Ctx): List<File> = c.inputs.mapIndexed { fi, f ->
        ensure(c.app)
        c.progress(fi.toFloat() / c.inputs.size, "تدوير ${f.name}")
        val angle = c.s("angle").toIntOrNull() ?: 90
        val doc = PDDocument.load(f.file)
        try {
            for (pg in ranges(c.s("pages"), doc.numberOfPages)) {
                val page = doc.getPage(pg - 1)
                page.rotation = ((page.rotation + angle) % 360 + 360) % 360
            }
            val o = c.out("${f.base}_rotated.pdf")
            doc.save(o)
            o
        } finally { doc.close() }
    }

    fun numbers(c: Ctx): List<File> = c.inputs.mapIndexed { fi, f ->
        ensure(c.app)
        val start = c.s("start").trim().toIntOrNull() ?: 1
        val pos = c.s("pos")
        val withTotal = c.on("total")
        val font = PDType1Font.HELVETICA
        val doc = PDDocument.load(f.file)
        try {
            val total = doc.numberOfPages
            for ((n, page) in doc.pages.withIndex()) {
                c.progress((fi + n.toFloat() / total) / c.inputs.size, "ترقيم ${f.name}")
                val label = if (withTotal) "${n + start} / ${total + start - 1}" else "${n + start}"
                val box = page.mediaBox
                val w = font.getStringWidth(label) / 1000f * 10f
                val x = when (pos) {
                    "l" -> box.lowerLeftX + 28f
                    "r" -> box.upperRightX - 28f - w
                    else -> (box.lowerLeftX + box.upperRightX) / 2f - w / 2f
                }
                PDPageContentStream(doc, page, PDPageContentStream.AppendMode.APPEND, true, true).use { cs ->
                    cs.beginText()
                    cs.setFont(font, 10f)
                    cs.newLineAtOffset(x, box.lowerLeftY + 20f)
                    cs.showText(label)
                    cs.endText()
                }
            }
            val o = c.out("${f.base}_numbered.pdf")
            doc.save(o)
            o
        } finally { doc.close() }
    }

    fun watermark(c: Ctx): List<File> = c.inputs.mapIndexed { fi, f ->
        ensure(c.app)
        val text = c.s("text").trim()
        if (text.isEmpty()) throw IllegalStateException("اكتب نص العلامة المائية")
        val alpha = when (c.s("op")) { "light" -> 55; "strong" -> 150; else -> 95 }
        val doc = PDDocument.load(f.file)
        try {
            val cache = HashMap<String, com.tom_roush.pdfbox.pdmodel.graphics.image.PDImageXObject>()
            val total = doc.numberOfPages
            for ((n, page) in doc.pages.withIndex()) {
                c.progress((fi + n.toFloat() / total) / c.inputs.size, "علامة مائية ${f.name}")
                val box = page.mediaBox
                val w = box.width
                val h = box.height
                val img = cache.getOrPut("${w.toInt()}x${h.toInt()}") {
                    val bw = 900
                    val bh = (bw * h / w).toInt().coerceAtLeast(100)
                    val bmp = Bitmap.createBitmap(bw, bh, Bitmap.Config.ARGB_8888)
                    val paint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
                        color = Color.argb(alpha, 110, 110, 110)
                        textAlign = Paint.Align.CENTER
                        textSize = 100f
                    }
                    val diag = Math.hypot(bw.toDouble(), bh.toDouble()).toFloat()
                    val tw = paint.measureText(text).coerceAtLeast(1f)
                    paint.textSize = (100f * 0.78f * diag / tw).coerceAtMost(bw / 5f)
                    val cv = Canvas(bmp)
                    cv.rotate(-45f, bw / 2f, bh / 2f)
                    cv.drawText(text, bw / 2f, bh / 2f + paint.textSize / 3f, paint)
                    LosslessFactory.createFromImage(doc, bmp)
                }
                PDPageContentStream(doc, page, PDPageContentStream.AppendMode.APPEND, true, true).use {
                    it.drawImage(img, box.lowerLeftX, box.lowerLeftY, w, h)
                }
            }
            val o = c.out("${f.base}_watermark.pdf")
            doc.save(o)
            o
        } finally { doc.close() }
    }

    fun protect(c: Ctx): List<File> = c.inputs.mapIndexed { fi, f ->
        ensure(c.app)
        val pw = c.s("pw")
        if (pw.isBlank()) throw IllegalStateException("اكتب الباسورد")
        c.progress(fi.toFloat() / c.inputs.size, "حماية ${f.name}")
        val doc = PDDocument.load(f.file)
        try {
            val ap = AccessPermission()
            ap.setCanModify(false)
            if (c.on("nocopy")) ap.setCanExtractContent(false)
            val policy = StandardProtectionPolicy(pw, pw, ap)
            policy.setEncryptionKeyLength(128)
            doc.protect(policy)
            val o = c.out("${f.base}_protected.pdf")
            doc.save(o)
            o
        } finally { doc.close() }
    }

    fun organize(c: Ctx, items: List<OPage>): List<File> {
        ensure(c.app)
        if (items.isEmpty()) throw IllegalStateException("مفيش صفحات للحفظ")
        val out = PDDocument()
        val srcs = HashMap<String, PDDocument>()
        try {
            items.forEachIndexed { n, it ->
                c.progress(n.toFloat() / items.size, "صفحة ${n + 1} من ${items.size}")
                val rot = ((it.rot % 360) + 360) % 360
                if (it.isImg) {
                    val bmp = Img.flatten(Img.decode(it.src, 2600))
                    val W = 595f
                    val H = W * bmp.height / bmp.width
                    val page = PDPage(PDRectangle(W, H))
                    out.addPage(page)
                    val img = JPEGFactory.createFromImage(out, bmp, 0.8f)
                    PDPageContentStream(out, page).use { cs -> cs.drawImage(img, 0f, 0f, W, H) }
                    page.rotation = rot
                    bmp.recycle()
                } else {
                    val sd = srcs.getOrPut(it.src.path) { PDDocument.load(it.src) }
                    val pg = out.importPage(sd.getPage(it.idx))
                    pg.rotation = ((pg.rotation + rot) % 360 + 360) % 360
                }
            }
            val o = c.out("organized.pdf")
            out.save(o)
            return listOf(o)
        } finally {
            out.close()
            srcs.values.forEach { d -> d.close() }
        }
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
