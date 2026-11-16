package com.edunurse.quickfile

import android.content.Context
import android.content.SharedPreferences
import android.widget.Toast
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Share
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

/** ملف ناتج محفوظ في السجل */
data class Rec(val path: String, val name: String, val size: Long, val time: Long, val tool: String)

object History {
    private var sp: SharedPreferences? = null
    val items = mutableStateListOf<Rec>()
    private const val MAX = 30
    private const val MAX_BYTES = 1_500_000_000L

    fun init(ctx: Context) {
        sp = ctx.getSharedPreferences("qf", Context.MODE_PRIVATE)
        items.clear()
        try {
            val arr = JSONArray(sp?.getString("hist", "[]") ?: "[]")
            for (i in 0 until arr.length()) {
                val o = arr.getJSONObject(i)
                val r = Rec(o.getString("p"), o.getString("n"), o.getLong("s"), o.getLong("t"), o.optString("k"))
                if (File(r.path).exists()) items.add(r)
            }
        } catch (_: Exception) { }
    }

    private fun persist() {
        val arr = JSONArray()
        items.forEach { r ->
            arr.put(JSONObject().put("p", r.path).put("n", r.name).put("s", r.size).put("t", r.time).put("k", r.tool))
        }
        sp?.edit()?.putString("hist", arr.toString())?.apply()
    }

    private fun deleteFile(r: Rec) {
        val f = File(r.path)
        f.delete()
        f.parentFile?.let { if (it.list().isNullOrEmpty()) it.delete() }
    }

    fun add(files: List<File>, tool: String) {
        val now = System.currentTimeMillis()
        files.reversed().forEach { f -> items.add(0, Rec(f.absolutePath, f.name, f.length(), now, tool)) }
        while (items.size > MAX || (items.size > 1 && items.sumOf { it.size } > MAX_BYTES)) {
            deleteFile(items.removeAt(items.lastIndex))
        }
        persist()
    }

    fun remove(r: Rec) {
        items.remove(r)
        deleteFile(r)
        persist()
    }

    fun clear() {
        items.toList().forEach { deleteFile(it) }
        items.clear()
        persist()
    }
}

private fun ago(t: Long): String {
    val m = (System.currentTimeMillis() - t) / 60000L
    return when {
        m < 1 -> "now"
        m < 60 -> "${m}m"
        m < 1440 -> "${m / 60}h"
        else -> "${m / 1440}d"
    }
}

@Composable
private fun RecRow(r: Rec, full: Boolean) {
    val ctx = LocalContext.current
    val f = File(r.path)
    AppCard {
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            Text(kindEmoji(kindOf(r.name, null)), fontSize = 24.sp, raw = true)
            Column(Modifier.weight(1f)) {
                Text(r.name, fontSize = 14.sp, fontWeight = FontWeight.Medium, maxLines = 1, overflow = TextOverflow.Ellipsis, raw = true)
                Text(
                    "${Fs.size(r.size)} · ${tr(r.tool)} · ${ago(r.time)}", fontSize = 12.sp,
                    color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1, overflow = TextOverflow.Ellipsis, raw = true
                )
            }
            TextButton(onClick = { openFile(ctx, f) }) { Text("فتح") }
            IconButton(onClick = { shareFile(ctx, f) }) { Icon(Icons.Default.Share, contentDescription = "share") }
        }
        if (full) {
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                FilledTonalButton(
                    onClick = {
                        val ok = Fs.save(ctx, f)
                        Toast.makeText(ctx, tr(if (ok) (if (Prefs.saveTree.isBlank()) "اتحفظ في Download/QuickFile" else "اتحفظ في المجلد المحدد") else "فشل حفظ"), Toast.LENGTH_LONG).show()
                    },
                    shape = RoundedCornerShape(12.dp), modifier = Modifier.weight(1f)
                ) { Text("حفظ في التنزيلات") }
                IconButton(onClick = { History.remove(r) }) { Icon(Icons.Default.Close, contentDescription = "remove") }
            }
        }
    }
}

/** قسم «آخر الملفات» في الرئيسية */
@Composable
fun RecentSection(onAll: () -> Unit) {
    if (History.items.isEmpty()) return
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
            Text("آخر الملفات", fontSize = 17.sp, fontWeight = FontWeight.Bold)
            TextButton(onClick = onAll) { Text("عرض الكل") }
        }
        History.items.take(3).forEach { RecRow(it, full = false) }
    }
}

@Composable
fun HistoryScreen(onBack: () -> Unit) {
    Column(Modifier.fillMaxSize().statusBarsPadding()) {
        TopBar("آخر الملفات", "🕘", null, onBack) {
            if (History.items.isNotEmpty()) TextButton(onClick = { History.clear() }) { Text("مسح الكل") }
        }
        if (History.items.isEmpty()) {
            Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                Text("مفيش ملفات لسه", color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        } else {
            LazyColumn(contentPadding = PaddingValues(horizontal = 20.dp, vertical = 8.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                items(History.items.toList(), key = { it.path }) { RecRow(it, full = true) }
                item { Spacer(Modifier.navigationBarsPadding().height(24.dp)) }
            }
        }
    }
}
