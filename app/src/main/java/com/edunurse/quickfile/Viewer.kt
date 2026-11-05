package com.edunurse.quickfile

import android.content.Intent
import android.graphics.Bitmap
import android.graphics.Color as AColor
import android.graphics.pdf.PdfRenderer
import android.net.Uri
import android.os.ParcelFileDescriptor
import android.util.LruCache
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.File

class PdfDoc(val file: File) {
    private val pfd = ParcelFileDescriptor.open(file, ParcelFileDescriptor.MODE_READ_ONLY)
    private val r = PdfRenderer(pfd)
    val count: Int = r.pageCount
    private val cache = LruCache<String, Bitmap>(10)

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

@Composable
fun ViewerScreen(p: Picked, onBack: () -> Unit, onTools: (Picked) -> Unit) {
    val ctx = LocalContext.current
    var doc by remember { mutableStateOf<PdfDoc?>(null) }
    var file by remember { mutableStateOf<File?>(null) }
    var err by remember { mutableStateOf<String?>(null) }
    var zoom by remember { mutableFloatStateOf(1f) }

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
    DisposableEffect(Unit) { onDispose { doc?.close() } }

    val listState = rememberLazyListState()
    Column(Modifier.fillMaxSize().statusBarsPadding()) {
        TopBar(p.name, "👁️", null, onBack) {
            val d = doc
            if (d != null) {
                Text("${listState.firstVisibleItemIndex + 1}/${d.count}", fontSize = 13.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                TextButton(onClick = { zoom = (zoom - 0.5f).coerceAtLeast(1f) }, enabled = zoom > 1f) { Text("−", fontSize = 20.sp) }
                TextButton(onClick = { zoom = (zoom + 0.5f).coerceAtMost(3f) }, enabled = zoom < 3f) { Text("+", fontSize = 20.sp) }
            }
        }
        Box(Modifier.weight(1f).fillMaxWidth().background(MaterialTheme.colorScheme.surfaceVariant)) {
            val d = doc
            when {
                err != null -> Text(err!!, Modifier.align(Alignment.Center).padding(24.dp), color = MaterialTheme.colorScheme.error)
                d == null -> CircularProgressIndicator(Modifier.align(Alignment.Center))
                else -> BoxWithConstraints(Modifier.fillMaxSize()) {
                    val basePx = with(LocalDensity.current) { maxWidth.roundToPx() }
                    val widthPx = (basePx * zoom).toInt().coerceAtMost(2400)
                    val aspect = remember(d) { try { d.aspect() } catch (_: Throwable) { 1.41f } }
                    Box(Modifier.fillMaxSize().horizontalScroll(rememberScrollState())) {
                        LazyColumn(
                            Modifier.width(maxWidth * zoom),
                            state = listState,
                            contentPadding = PaddingValues(8.dp),
                            verticalArrangement = Arrangement.spacedBy(8.dp)
                        ) {
                            items(d.count) { i -> PageView(d, i, widthPx, aspect) }
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
                        ctx.startActivity(Intent.createChooser(i, "مشاركة").addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
                    },
                    shape = RoundedCornerShape(14.dp), modifier = Modifier.height(48.dp)
                ) { Text("مشاركة") }
            }
        }
    }
}

@Composable
private fun PageView(doc: PdfDoc, i: Int, widthPx: Int, aspect: Float) {
    val bmp by produceState<Bitmap?>(null, i, widthPx) {
        value = withContext(Dispatchers.Default) { try { doc.render(i, widthPx) } catch (_: Throwable) { null } }
    }
    val b = bmp
    val a by animateFloatAsState(if (b != null) 1f else 0f, tween(260), label = "pg")
    Box(
        Modifier.fillMaxWidth().then(if (b == null) Modifier.aspectRatio(1f / aspect) else Modifier)
            .background(androidx.compose.ui.graphics.Color.White),
        contentAlignment = Alignment.Center
    ) {
        if (b == null) CircularProgressIndicator(Modifier.size(26.dp), strokeWidth = 2.dp)
        else Image(b.asImageBitmap(), null, Modifier.fillMaxWidth().graphicsLayer { alpha = a }, contentScale = ContentScale.FillWidth)
    }
}
