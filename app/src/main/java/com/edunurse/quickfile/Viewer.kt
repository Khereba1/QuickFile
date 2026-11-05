@file:OptIn(ExperimentalMaterial3Api::class)

package com.edunurse.quickfile

import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.Color as AColor
import android.graphics.pdf.PdfRenderer
import android.net.Uri
import android.os.ParcelFileDescriptor
import android.os.SystemClock
import android.util.LruCache
import android.widget.Toast
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.gestures.calculatePan
import androidx.compose.foundation.gestures.calculateZoom
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clipToBounds
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.ColorFilter
import androidx.compose.ui.graphics.ColorMatrix
import androidx.compose.ui.graphics.TransformOrigin
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.input.pointer.PointerEventPass
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.tom_roush.pdfbox.pdmodel.PDDocument
import com.tom_roush.pdfbox.text.PDFTextStripper
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File
import java.util.concurrent.ConcurrentHashMap

class PdfDoc(val file: File) {
    private val pfd = ParcelFileDescriptor.open(file, ParcelFileDescriptor.MODE_READ_ONLY)
    private val r = PdfRenderer(pfd)
    val count: Int = r.pageCount
    private val cache = object : LruCache<String, Bitmap>(48 * 1024 * 1024) {
        override fun sizeOf(key: String, value: Bitmap): Int = value.byteCount
    }

    fun render(i: Int, width: Int): Bitmap {
        val key = "$i@$width"
        cache.get(key)?.let { return it }
        val b = synchronized(this) {
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

    fun aspect(): Float = synchronized(this) {
        val p = r.openPage(0)
        try { p.height.toFloat() / p.width } finally { p.close() }
    }

    fun close() {
        try { r.close(); pfd.close() } catch (_: Exception) { }
    }
}

/** استخراج نص الصفحات (للبحث والنسخ) بـ PdfBox */
object PdfText {
    private val docs = ConcurrentHashMap<String, PDDocument>()
    private val cache = ConcurrentHashMap<String, String>()

    fun page(ctx: Context, f: File, i: Int): String {
        Pdf.ensure(ctx)
        val key = "${f.path}#$i"
        cache[key]?.let { return it }
        val d = docs.getOrPut(f.path) { PDDocument.load(f) }
        val t = synchronized(d) {
            val st = PDFTextStripper()
            st.sortByPosition = true
            st.startPage = i + 1
            st.endPage = i + 1
            st.getText(d).trim()
        }
        cache[key] = t
        return t
    }

    fun close(f: File) {
        docs.remove(f.path)?.let { try { it.close() } catch (_: Exception) { } }
        cache.keys.removeAll { it.startsWith(f.path + "#") }
    }
}

private val INVERT = ColorFilter.colorMatrix(
    ColorMatrix(floatArrayOf(-1f, 0f, 0f, 0f, 255f, 0f, -1f, 0f, 0f, 255f, 0f, 0f, -1f, 0f, 255f, 0f, 0f, 0f, 1f, 0f))
)

@Composable
fun ViewerScreen(p: Picked, onBack: () -> Unit, onTools: (Picked) -> Unit) {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    val clipboard = LocalClipboardManager.current
    var doc by remember { mutableStateOf<PdfDoc?>(null) }
    var file by remember { mutableStateOf<File?>(null) }
    var err by remember { mutableStateOf<String?>(null) }
    var scale by remember { mutableFloatStateOf(1f) }
    var offX by remember { mutableFloatStateOf(0f) }
    var vw by remember { mutableFloatStateOf(0f) }
    var mulS by remember { mutableFloatStateOf(1.5f) }
    var night by remember { mutableStateOf(false) }
    var searchOpen by remember { mutableStateOf(false) }
    var query by remember { mutableStateOf("") }
    var results by remember { mutableStateOf<List<Int>>(emptyList()) }
    var resIdx by remember { mutableIntStateOf(0) }
    var searching by remember { mutableStateOf(false) }
    var searched by remember { mutableStateOf(false) }
    var searchJob by remember { mutableStateOf<Job?>(null) }
    var textPage by remember { mutableStateOf<Int?>(null) }
    var jump by remember { mutableStateOf(false) }
    val aspects = remember { mutableStateMapOf<Int, Float>() }
    val listState = rememberLazyListState()

    LaunchedEffect(p.uri) {
        try {
            val f = withContext(Dispatchers.IO) { Fs.copyToCache(ctx, p.uri, p.name, "view") }
            val d = withContext(Dispatchers.IO) { PdfDoc(f) }
            file = f
            doc = d
        } catch (e: Throwable) {
            err = if (e is SecurityException) "الملف محمي بباسورد. استخدم «فك حماية PDF» الأول." else "تعذّر فتح الملف"
        }
    }
    DisposableEffect(Unit) { onDispose { doc?.close(); file?.let { PdfText.close(it) } } }
    LaunchedEffect(scale) {
        delay(300)
        mulS = when { scale <= 1.05f -> 1.5f; scale <= 2.2f -> 2.2f; else -> 3f }
    }

    fun runSearch() {
        val d = doc ?: return
        val f = file ?: return
        val q = query.trim()
        if (q.isEmpty()) return
        searchJob?.cancel()
        searchJob = scope.launch {
            searching = true; searched = false; results = emptyList(); resIdx = 0
            val found = withContext(Dispatchers.IO) {
                val out = ArrayList<Int>()
                for (i in 0 until d.count) {
                    ensureActive()
                    val t = try { PdfText.page(ctx, f, i) } catch (_: Throwable) { "" }
                    if (t.contains(q, ignoreCase = true)) out.add(i)
                }
                out
            }
            results = found; searching = false; searched = true
            if (found.isNotEmpty()) listState.scrollToItem(found[0])
        }
    }
    fun go(delta: Int) {
        if (results.isEmpty()) return
        resIdx = (resIdx + delta + results.size) % results.size
        scope.launch { listState.animateScrollToItem(results[resIdx]) }
    }

    Column(Modifier.fillMaxSize().statusBarsPadding()) {
        TopBar(p.name, "👁️", null, onBack, raw = true) {
            if (doc != null) {
                IconButton(onClick = { searchOpen = !searchOpen }) { Text("🔍", raw = true) }
                IconButton(onClick = { night = !night }) { Text(if (night) "☀️" else "🌙", raw = true) }
                IconButton(onClick = { textPage = listState.firstVisibleItemIndex }) { Text("📋", raw = true) }
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
                if (results.isNotEmpty()) {
                    Text("${resIdx + 1}/${results.size}", fontSize = 13.sp, raw = true)
                    TextButton(onClick = { go(-1) }) { Text("▲", raw = true) }
                    TextButton(onClick = { go(1) }) { Text("▼", raw = true) }
                }
            }
            if (searching) { Text("جاري البحث…", fontSize = 12.sp, modifier = Modifier.padding(horizontal = 16.dp, vertical = 4.dp), color = MaterialTheme.colorScheme.onSurfaceVariant) }
            else if (searched) Text(
                if (results.isEmpty()) "مفيش نتائج" else "${results.size} صفحة فيها نتائج", fontSize = 12.sp,
                modifier = Modifier.padding(horizontal = 16.dp, vertical = 4.dp), color = MaterialTheme.colorScheme.onSurfaceVariant
            )
        }
        Box(
            Modifier.weight(1f).fillMaxWidth()
                .background(if (night) Color.Black else MaterialTheme.colorScheme.surfaceVariant)
                .clipToBounds()
                .onSizeChanged { vw = it.width.toFloat() }
                .pointerInput(Unit) {
                    var lastTap = 0L
                    awaitEachGesture {
                        val down = awaitFirstDown(requireUnconsumed = false, pass = PointerEventPass.Initial)
                        val start = SystemClock.uptimeMillis()
                        var moved = false
                        var multi = false
                        do {
                            val ev = awaitPointerEvent(PointerEventPass.Initial)
                            val n = ev.changes.count { it.pressed }
                            if (n >= 2) multi = true
                            if (ev.changes.any { (it.position - down.position).getDistance() > 24f }) moved = true
                            if (n >= 2 || scale > 1f) {
                                val z = ev.calculateZoom()
                                val pan = ev.calculatePan()
                                if (n >= 2) scale = (scale * z).coerceIn(1f, 4f)
                                val mx = (scale - 1f) * vw / 2f
                                offX = (offX + pan.x).coerceIn(-mx, mx)
                                if (scale > 1f) listState.dispatchRawDelta(-pan.y / scale)
                                ev.changes.forEach { it.consume() }
                            }
                        } while (ev.changes.any { it.pressed })
                        if (!moved && !multi && SystemClock.uptimeMillis() - start < 250) {
                            if (start - lastTap < 320) {
                                scale = if (scale > 1.05f) 1f else 2.5f
                                offX = 0f
                                lastTap = 0L
                            } else lastTap = start
                        }
                    }
                }
        ) {
            val d = doc
            when {
                err != null -> Text(err!!, Modifier.align(Alignment.Center).padding(24.dp), color = MaterialTheme.colorScheme.error)
                d == null || vw <= 0f -> CircularProgressIndicator(Modifier.align(Alignment.Center))
                else -> {
                    val widthPx = (vw * mulS).toInt().coerceIn(200, 2400)
                    val aspect0 = remember(d) { try { d.aspect() } catch (_: Throwable) { 1.41f } }
                    LazyColumn(
                        Modifier.fillMaxSize().graphicsLayer {
                            scaleX = scale; scaleY = scale; translationX = offX
                            transformOrigin = TransformOrigin(0.5f, 0f)
                        },
                        state = listState,
                        contentPadding = PaddingValues(8.dp),
                        verticalArrangement = Arrangement.spacedBy(8.dp)
                    ) {
                        items(d.count) { i -> PageView(d, i, widthPx, aspects[i] ?: aspect0, night) { a -> aspects[i] = a } }
                    }
                    Surface(
                        modifier = Modifier.align(Alignment.BottomCenter).padding(bottom = 12.dp).clickable { jump = true },
                        shape = CircleShape, color = Color(0xCC000000)
                    ) {
                        Text(
                            "${listState.firstVisibleItemIndex + 1} / ${d.count}", color = Color.White, fontSize = 12.sp, raw = true,
                            modifier = Modifier.padding(horizontal = 14.dp, vertical = 6.dp)
                        )
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

    // ── نص الصفحة (تحديد ونسخ) ──
    val tp = textPage
    val dd = doc
    val ff = file
    if (tp != null && dd != null && ff != null) {
        ModalBottomSheet(
            onDismissRequest = { textPage = null },
            sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)
        ) {
            val txt by produceState<String?>(null, tp) {
                value = withContext(Dispatchers.IO) { try { PdfText.page(ctx, ff, tp) } catch (_: Throwable) { "" } }
            }
            Column(Modifier.padding(horizontal = 20.dp).padding(bottom = 24.dp).navigationBarsPadding(), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    TextButton(onClick = { textPage = tp - 1 }, enabled = tp > 0) { Text("▲", raw = true) }
                    Text("صفحة ${tp + 1}/${dd.count}", fontWeight = FontWeight.SemiBold)
                    TextButton(onClick = { textPage = tp + 1 }, enabled = tp < dd.count - 1) { Text("▼", raw = true) }
                    Spacer(Modifier.weight(1f))
                    val t = txt
                    Button(
                        onClick = {
                            clipboard.setText(AnnotatedString(t.orEmpty()))
                            Toast.makeText(ctx, tr("اتنسخ"), Toast.LENGTH_SHORT).show()
                        },
                        enabled = !t.isNullOrBlank(), shape = RoundedCornerShape(12.dp)
                    ) { Text("نسخ الكل") }
                }
                val t = txt
                when {
                    t == null -> CircularProgressIndicator(Modifier.align(Alignment.CenterHorizontally))
                    t.isBlank() -> {
                        Text("الصفحة دي صورة (ممسوحة) ومفيهاش نص. جرّب OCR.", color = MaterialTheme.colorScheme.onSurfaceVariant)
                        Button(onClick = { textPage = null; onTools(Picked(Uri.fromFile(ff), p.name, ff.length(), "application/pdf")) }, shape = RoundedCornerShape(12.dp)) { Text("فتح OCR") }
                    }
                    else -> SelectionContainer {
                        Text(t, fontSize = 15.sp, raw = true, modifier = Modifier.heightIn(max = 380.dp).verticalScroll(rememberScrollState()))
                    }
                }
            }
        }
    }

    // ── الذهاب لصفحة ──
    if (jump && dd != null) {
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
                    txt.toIntOrNull()?.let { n -> scope.launch { listState.scrollToItem((n - 1).coerceIn(0, dd.count - 1)) } }
                    jump = false
                }) { Text("تمام") }
            }
        )
    }
}

@Composable
private fun PageView(doc: PdfDoc, i: Int, widthPx: Int, aspect: Float, night: Boolean, onAspect: (Float) -> Unit) {
    val bmp by produceState<Bitmap?>(null, i, widthPx) {
        value = withContext(Dispatchers.Default) { try { doc.render(i, widthPx) } catch (_: Throwable) { null } }
    }
    val b = bmp
    LaunchedEffect(b) { if (b != null) onAspect(b.height.toFloat() / b.width) }
    Box(
        Modifier.fillMaxWidth().then(if (b == null) Modifier.aspectRatio(1f / aspect) else Modifier)
            .background(if (night) Color.Black else Color.White),
        contentAlignment = Alignment.Center
    ) {
        if (b == null) CircularProgressIndicator(Modifier.size(26.dp), strokeWidth = 2.dp)
        else Image(
            b.asImageBitmap(), null, Modifier.fillMaxWidth(), contentScale = ContentScale.FillWidth,
            colorFilter = if (night) INVERT else null
        )
    }
}
