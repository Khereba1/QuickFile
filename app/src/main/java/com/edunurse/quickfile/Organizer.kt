package com.edunurse.quickfile

import android.graphics.Bitmap
import android.widget.Toast
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.animation.expandVertically
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.shrinkVertically
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.rotate
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File
import java.util.Collections
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.atomic.AtomicInteger

/** صفحة واحدة في المنظّم: من PDF (idx) أو صورة */
data class OPage(val id: Int, val src: File, val idx: Int, val rot: Int, val isImg: Boolean)

object Plan { var items: List<OPage> = emptyList() }

val ORGANIZE_TOOL = Tool(
    "organize", "تنظيم الصفحات", "", "🧩", Group.PDF, arrayOf("application/pdf"), false,
    exec = { Pdf.organize(it, Plan.items) }
)

@Composable
fun OrganizerScreen(initial: Picked?, onBack: () -> Unit) {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    val pages = remember { mutableStateListOf<OPage>() }
    val sel = remember { mutableStateListOf<Int>() }
    val docs = remember { ConcurrentHashMap<String, PdfDoc>() }
    val ids = remember { AtomicInteger(1) }
    var loading by remember { mutableStateOf(false) }
    val state by Jobs.state.collectAsState()
    val running = state is JobState.Running
    val mine = (state as? JobState.Done)?.toolId == "organize" || (state as? JobState.Failed)?.toolId == "organize" || running

    DisposableEffect(Unit) { onDispose { docs.values.forEach { it.close() } } }

    fun addFiles(ps: List<Picked>) {
        scope.launch {
            loading = true
            try {
                val added = withContext(Dispatchers.IO) {
                    val list = ArrayList<OPage>()
                    for (p in ps) {
                        when (p.kind) {
                            "pdf" -> {
                                val f = Fs.copyToCache(ctx, p.uri, p.name, "org")
                                val d = PdfDoc(f)
                                docs[f.path] = d
                                for (i in 0 until d.count) list.add(OPage(ids.getAndIncrement(), f, i, 0, false))
                            }
                            "image" -> {
                                val f = Fs.copyToCache(ctx, p.uri, p.name, "org")
                                list.add(OPage(ids.getAndIncrement(), f, 0, 0, true))
                            }
                        }
                    }
                    list
                }
                pages.addAll(added)
            } catch (e: Throwable) {
                val m = if (e is SecurityException) "الملف محمي بباسورد، فك الحماية الأول" else "تعذّر فتح الملف"
                Toast.makeText(ctx, m, Toast.LENGTH_LONG).show()
            } finally { loading = false }
        }
    }

    LaunchedEffect(Unit) { initial?.let { addFiles(listOf(it)) } }
    val picker = rememberLauncherForActivityResult(ActivityResultContracts.OpenMultipleDocuments()) { uris ->
        addFiles(uris.map { Fs.pick(ctx, it) })
    }

    fun move(dir: Int) {
        val idxs = pages.indices.filter { pages[it].id in sel }
        if (idxs.isEmpty()) return
        if (dir < 0) {
            if (idxs.first() == 0) return
            for (i in idxs) Collections.swap(pages, i, i - 1)
        } else {
            if (idxs.last() == pages.lastIndex) return
            for (i in idxs.reversed()) Collections.swap(pages, i, i + 1)
        }
    }
    fun moveEdge(toStart: Boolean) {
        val picked = pages.filter { it.id in sel }
        if (picked.isEmpty()) return
        pages.removeAll { it.id in sel }
        if (toStart) pages.addAll(0, picked) else pages.addAll(picked)
    }

    Column(Modifier.fillMaxSize().statusBarsPadding()) {
        TopBar("تنظيم الصفحات", "🧩", null, onBack) {
            if (pages.isNotEmpty()) Text("${pages.size}", fontSize = 13.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
            IconButton(onClick = { picker.launch(arrayOf("application/pdf", "image/*")) }, enabled = !running && !loading) {
                Icon(Icons.Default.Add, contentDescription = "إضافة")
            }
            Button(
                onClick = { Plan.items = pages.toList(); Jobs.start(ctx, ORGANIZE_TOOL, emptyList(), emptyMap()) },
                enabled = pages.isNotEmpty() && !running, shape = RoundedCornerShape(12.dp)
            ) { Text("حفظ", fontWeight = FontWeight.Bold) }
            Spacer(Modifier.width(8.dp))
        }

        Box(Modifier.weight(1f).fillMaxWidth()) {
            if (pages.isEmpty()) {
                Column(Modifier.align(Alignment.Center).padding(32.dp), horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    if (loading) CircularProgressIndicator() else {
                        Text("🧩", fontSize = 48.sp)
                        Text("اختار PDF أو صور عشان تبدأ", fontSize = 15.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        Button(onClick = { picker.launch(arrayOf("application/pdf", "image/*")) }, shape = RoundedCornerShape(14.dp)) { Text("اختار ملفات") }
                    }
                }
            } else {
                LazyVerticalGrid(
                    columns = GridCells.Fixed(3), modifier = Modifier.fillMaxSize(),
                    contentPadding = PaddingValues(12.dp),
                    horizontalArrangement = Arrangement.spacedBy(10.dp), verticalArrangement = Arrangement.spacedBy(10.dp)
                ) {
                    items(pages, key = { it.id }) { p ->
                        val n = pages.indexOf(p) + 1
                        val selected = p.id in sel
                        Thumb(p, docs[p.src.path], n, selected, Modifier.animateItem()) {
                            if (selected) sel.remove(p.id) else sel.add(p.id)
                        }
                    }
                }
            }
        }

        AnimatedVisibility(visible = mine, enter = fadeIn() + expandVertically(), exit = fadeOut() + shrinkVertically()) {
            Column(Modifier.padding(horizontal = 16.dp, vertical = 8.dp)) { JobPanel(ORGANIZE_TOOL, state) }
        }

        AnimatedVisibility(visible = sel.isNotEmpty() && !running, enter = fadeIn() + expandVertically(), exit = fadeOut() + shrinkVertically()) {
            Surface(color = MaterialTheme.colorScheme.surface, tonalElevation = 3.dp) {
                Row(
                    Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()).padding(horizontal = 12.dp, vertical = 10.dp).navigationBarsPadding(),
                    horizontalArrangement = Arrangement.spacedBy(8.dp)
                ) {
                    Act("${sel.size} محدد ✕") { sel.clear() }
                    Act("الكل") { sel.clear(); sel.addAll(pages.map { it.id }) }
                    Act("⏫ للأول") { moveEdge(true) }
                    Act("◀ تقديم") { move(-1) }
                    Act("تأخير ▶") { move(1) }
                    Act("⏬ للآخر") { moveEdge(false) }
                    Act("🔄 تدوير") {
                        for (i in pages.indices) if (pages[i].id in sel) pages[i] = pages[i].copy(rot = pages[i].rot + 90)
                    }
                    Act("📑 تكرار") {
                        var i = 0
                        while (i < pages.size) {
                            val p = pages[i]
                            if (p.id in sel) { pages.add(i + 1, p.copy(id = ids.getAndIncrement())); i++ }
                            i++
                        }
                    }
                    Act("🗑️ حذف") { pages.removeAll { it.id in sel }; sel.clear() }
                }
            }
        }
        if (sel.isEmpty() || running) Spacer(Modifier.navigationBarsPadding())
    }
}

@Composable
private fun Act(label: String, onClick: () -> Unit) {
    FilledTonalButton(onClick = onClick, shape = RoundedCornerShape(12.dp), contentPadding = PaddingValues(horizontal = 14.dp, vertical = 8.dp)) {
        Text(label, fontSize = 13.sp)
    }
}

@Composable
private fun Thumb(p: OPage, doc: PdfDoc?, n: Int, selected: Boolean, modifier: Modifier, onClick: () -> Unit) {
    val bmp by produceState<Bitmap?>(null, p.src, p.idx, p.isImg) {
        value = withContext(Dispatchers.Default) {
            try { if (p.isImg) Img.decode(p.src, 420) else doc?.render(p.idx, 320) } catch (_: Throwable) { null }
        }
    }
    val rot by animateFloatAsState(p.rot.toFloat(), tween(300), label = "rot")
    val shape = RoundedCornerShape(10.dp)
    val bd = if (selected) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.outline
    Box(
        modifier.aspectRatio(0.72f).clip(shape).background(Color.White)
            .border(if (selected) 3.dp else 1.dp, bd, shape).clickable(onClick = onClick),
        contentAlignment = Alignment.Center
    ) {
        val b = bmp
        if (b == null) CircularProgressIndicator(Modifier.size(22.dp), strokeWidth = 2.dp)
        else Image(b.asImageBitmap(), null, Modifier.fillMaxSize().rotate(rot), contentScale = ContentScale.Fit)
        Box(
            Modifier.align(Alignment.TopStart).padding(6.dp).clip(CircleShape).background(Color(0xCC000000)).padding(horizontal = 7.dp, vertical = 2.dp)
        ) { Text("$n", color = Color.White, fontSize = 11.sp, fontWeight = FontWeight.Bold) }
        if (p.isImg) Text("🖼️", Modifier.align(Alignment.BottomStart).padding(6.dp), fontSize = 12.sp)
        if (selected) Box(
            Modifier.matchParentSize().background(MaterialTheme.colorScheme.primary.copy(alpha = 0.18f)),
            contentAlignment = Alignment.TopEnd
        ) {
            Box(Modifier.padding(6.dp).size(22.dp).clip(CircleShape).background(MaterialTheme.colorScheme.primary), contentAlignment = Alignment.Center) {
                Text("✓", color = MaterialTheme.colorScheme.onPrimary, fontSize = 13.sp, fontWeight = FontWeight.Bold)
            }
        }
    }
}
