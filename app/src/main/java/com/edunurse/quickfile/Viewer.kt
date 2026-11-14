package com.edunurse.quickfile

import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.Color as AColor
import android.graphics.Matrix
import android.graphics.Rect
import android.graphics.pdf.PdfRenderer
import android.net.Uri
import android.os.ParcelFileDescriptor
import android.os.SystemClock
import android.util.LruCache
import android.widget.Toast
import androidx.compose.animation.core.AnimationState
import androidx.compose.animation.core.animateDecay
import androidx.compose.animation.core.exponentialDecay
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.gestures.calculateCentroid
import androidx.compose.foundation.gestures.calculatePan
import androidx.compose.foundation.gestures.calculateZoom
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clipToBounds
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.ColorFilter
import androidx.compose.ui.graphics.ColorMatrix
import androidx.compose.ui.graphics.FilterQuality
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.input.pointer.changedToUpIgnoreConsumed
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.input.pointer.positionChanged
import androidx.compose.ui.input.pointer.util.VelocityTracker
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.tom_roush.pdfbox.pdmodel.PDDocument
import com.tom_roush.pdfbox.text.PDFTextStripper
import com.tom_roush.pdfbox.text.TextPosition
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.asCoroutineDispatcher
import kotlinx.coroutines.delay
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.Executors
import kotlin.math.abs
import kotlin.math.ceil
import kotlin.math.floor
import kotlin.math.max
import kotlin.math.min
import kotlin.math.roundToInt

private const val GAP = 10f      // الفراغ بين الصفحات (نقاط)
private const val TILE = 512     // حجم القطعة بالبكسل
private const val MAXZ = 6f      // أقصى تكبير

/** هندسة المستند: مقاسات الصفحات بالنقاط (1/72 بوصة) ومواضعها الرأسية */
class Geo(val w: FloatArray, val h: FloatArray) {
    val n = w.size
    val maxW = (w.maxOrNull() ?: 1f).coerceAtLeast(1f)
    val tops = FloatArray(n + 1).also { t ->
        var y = 0f
        for (i in 0 until n) { t[i] = y; y += h[i] + GAP }
        t[n] = y
    }
    val total = tops[n] - GAP
    fun pageAt(yPt: Float): Int {
        var lo = 0
        var hi = n - 1
        while (lo < hi) {
            val mid = (lo + hi + 1) / 2
            if (tops[mid] <= yPt) lo = mid else hi = mid - 1
        }
        return lo
    }
}

class PdfDoc(val file: File) {
    private val pfd = ParcelFileDescriptor.open(file, ParcelFileDescriptor.MODE_READ_ONLY)
    private val r = PdfRenderer(pfd)
    val count: Int = r.pageCount
    @Volatile private var closed = false
    private val cache = object : LruCache<String, Bitmap>(48 * 1024 * 1024) {
        override fun sizeOf(key: String, value: Bitmap): Int = value.byteCount
    }

    /** رسم الصفحة كلها بعرض معيّن (بيستخدمه منظّم الصفحات) */
    fun render(i: Int, width: Int): Bitmap {
        val key = "$i@$width"
        cache.get(key)?.let { return it }
        val b = synchronized(this) {
            check(!closed)
            val p = r.openPage(i)
            try {
                val h = (width.toFloat() * p.height / p.width).toInt().coerceAtLeast(1)
                val bmp = Bitmap.createBitmap(width, h, Bitmap.Config.ARGB_8888)
                bmp.eraseColor(AColor.WHITE)
                p.render(bmp, null, null, PdfRenderer.Page.RENDER_MODE_FOR_DISPLAY)
                bmp
            } finally { p.close() }
        }
        cache.put(key, b)
        return b
    }

    /** رسم قطعة بالدقة الحقيقية: scale = بكسل لكل نقطة، (tx,ty) = موضع القطعة بالبكسل */
    fun renderTile(i: Int, scale: Float, tx: Int, ty: Int, tw: Int, th: Int): Bitmap = synchronized(this) {
        check(!closed)
        val p = r.openPage(i)
        try {
            val bmp = Bitmap.createBitmap(tw, th, Bitmap.Config.ARGB_8888)
            bmp.eraseColor(AColor.WHITE)
            val m = Matrix()
            m.postScale(scale, scale)
            m.postTranslate(-tx.toFloat(), -ty.toFloat())
            p.render(bmp, Rect(0, 0, tw, th), m, PdfRenderer.Page.RENDER_MODE_FOR_DISPLAY)
            bmp
        } finally { p.close() }
    }

    fun sizes(): Geo = synchronized(this) {
        val w = FloatArray(count)
        val h = FloatArray(count)
        for (i in 0 until count) {
            val p = r.openPage(i)
            w[i] = p.width.toFloat()
            h[i] = p.height.toFloat()
            p.close()
        }
        Geo(w, h)
    }

    fun aspect(): Float = synchronized(this) {
        val p = r.openPage(0)
        try { p.height.toFloat() / p.width } finally { p.close() }
    }

    fun close() {
        synchronized(this) {
            if (closed) return
            closed = true
            try { r.close(); pfd.close() } catch (_: Exception) { }
        }
    }
}

/** كلمة من نص الـ PDF مع مكانها في الصفحة (نقاط، الأصل أعلى اليسار) */
data class Word(val text: String, val l: Float, val t: Float, val r: Float, val b: Float, val line: Int = 0)

private class WordCollector : PDFTextStripper() {
    val out = ArrayList<Word>()
    override fun writeString(text: String, textPositions: MutableList<TextPosition>) {
        val t = text.trim()
        if (t.isEmpty() || textPositions.isEmpty()) return
        var l = Float.MAX_VALUE
        var tp = Float.MAX_VALUE
        var r = -Float.MAX_VALUE
        var b = -Float.MAX_VALUE
        for (p in textPositions) {
            val x = p.xDirAdj
            val y = p.yDirAdj
            val h = max(p.heightDir, 1f)
            l = min(l, x)
            r = max(r, x + p.widthDirAdj)
            tp = min(tp, y - h)
            b = max(b, y + h * 0.22f)
        }
        out.add(Word(t, l, tp, r, b))
    }
}

private fun isArabicWord(s: String) = s.any { it in '\u0600'..'\u06FF' || it in '\uFB50'..'\uFEFF' }

object PdfText {
    private val docs = ConcurrentHashMap<String, PDDocument>()
    private val cache = ConcurrentHashMap<String, List<Word>>()

    fun words(ctx: Context, f: File, i: Int): List<Word> {
        Pdf.ensure(ctx)
        val key = "${f.path}#$i"
        cache[key]?.let { return it }
        val d = docs.getOrPut(f.path) { PDDocument.load(f) }
        val raw = synchronized(d) {
            val st = WordCollector()
            st.sortByPosition = true
            st.startPage = i + 1
            st.endPage = i + 1
            st.getText(d)
            st.out
        }
        val res = order(raw)
        cache[key] = res
        return res
    }

    /** ترتيب القراءة: سطور من فوق لتحت، وجوه السطر يمين→شمال لو السطر عربي */
    private fun order(raw: List<Word>): List<Word> {
        if (raw.isEmpty()) return raw
        val sorted = raw.sortedBy { (it.t + it.b) / 2f }
        val lines = ArrayList<MutableList<Word>>()
        for (w in sorted) {
            val cy = (w.t + w.b) / 2f
            val ln = lines.lastOrNull()
            if (ln != null) {
                val ref = ln[0]
                val rh = max(ref.b - ref.t, 1f)
                if (abs(cy - (ref.t + ref.b) / 2f) < 0.6f * rh) { ln.add(w); continue }
            }
            lines.add(mutableListOf(w))
        }
        val out = ArrayList<Word>()
        lines.forEachIndexed { li, ln ->
            val ar = ln.count { isArabicWord(it.text) } * 2 > ln.size
            val s = if (ar) ln.sortedByDescending { it.l } else ln.sortedBy { it.l }
            s.forEach { out.add(it.copy(line = li)) }
        }
        return out
    }

    fun close(f: File) {
        docs.remove(f.path)?.let { try { it.close() } catch (_: Exception) { } }
        cache.keys.removeAll { it.startsWith(f.path + "#") }
    }
}

private fun joinWords(ws: List<Word>, a: Int, b: Int): String {
    val sb = StringBuilder()
    var prev = -1
    for (k in a..b) {
        val w = ws.getOrNull(k) ?: continue
        if (prev >= 0) sb.append(if (w.line != prev) "\n" else " ")
        sb.append(w.text)
        prev = w.line
    }
    return sb.toString()
}

/** توحيد الحروف العربية للبحث: شيل التشكيل وطابق الهمزات والياء */
private fun normAr(s: String): String {
    val sb = StringBuilder(s.length)
    for (c in s) {
        when {
            c in '\u064B'..'\u065F' || c == '\u0670' || c == '\u0640' -> {}
            c == 'أ' || c == 'إ' || c == 'آ' -> sb.append('ا')
            c == 'ى' -> sb.append('ي')
            else -> sb.append(c)
        }
    }
    return sb.toString()
}

private val INVERT = ColorFilter.colorMatrix(
    ColorMatrix(floatArrayOf(-1f, 0f, 0f, 0f, 255f, 0f, -1f, 0f, 0f, 255f, 0f, 0f, -1f, 0f, 255f, 0f, 0f, 0f, 1f, 0f))
)

private class Tile(val bmp: Bitmap) { val img: ImageBitmap = bmp.asImageBitmap() }
private data class TKey(val page: Int, val sk: Int, val tx: Int, val ty: Int)
private data class Hit(val page: Int, val a: Int, val b: Int)
private data class Sel(val page: Int, val a: Int, val b: Int, val anchor: Int)

/** نطاق القطع المرئية من صفحة: [tx0, tx1, ty0, ty1] أو null لو مفيش جزء ظاهر */
private fun tileRange(vw: Float, vh: Float, pl: Float, pt: Float, pw: Float, ph: Float, k: Float): IntArray? {
    val vx0 = max(0f, pl)
    val vx1 = min(vw, pl + pw)
    val vy0 = max(0f, pt)
    val vy1 = min(vh, pt + ph)
    if (vx1 <= vx0 || vy1 <= vy0) return null
    return intArrayOf(
        floor((vx0 - pl) * k / TILE).toInt(), floor(((vx1 - pl) * k - 0.01f) / TILE).toInt(),
        floor((vy0 - pt) * k / TILE).toInt(), floor(((vy1 - pt) * k - 0.01f) / TILE).toInt()
    )
}

@Composable
fun ViewerScreen(p: Picked, onBack: () -> Unit, onTools: (Picked) -> Unit) {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    val clipboard = LocalClipboardManager.current
    var doc by remember { mutableStateOf<PdfDoc?>(null) }
    var file by remember { mutableStateOf<File?>(null) }
    var geo by remember { mutableStateOf<Geo?>(null) }
    var err by remember { mutableStateOf<String?>(null) }
    var vw by remember { mutableFloatStateOf(0f) }
    var vh by remember { mutableFloatStateOf(0f) }
    var zoom by remember { mutableFloatStateOf(1f) }
    var rsZoom by remember { mutableFloatStateOf(1f) }
    var scrollX by remember { mutableFloatStateOf(0f) }
    var scrollY by remember { mutableFloatStateOf(0f) }
    var tick by remember { mutableIntStateOf(0) }
    var night by remember { mutableStateOf(false) }
    var searchOpen by remember { mutableStateOf(false) }
    var query by remember { mutableStateOf("") }
    var hits by remember { mutableStateOf<List<Hit>>(emptyList()) }
    var hitIdx by remember { mutableIntStateOf(0) }
    var searching by remember { mutableStateOf(false) }
    var searched by remember { mutableStateOf(false) }
    var searchJob by remember { mutableStateOf<Job?>(null) }
    var sel by remember { mutableStateOf<Sel?>(null) }
    var jump by remember { mutableStateOf(false) }
    val words = remember { mutableStateMapOf<Int, List<Word>>() }
    val baseCache: LruCache<Int, Tile> = remember {
        object : LruCache<Int, Tile>(40 * 1024 * 1024) { override fun sizeOf(key: Int, value: Tile): Int = value.bmp.byteCount }
    }
    val tileCache: LruCache<TKey, Tile> = remember {
        object : LruCache<TKey, Tile>(64 * 1024 * 1024) { override fun sizeOf(key: TKey, value: Tile): Int = value.bmp.byteCount }
    }
    val jobs = remember { HashMap<Any, Job>() }
    val wordJobs = remember { HashSet<Int>() }
    val flings = remember { arrayOfNulls<Job>(2) }
    val renderDispatcher = remember { Executors.newSingleThreadExecutor().asCoroutineDispatcher() }

    LaunchedEffect(p.uri) {
        try {
            val f = withContext(Dispatchers.IO) { Fs.copyToCache(ctx, p.uri, p.name, "view") }
            val d = withContext(Dispatchers.IO) { PdfDoc(f) }
            val g = withContext(Dispatchers.IO) { d.sizes() }
            file = f; doc = d; geo = g
        } catch (e: Throwable) {
            err = if (e is SecurityException) "الملف محمي بباسورد. استخدم «فك حماية PDF» الأول." else "تعذّر فتح الملف"
        }
    }
    DisposableEffect(Unit) {
        onDispose {
            jobs.values.forEach { it.cancel() }
            renderDispatcher.close()
            doc?.close()
            file?.let { PdfText.close(it) }
        }
    }
    LaunchedEffect(zoom) {
        delay(250)
        rsZoom = (ceil(zoom * 4f) / 4f).coerceIn(1f, MAXZ)
    }

    // ───── هندسة وتحويلات ─────
    fun scaleNow(): Float { val g = geo ?: return 1f; return if (vw > 0f) vw / g.maxW * zoom else 1f }
    fun leftOff(): Float {
        val g = geo ?: return 0f
        val cw = g.maxW * scaleNow()
        return if (cw <= vw) (vw - cw) / 2f else -scrollX
    }
    fun clamp() {
        val g = geo ?: return
        val s = scaleNow()
        val cw = g.maxW * s
        val ch = g.total * s
        scrollX = if (cw <= vw) 0f else scrollX.coerceIn(0f, cw - vw)
        scrollY = if (ch <= vh) 0f else scrollY.coerceIn(0f, ch - vh)
    }
    fun zoomAt(newZoom: Float, cx: Float, cy: Float) {
        val g = geo ?: return
        val oldS = scaleNow()
        val xPt = (cx - leftOff()) / oldS
        val yPt = (scrollY + cy) / oldS
        zoom = newZoom.coerceIn(1f, MAXZ)
        val ns = scaleNow()
        scrollY = yPt * ns - cy
        scrollX = if (g.maxW * ns <= vw) 0f else xPt * ns - cx
        clamp()
    }
    fun fling(vx: Float, vy: Float) {
        flings.forEach { it?.cancel() }
        flings[0] = scope.launch {
            var last = 0f
            AnimationState(0f, vx).animateDecay(exponentialDecay()) { val d = value - last; last = value; scrollX -= d; clamp() }
        }
        flings[1] = scope.launch {
            var last = 0f
            AnimationState(0f, vy).animateDecay(exponentialDecay()) { val d = value - last; last = value; scrollY -= d; clamp() }
        }
    }

    // ───── تحميل القطع والكلمات حسب المكان المرئي ─────
    fun ensure() {
        val d = doc ?: return
        val g = geo ?: return
        val f = file ?: return
        if (vw <= 0f || vh <= 0f) return
        val bs = vw / g.maxW
        val s = bs * zoom
        val left = leftOff()
        val needed = HashSet<Any>()
        val yTop = scrollY / s
        val yBot = (scrollY + vh) / s
        var tilesLeft = 40
        var i = g.pageAt(yTop)
        while (i < g.n && g.tops[i] <= yBot) {
            val page = i
            val bkey = TKey(page, -1, 0, 0)
            needed.add(bkey)
            if (baseCache.get(page) == null && !jobs.containsKey(bkey)) {
                val pwPx = ceil(g.w[page] * bs).toInt().coerceAtLeast(1)
                val phPx = ceil(g.h[page] * bs).toInt().coerceAtLeast(1)
                jobs[bkey] = scope.launch(renderDispatcher) {
                    val bmp = try { d.renderTile(page, bs, 0, 0, pwPx, phPx) } catch (_: Throwable) { null }
                    withContext(Dispatchers.Main) {
                        if (bmp != null) baseCache.put(page, Tile(bmp))
                        jobs.remove(bkey); tick++
                    }
                }
            }
            if (zoom > 1.15f) {
                val rs = bs * rsZoom
                val sk = (rsZoom * 100).toInt()
                val pl = left + (g.maxW - g.w[page]) / 2f * s
                val pt = g.tops[page] * s - scrollY
                val tr = tileRange(vw, vh, pl, pt, g.w[page] * s, g.h[page] * s, rs / s)
                if (tr != null) {
                    val pxW = ceil(g.w[page] * rs).toInt()
                    val pxH = ceil(g.h[page] * rs).toInt()
                    for (ty in tr[2]..tr[3]) for (tx in tr[0]..tr[1]) {
                        val key = TKey(page, sk, tx, ty)
                        needed.add(key)
                        if (tileCache.get(key) == null && !jobs.containsKey(key) && tilesLeft > 0) {
                            tilesLeft--
                            val tw = min(TILE, pxW - tx * TILE).coerceAtLeast(1)
                            val th = min(TILE, pxH - ty * TILE).coerceAtLeast(1)
                            jobs[key] = scope.launch(renderDispatcher) {
                                val bmp = try { d.renderTile(page, rs, tx * TILE, ty * TILE, tw, th) } catch (_: Throwable) { null }
                                withContext(Dispatchers.Main) {
                                    if (bmp != null) tileCache.put(key, Tile(bmp))
                                    jobs.remove(key); tick++
                                }
                            }
                        }
                    }
                }
            }
            if (!words.containsKey(page) && wordJobs.add(page)) {
                scope.launch(Dispatchers.IO) {
                    val ws = try { PdfText.words(ctx, f, page) } catch (_: Throwable) { emptyList() }
                    withContext(Dispatchers.Main) { words[page] = ws; wordJobs.remove(page) }
                }
            }
            i++
        }
        val iter = jobs.entries.iterator()
        while (iter.hasNext()) { val e = iter.next(); if (e.key !in needed) { e.value.cancel(); iter.remove() } }
    }
    LaunchedEffect(doc, geo) {
        snapshotFlow { listOf(scrollX, scrollY, zoom, rsZoom, vw, vh) }.collect { ensure() }
    }

    // ───── تحديد النص ─────
    fun hitTest(pos: Offset): Pair<Int, Int>? {
        val g = geo ?: return null
        val s = scaleNow()
        val yPt = (scrollY + pos.y) / s
        val pg = g.pageAt(yPt)
        if (pg >= g.n) return null
        val xPt = (pos.x - leftOff()) / s - (g.maxW - g.w[pg]) / 2f
        val yIn = yPt - g.tops[pg]
        val ws = words[pg] ?: return null
        var best = -1
        var bd = Float.MAX_VALUE
        ws.forEachIndexed { k, w ->
            val dx = if (xPt < w.l) w.l - xPt else if (xPt > w.r) xPt - w.r else 0f
            val dy = if (yIn < w.t) w.t - yIn else if (yIn > w.b) yIn - w.b else 0f
            val dd = dx * dx + dy * dy
            if (dd < bd) { bd = dd; best = k }
        }
        return if (best >= 0 && bd <= 14f * 14f) pg to best else null
    }
    fun selectAt(pos: Offset): Boolean {
        val h = hitTest(pos) ?: return false
        sel = Sel(h.first, h.second, h.second, h.second)
        return true
    }
    fun extendTo(pos: Offset) {
        val cur = sel ?: return
        val h = hitTest(pos) ?: return
        if (h.first != cur.page) return
        sel = cur.copy(a = min(cur.anchor, h.second), b = max(cur.anchor, h.second))
    }

    // ───── البحث ─────
    fun goToHit(i: Int) {
        val g = geo ?: return
        val h = hits.getOrNull(i) ?: return
        hitIdx = i
        flings.forEach { it?.cancel() }
        val w = words[h.page]?.getOrNull(h.a)
        val s = scaleNow()
        scrollY = (g.tops[h.page] + (w?.t ?: 0f)) * s - vh / 3f
        if (w != null && g.maxW * s > vw) scrollX = ((g.maxW - g.w[h.page]) / 2f + (w.l + w.r) / 2f) * s - vw / 2f
        clamp()
    }
    fun runSearch() {
        val d = doc ?: return
        val f = file ?: return
        val q = normAr(query.trim())
        if (q.isEmpty()) return
        searchJob?.cancel()
        searchJob = scope.launch {
            searching = true; searched = false; hits = emptyList(); hitIdx = 0
            val loaded = HashMap<Int, List<Word>>()
            val found = withContext(Dispatchers.IO) {
                val out = ArrayList<Hit>()
                for (i in 0 until d.count) {
                    ensureActive()
                    val ws = words[i] ?: try { PdfText.words(ctx, f, i) } catch (_: Throwable) { emptyList() }
                    loaded[i] = ws
                    val offs = IntArray(ws.size)
                    val sb = StringBuilder()
                    ws.forEachIndexed { k, w -> offs[k] = sb.length; sb.append(normAr(w.text)).append(' ') }
                    val hay = sb.toString()
                    fun wordOf(pos: Int): Int { var k = 0; while (k + 1 < offs.size && offs[k + 1] <= pos) k++; return k }
                    var from = 0
                    while (true) {
                        val idx = hay.indexOf(q, from, ignoreCase = true)
                        if (idx < 0) break
                        out.add(Hit(i, wordOf(idx), wordOf(idx + q.length - 1)))
                        from = idx + q.length
                    }
                }
                out
            }
            loaded.forEach { (k, v) -> if (words[k] == null) words[k] = v }
            hits = found; searching = false; searched = true
            if (found.isNotEmpty()) goToHit(0)
        }
    }
    fun step(delta: Int) { if (hits.isNotEmpty()) goToHit((hitIdx + delta + hits.size) % hits.size) }

    fun exportText() {
        val d = doc ?: return
        val f = file ?: return
        scope.launch {
            Toast.makeText(ctx, tr("جاري استخراج النص…"), Toast.LENGTH_SHORT).show()
            val txt = withContext(Dispatchers.IO) {
                val sb = StringBuilder()
                for (i in 0 until d.count) {
                    val ws = words[i] ?: try { PdfText.words(ctx, f, i) } catch (_: Throwable) { emptyList() }
                    if (ws.isNotEmpty()) sb.append(joinWords(ws, 0, ws.lastIndex)).append("\n\n")
                }
                sb.toString()
            }
            if (txt.isBlank()) {
                Toast.makeText(ctx, tr("مفيش نص في الملف (ممسوح؟) جرّب OCR"), Toast.LENGTH_LONG).show()
            } else {
                val out = File(ctx.cacheDir, "view/${p.name.substringBeforeLast('.')}.txt")
                withContext(Dispatchers.IO) { out.parentFile?.mkdirs(); out.writeText(txt) }
                val ok = withContext(Dispatchers.IO) { Fs.saveToDownloads(ctx, out) }
                if (txt.length < 150_000) clipboard.setText(AnnotatedString(txt))
                Toast.makeText(ctx, tr(if (ok) "اتنسخ واتحفظ في Download/QuickFile" else "اتنسخ"), Toast.LENGTH_LONG).show()
            }
        }
    }

    val curPage by remember { derivedStateOf { val g = geo; if (g == null || vw <= 0f) 0 else g.pageAt((scrollY + vh / 2f) / (vw / g.maxW * zoom)) } }

    Column(Modifier.fillMaxSize().statusBarsPadding()) {
        TopBar(p.name, "👁️", null, onBack, raw = true) {
            if (doc != null) {
                IconButton(onClick = { searchOpen = !searchOpen }) { Text("🔍", raw = true) }
                IconButton(onClick = { night = !night; tick++ }) { Text(if (night) "☀️" else "🌙", raw = true) }
                IconButton(onClick = { exportText() }) { Text("📄", raw = true) }
            }
        }
        if (searchOpen) {
            Row(Modifier.fillMaxWidth().padding(horizontal = 12.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                OutlinedTextField(
                    value = query, onValueChange = { query = it }, singleLine = true, shape = RoundedCornerShape(16.dp),
                    placeholder = { Text("بحث في الملف…") }, modifier = Modifier.weight(1f),
                    keyboardOptions = KeyboardOptions(imeAction = ImeAction.Search),
                    keyboardActions = KeyboardActions(onSearch = { runSearch() })
                )
                if (hits.isNotEmpty()) {
                    Text("${hitIdx + 1}/${hits.size}", fontSize = 13.sp, raw = true)
                    TextButton(onClick = { step(-1) }) { Text("▲", raw = true) }
                    TextButton(onClick = { step(1) }) { Text("▼", raw = true) }
                }
            }
            if (searching) Text("جاري البحث…", fontSize = 12.sp, modifier = Modifier.padding(horizontal = 16.dp, vertical = 4.dp), color = MaterialTheme.colorScheme.onSurfaceVariant)
            else if (searched) Text(
                if (hits.isEmpty()) "مفيش نتائج" else "${hits.size} نتيجة", fontSize = 12.sp,
                modifier = Modifier.padding(horizontal = 16.dp, vertical = 4.dp), color = MaterialTheme.colorScheme.onSurfaceVariant
            )
        }
        Box(Modifier.weight(1f).fillMaxWidth().background(if (night) Color.Black else MaterialTheme.colorScheme.surfaceVariant).clipToBounds()) {
            val g = geo
            when {
                err != null -> Text(err!!, Modifier.align(Alignment.Center).padding(24.dp), color = MaterialTheme.colorScheme.error)
                g == null -> CircularProgressIndicator(Modifier.align(Alignment.Center))
                else -> {
                    val hl = MaterialTheme.colorScheme.primary
                    Canvas(
                        Modifier.fillMaxSize()
                            .onSizeChanged { vw = it.width.toFloat(); vh = it.height.toFloat() }
                            .pointerInput(Unit) {
                                val slop = viewConfiguration.touchSlop
                                val lpTimeout = viewConfiguration.longPressTimeoutMillis
                                var lastTapTime = 0L
                                var lastTapPos = Offset.Zero
                                awaitEachGesture {
                                    val down = awaitFirstDown(requireUnconsumed = false)
                                    flings.forEach { it?.cancel() }
                                    val res = withTimeoutOrNull(lpTimeout) {
                                        var r = 0
                                        while (r == 0) {
                                            val ev = awaitPointerEvent()
                                            if (ev.changes.count { it.pressed } >= 2) { r = 3; continue }
                                            val c = ev.changes.firstOrNull { it.id == down.id } ?: continue
                                            if (c.changedToUpIgnoreConsumed()) r = 1
                                            else if ((c.position - down.position).getDistance() > slop) r = 2
                                        }
                                        r
                                    }
                                    when (res) {
                                        null -> {   // ضغطة طويلة: تحديد نص
                                            val found = selectAt(down.position)
                                            do {
                                                val ev = awaitPointerEvent()
                                                val c = ev.changes.firstOrNull { it.id == down.id }
                                                if (found && c != null && c.positionChanged()) { extendTo(c.position); c.consume() }
                                            } while (ev.changes.any { it.pressed })
                                        }
                                        1 -> {   // نقرة
                                            val now = SystemClock.uptimeMillis()
                                            if (now - lastTapTime < 300 && (down.position - lastTapPos).getDistance() < 80f) {
                                                if (zoom > 1.05f) zoomAt(1f, down.position.x, down.position.y) else zoomAt(2.5f, down.position.x, down.position.y)
                                                lastTapTime = 0L
                                            } else {
                                                lastTapTime = now; lastTapPos = down.position
                                                sel = null
                                            }
                                        }
                                        else -> {   // سحب / قرص
                                            val tracker = VelocityTracker()
                                            tracker.addPosition(down.uptimeMillis, down.position)
                                            do {
                                                val ev = awaitPointerEvent()
                                                val pressed = ev.changes.count { it.pressed }
                                                if (pressed >= 2) {
                                                    val z = ev.calculateZoom()
                                                    val cen = ev.calculateCentroid()
                                                    if (z != 1f && cen != Offset.Unspecified) zoomAt(zoom * z, cen.x, cen.y)
                                                }
                                                val pan = ev.calculatePan()
                                                scrollX -= pan.x; scrollY -= pan.y; clamp()
                                                ev.changes.firstOrNull { it.pressed }?.let { tracker.addPosition(it.uptimeMillis, it.position) }
                                                ev.changes.forEach { if (it.positionChanged()) it.consume() }
                                            } while (ev.changes.any { it.pressed })
                                            val v = tracker.calculateVelocity()
                                            if (abs(v.x) > 200f || abs(v.y) > 200f) fling(v.x, v.y)
                                        }
                                    }
                                }
                            }
                    ) {
                        tick
                        val bs = vw / g.maxW
                        val s = bs * zoom
                        val cw = g.maxW * s
                        val left = if (cw <= vw) (vw - cw) / 2f else -scrollX
                        val yBot = (scrollY + vh) / s
                        var i = g.pageAt(scrollY / s)
                        val pageHits = hits
                        val sl = sel
                        while (i < g.n && g.tops[i] <= yBot) {
                            val pw = g.w[i] * s
                            val ph = g.h[i] * s
                            val pl = left + (g.maxW - g.w[i]) / 2f * s
                            val pt = g.tops[i] * s - scrollY
                            drawRect(if (night) Color.Black else Color.White, Offset(pl, pt), Size(pw, ph))
                            baseCache.get(i)?.let { t ->
                                drawImage(
                                    t.img, srcOffset = IntOffset.Zero, srcSize = IntSize(t.bmp.width, t.bmp.height),
                                    dstOffset = IntOffset(pl.roundToInt(), pt.roundToInt()), dstSize = IntSize(ceil(pw).toInt(), ceil(ph).toInt()),
                                    colorFilter = if (night) INVERT else null, filterQuality = FilterQuality.Medium
                                )
                            }
                            if (zoom > 1.15f) {
                                val rs = bs * rsZoom
                                val k = rs / s
                                val sk = (rsZoom * 100).toInt()
                                val tr = tileRange(vw, vh, pl, pt, pw, ph, k)
                                if (tr != null) {
                                    for (ty in tr[2]..tr[3]) for (tx in tr[0]..tr[1]) {
                                        val t = tileCache.get(TKey(i, sk, tx, ty)) ?: continue
                                        val dx = pl + tx * TILE / k
                                        val dy = pt + ty * TILE / k
                                        val ix = floor(dx).toInt()
                                        val iy = floor(dy).toInt()
                                        drawImage(
                                            t.img, srcOffset = IntOffset.Zero, srcSize = IntSize(t.bmp.width, t.bmp.height),
                                            dstOffset = IntOffset(ix, iy),
                                            dstSize = IntSize(ceil(dx + t.bmp.width / k).toInt() - ix, ceil(dy + t.bmp.height / k).toInt() - iy),
                                            colorFilter = if (night) INVERT else null, filterQuality = FilterQuality.Medium
                                        )
                                    }
                                }
                            }
                            val ws = words[i]
                            if (ws != null) {
                                for ((hi, h) in pageHits.withIndex()) {
                                    if (h.page != i) continue
                                    val c = if (hi == hitIdx) Color(0x99FF9800) else Color(0x66FFEB3B)
                                    for (kx in h.a..h.b) {
                                        val w = ws.getOrNull(kx) ?: continue
                                        drawRect(c, Offset(pl + w.l * s, pt + w.t * s), Size((w.r - w.l) * s, (w.b - w.t) * s))
                                    }
                                }
                                if (sl != null && sl.page == i) {
                                    for (kx in sl.a..sl.b) {
                                        val w = ws.getOrNull(kx) ?: continue
                                        drawRect(hl.copy(alpha = 0.35f), Offset(pl + w.l * s, pt + w.t * s), Size((w.r - w.l) * s, (w.b - w.t) * s))
                                    }
                                }
                            }
                            i++
                        }
                    }
                    Surface(
                        modifier = Modifier.align(Alignment.BottomCenter).padding(bottom = 12.dp).clickable { jump = true },
                        shape = CircleShape, color = Color(0xCC000000)
                    ) {
                        Text("${curPage + 1} / ${g.n}", color = Color.White, fontSize = 12.sp, raw = true, modifier = Modifier.padding(horizontal = 14.dp, vertical = 6.dp))
                    }
                    val s0 = sel
                    if (s0 != null) {
                        Surface(
                            modifier = Modifier.align(Alignment.TopCenter).padding(top = 10.dp),
                            shape = RoundedCornerShape(14.dp), color = MaterialTheme.colorScheme.surface, shadowElevation = 6.dp
                        ) {
                            Row(Modifier.padding(horizontal = 6.dp), verticalAlignment = Alignment.CenterVertically) {
                                TextButton(onClick = {
                                    val ws = words[s0.page].orEmpty()
                                    clipboard.setText(AnnotatedString(joinWords(ws, s0.a, s0.b)))
                                    Toast.makeText(ctx, tr("اتنسخ"), Toast.LENGTH_SHORT).show()
                                    sel = null
                                }) { Text("نسخ", fontWeight = FontWeight.Bold) }
                                TextButton(onClick = {
                                    val ws = words[s0.page].orEmpty()
                                    if (ws.isNotEmpty()) sel = Sel(s0.page, 0, ws.lastIndex, 0)
                                }) { Text("الصفحة كلها") }
                                TextButton(onClick = { sel = null }) { Text("✕", raw = true) }
                            }
                        }
                    }
                }
            }
        }
        val f = file
        if (f != null) {
            Row(
                Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 10.dp).navigationBarsPadding(),
                horizontalArrangement = Arrangement.spacedBy(10.dp)
            ) {
                Button(
                    onClick = { onTools(Picked(Uri.fromFile(f), p.name, f.length(), "application/pdf")) },
                    shape = RoundedCornerShape(14.dp), modifier = Modifier.weight(1f).height(48.dp)
                ) { Text("🧰  أدوات على الملف", fontWeight = FontWeight.Bold) }
                FilledTonalButton(
                    onClick = {
                        val i = Intent(Intent.ACTION_SEND).setType("application/pdf")
                            .putExtra(Intent.EXTRA_STREAM, Fs.uriFor(ctx, f))
                            .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                        ctx.startActivity(Intent.createChooser(i, "share").addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
                    },
                    shape = RoundedCornerShape(14.dp), modifier = Modifier.height(48.dp)
                ) { Text("مشاركة") }
            }
        }
    }

    if (jump && geo != null) {
        var txt by remember { mutableStateOf("") }
        AlertDialog(
            onDismissRequest = { jump = false },
            title = { Text("اذهب لصفحة") },
            text = {
                OutlinedTextField(
                    value = txt, onValueChange = { txt = it.filter { c -> c.isDigit() } }, singleLine = true,
                    label = { Text("رقم الصفحة") }, keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number)
                )
            },
            confirmButton = {
                TextButton(onClick = {
                    val g = geo
                    val n = txt.toIntOrNull()
                    if (g != null && n != null) {
                        flings.forEach { it?.cancel() }
                        scrollY = g.tops[(n - 1).coerceIn(0, g.n - 1)] * scaleNow()
                        clamp()
                    }
                    jump = false
                }) { Text("تمام") }
            }
        )
    }
}
