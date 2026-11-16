package com.edunurse.quickfile

import android.widget.MediaController
import android.widget.VideoView
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import java.util.Locale

private fun fmtMs(ms: Long): String {
    val t = ms / 1000
    return "%d:%02d:%02d.%03d".format(Locale.US, t / 3600, (t / 60) % 60, t % 60, ms % 1000)
}

private fun parseMs(t: String): Long {
    val v = t.trim().split(":").map { it.toDoubleOrNull() ?: 0.0 }.fold(0.0) { a, x -> a * 60 + x }
    return (v * 1000).toLong()
}

/** مشغّل صغير جوه شاشة الأداة: تشوف الفيديو/الصوت وانت بتقص أو بتدمج */
@Composable
fun InlinePlayer(p: Picked, marking: Boolean, vals: MutableMap<String, String>) {
    val holder = remember(p.uri) { arrayOfNulls<VideoView>(1) }
    var failed by remember(p.uri) { mutableStateOf(false) }
    AppCard {
        Box(
            Modifier.fillMaxWidth().height(if (p.kind == "video") 210.dp else 80.dp).clip(RoundedCornerShape(14.dp)).background(Color.Black),
            contentAlignment = Alignment.Center
        ) {
            if (failed) {
                Text("تعذّر تشغيل الملف", color = Color.White, fontSize = 13.sp)
            } else {
                AndroidView(
                    factory = { c ->
                        VideoView(c).also { v ->
                            val mc = MediaController(c)
                            mc.setAnchorView(v)
                            v.setMediaController(mc)
                            v.setOnErrorListener { _, _, _ -> failed = true; true }
                            v.setOnPreparedListener { v.seekTo(1) }
                            v.setVideoURI(p.uri)
                            holder[0] = v
                        }
                    },
                    modifier = Modifier.fillMaxSize(),
                    onRelease = { it.stopPlayback() }
                )
            }
        }
        if (marking && !failed) {
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                FilledTonalButton(
                    onClick = { holder[0]?.let { vals["start"] = fmtMs(it.currentPosition.toLong()) } },
                    shape = RoundedCornerShape(12.dp), modifier = Modifier.weight(1f)
                ) { Text("⏱ بداية من هنا", fontSize = 13.sp) }
                FilledTonalButton(
                    onClick = { holder[0]?.let { vals["end"] = fmtMs(it.currentPosition.toLong()) } },
                    shape = RoundedCornerShape(12.dp), modifier = Modifier.weight(1f)
                ) { Text("⏱ نهاية هنا", fontSize = 13.sp) }
            }
            TextButton(onClick = {
                holder[0]?.let { v -> v.seekTo(parseMs(vals["start"].orEmpty()).toInt()); v.start() }
            }) { Text("▶ شغّل من البداية المحددة", fontSize = 13.sp) }
        }
    }
}
