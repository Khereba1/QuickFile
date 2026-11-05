@file:OptIn(androidx.compose.foundation.ExperimentalFoundationApi::class)

package com.edunurse.quickfile

import android.content.Intent
import android.graphics.Bitmap
import android.widget.MediaController
import android.widget.VideoView
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.gestures.rememberTransformableState
import androidx.compose.foundation.gestures.transformable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/** معاينة خفيفة للصور والصوت والفيديو */
@Composable
fun PreviewScreen(p: Picked, onBack: () -> Unit, onTools: (Picked) -> Unit) {
    val ctx = LocalContext.current
    var failed by remember { mutableStateOf(false) }
    Column(Modifier.fillMaxSize().statusBarsPadding()) {
        TopBar(p.name, kindEmoji(p.kind), null, onBack, raw = true)
        Box(Modifier.weight(1f).fillMaxWidth().background(Color.Black), contentAlignment = Alignment.Center) {
            when {
                failed -> Text("تعذّر تشغيل الملف", color = Color.White)
                p.kind == "image" -> ZoomImage(p)
                p.kind == "video" -> MediaView(p, Modifier.fillMaxSize()) { failed = true }
                else -> Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    Text("🎵", fontSize = 72.sp, raw = true)
                    Spacer(Modifier.height(24.dp))
                    MediaView(p, Modifier.fillMaxWidth().height(96.dp)) { failed = true }
                }
            }
        }
        Row(
            Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 10.dp).navigationBarsPadding(),
            horizontalArrangement = Arrangement.spacedBy(10.dp)
        ) {
            Button(onClick = { onTools(p) }, shape = RoundedCornerShape(14.dp), modifier = Modifier.weight(1f).height(48.dp)) {
                Text("🧰  أدوات على الملف", fontWeight = FontWeight.Bold)
            }
            FilledTonalButton(
                onClick = {
                    val i = Intent(Intent.ACTION_SEND).setType(p.mime ?: "*/*").putExtra(Intent.EXTRA_STREAM, p.uri)
                        .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                    ctx.startActivity(Intent.createChooser(i, "share").addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
                },
                shape = RoundedCornerShape(14.dp), modifier = Modifier.height(48.dp)
            ) { Text("مشاركة") }
        }
    }
}

@Composable
private fun MediaView(p: Picked, modifier: Modifier, onError: () -> Unit) {
    AndroidView(
        factory = { c ->
            VideoView(c).also { vv ->
                val mc = MediaController(c)
                mc.setAnchorView(vv)
                vv.setMediaController(mc)
                vv.setOnPreparedListener { vv.start(); if (p.kind == "audio") mc.show(0) }
                vv.setOnErrorListener { _, _, _ -> onError(); true }
                vv.setVideoURI(p.uri)
            }
        },
        modifier = modifier,
        onRelease = { it.stopPlayback() }
    )
}

@Composable
private fun ZoomImage(p: Picked) {
    val ctx = LocalContext.current
    val bmp by produceState<Bitmap?>(null, p.uri) {
        value = withContext(Dispatchers.IO) {
            try { Img.decode(Fs.copyToCache(ctx, p.uri, p.name, "view"), 2400) } catch (_: Throwable) { null }
        }
    }
    var scale by remember { mutableFloatStateOf(1f) }
    var off by remember { mutableStateOf(Offset.Zero) }
    var size by remember { mutableStateOf(IntSize.Zero) }
    val ts = rememberTransformableState { zoom, pan, _ ->
        scale = (scale * zoom).coerceIn(1f, 6f)
        val mx = (scale - 1f) * size.width / 2f
        val my = (scale - 1f) * size.height / 2f
        off = Offset((off.x + pan.x).coerceIn(-mx, mx), (off.y + pan.y).coerceIn(-my, my))
    }
    val b = bmp
    Box(
        Modifier.fillMaxSize().onSizeChanged { size = it }
            .pointerInput(Unit) { detectTapGestures(onDoubleTap = { scale = if (scale > 1.05f) 1f else 2.5f; off = Offset.Zero }) }
            .transformable(state = ts, canPan = { scale > 1f }),
        contentAlignment = Alignment.Center
    ) {
        if (b == null) CircularProgressIndicator()
        else Image(
            b.asImageBitmap(), null,
            Modifier.fillMaxSize().graphicsLayer { scaleX = scale; scaleY = scale; translationX = off.x; translationY = off.y },
            contentScale = ContentScale.Fit
        )
    }
}
