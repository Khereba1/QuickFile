@file:OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)

package com.edunurse.quickfile

import android.content.Intent
import android.net.Uri
import android.widget.Toast
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Share
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalLayoutDirection
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.LayoutDirection
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import java.io.File

private val DarkColors = darkColorScheme(
    primary = Color(0xFF5EEAD4), onPrimary = Color(0xFF00201C),
    primaryContainer = Color(0xFF14403B), onPrimaryContainer = Color(0xFFB8F5EA),
    secondaryContainer = Color(0xFF14403B), onSecondaryContainer = Color(0xFFB8F5EA),
    background = Color(0xFF0E1317), onBackground = Color(0xFFE6EDF3),
    surface = Color(0xFF151C22), onSurface = Color(0xFFE6EDF3),
    surfaceVariant = Color(0xFF1E2830), onSurfaceVariant = Color(0xFF9AA8B5),
    outline = Color(0xFF2B3742), outlineVariant = Color(0xFF2B3742), error = Color(0xFFFF8A80)
)
private val LightColors = lightColorScheme(
    primary = Color(0xFF0B7A6E), onPrimary = Color.White,
    primaryContainer = Color(0xFFCDEFE8), onPrimaryContainer = Color(0xFF00201C),
    secondaryContainer = Color(0xFFCDEFE8), onSecondaryContainer = Color(0xFF00201C),
    background = Color(0xFFF5F6F2), onBackground = Color(0xFF14201E),
    surface = Color.White, onSurface = Color(0xFF14201E),
    surfaceVariant = Color(0xFFE9EDE8), onSurfaceVariant = Color(0xFF566562),
    outline = Color(0xFFD5DBD6), outlineVariant = Color(0xFFD5DBD6), error = Color(0xFFB3261E)
)

@Composable
fun AppTheme(content: @Composable () -> Unit) {
    MaterialTheme(colorScheme = if (isSystemInDarkTheme()) DarkColors else LightColors) {
        CompositionLocalProvider(LocalLayoutDirection provides LayoutDirection.Rtl) {
            Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background, content = content)
        }
    }
}

// ───────────────────────── الرئيسية ─────────────────────────

@Composable
fun Home(onOpen: (Tool) -> Unit) {
    LazyColumn(
        Modifier.fillMaxSize().statusBarsPadding(),
        contentPadding = PaddingValues(horizontal = 20.dp, vertical = 12.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp)
    ) {
        item {
            Column(Modifier.padding(top = 20.dp, bottom = 8.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                Text("QuickFile", fontSize = 36.sp, fontWeight = FontWeight.ExtraBold)
                Text("ملفاتك. على جهازك. من غير نت.", fontSize = 15.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                Spacer(Modifier.height(4.dp))
                Surface(shape = CircleShape, color = MaterialTheme.colorScheme.primaryContainer) {
                    Text(
                        "أوفلاين بالكامل · مفيش رفع لأي سيرفر", fontSize = 12.sp, fontWeight = FontWeight.Medium,
                        color = MaterialTheme.colorScheme.onPrimaryContainer,
                        modifier = Modifier.padding(horizontal = 12.dp, vertical = 6.dp)
                    )
                }
            }
        }
        Group.values().forEach { g ->
            val ts = TOOLS.filter { it.group == g }
            item(key = "h_" + g.name) {
                Row(Modifier.padding(top = 10.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Box(Modifier.size(10.dp).clip(CircleShape).background(Color(g.color)))
                    Text(g.title, fontSize = 17.sp, fontWeight = FontWeight.Bold)
                }
            }
            items(ts.chunked(2), key = { "r_" + it.first().id }) { row ->
                Row(Modifier.fillMaxWidth().height(IntrinsicSize.Min), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                    row.forEach { ToolCard(it, Modifier.weight(1f).fillMaxHeight()) { onOpen(it) } }
                    if (row.size == 1) Spacer(Modifier.weight(1f))
                }
            }
        }
        item { Spacer(Modifier.navigationBarsPadding().height(24.dp)) }
    }
}

@Composable
private fun ToolCard(t: Tool, modifier: Modifier, onClick: () -> Unit) {
    Surface(
        onClick = onClick, modifier = modifier, shape = RoundedCornerShape(20.dp),
        color = MaterialTheme.colorScheme.surface, border = BorderStroke(1.dp, MaterialTheme.colorScheme.outline)
    ) {
        Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Box(
                Modifier.size(44.dp).clip(RoundedCornerShape(14.dp)).background(Color(t.group.color).copy(alpha = 0.18f)),
                contentAlignment = Alignment.Center
            ) { Text(t.emoji, fontSize = 22.sp) }
            Spacer(Modifier.height(6.dp))
            Text(t.title, fontSize = 15.sp, fontWeight = FontWeight.SemiBold, maxLines = 1, overflow = TextOverflow.Ellipsis)
            Text(
                t.sub, fontSize = 12.sp, lineHeight = 17.sp, minLines = 2, maxLines = 2, overflow = TextOverflow.Ellipsis,
                color = MaterialTheme.colorScheme.onSurfaceVariant
            )
        }
    }
}

// ───────────────────────── شاشة الأداة ─────────────────────────

@Composable
fun ToolScreen(tool: Tool, onBack: () -> Unit) {
    val ctx = LocalContext.current
    val picks = remember(tool.id) { mutableStateListOf<Picked>() }
    val vals = remember(tool.id) {
        mutableStateMapOf<String, String>().also { m ->
            tool.opts.forEach { o ->
                m[o.key] = when (o) {
                    is Opt.Choice -> o.def
                    is Opt.Text -> o.def
                    is Opt.Toggle -> if (o.def) "1" else "0"
                }
            }
        }
    }
    val state by Jobs.state.collectAsState()
    val running = state is JobState.Running

    fun add(uris: List<Uri>) {
        uris.forEach { u ->
            val (n, s) = Fs.info(ctx, u)
            if (!tool.multi) picks.clear()
            picks.add(Picked(u, n, s))
        }
    }

    val multiL = rememberLauncherForActivityResult(ActivityResultContracts.OpenMultipleDocuments()) { add(it) }
    val oneL = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { u -> if (u != null) add(listOf(u)) }

    Column(Modifier.fillMaxSize().statusBarsPadding()) {
        Row(Modifier.fillMaxWidth().padding(horizontal = 8.dp, vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
            IconButton(onClick = onBack) { Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "رجوع") }
            Box(Modifier.size(34.dp).clip(RoundedCornerShape(10.dp)).background(Color(tool.group.color).copy(alpha = 0.18f)), contentAlignment = Alignment.Center) {
                Text(tool.emoji, fontSize = 17.sp)
            }
            Spacer(Modifier.width(10.dp))
            Text(tool.title, fontSize = 20.sp, fontWeight = FontWeight.Bold)
        }
        Column(
            Modifier.weight(1f).verticalScroll(rememberScrollState()).padding(horizontal = 20.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp)
        ) {
            // الملفات
            Card {
                Text(if (picks.isEmpty()) tool.sub else "الملفات المختارة (${picks.size})", fontSize = 14.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                picks.forEachIndexed { i, p ->
                    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                        Column(Modifier.weight(1f)) {
                            Text(p.name, fontSize = 14.sp, fontWeight = FontWeight.Medium, maxLines = 1, overflow = TextOverflow.Ellipsis)
                            Text(Fs.size(p.size), fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                        if (tool.ordered && picks.size > 1) {
                            TextButton(onClick = { if (i > 0) { val t = picks[i]; picks[i] = picks[i - 1]; picks[i - 1] = t } }, enabled = i > 0 && !running) { Text("▲") }
                            TextButton(onClick = { if (i < picks.size - 1) { val t = picks[i]; picks[i] = picks[i + 1]; picks[i + 1] = t } }, enabled = i < picks.size - 1 && !running) { Text("▼") }
                        }
                        IconButton(onClick = { picks.removeAt(i) }, enabled = !running) { Icon(Icons.Default.Close, contentDescription = "حذف") }
                    }
                }
                FilledTonalButton(
                    onClick = { if (tool.multi) multiL.launch(tool.mimes) else oneL.launch(tool.mimes) },
                    enabled = !running, shape = RoundedCornerShape(14.dp), modifier = Modifier.fillMaxWidth()
                ) { Text(if (picks.isEmpty()) "اختار ملف" else if (tool.multi) "إضافة ملفات" else "تغيير الملف") }
            }

            // الخيارات
            tool.opts.forEach { o ->
                when (o) {
                    is Opt.Choice -> Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        Label(o.label)
                        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            o.choices.forEach { (k, l) ->
                                FilterChip(selected = vals[o.key] == k, onClick = { vals[o.key] = k }, label = { Text(l) }, enabled = !running)
                            }
                        }
                    }
                    is Opt.Text -> OutlinedTextField(
                        value = vals[o.key] ?: "", onValueChange = { vals[o.key] = it },
                        label = { Text(o.label) }, placeholder = { Text(o.hint) }, singleLine = true, enabled = !running,
                        keyboardOptions = if (o.numeric) KeyboardOptions(keyboardType = KeyboardType.Number) else KeyboardOptions.Default,
                        shape = RoundedCornerShape(14.dp), modifier = Modifier.fillMaxWidth()
                    )
                    is Opt.Toggle -> Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
                        Text(o.label, fontSize = 14.sp, modifier = Modifier.weight(1f))
                        Switch(checked = vals[o.key] == "1", onCheckedChange = { vals[o.key] = if (it) "1" else "0" }, enabled = !running)
                    }
                }
            }

            // زر التشغيل
            val need = tool.minFiles - picks.size
            Button(
                onClick = { Jobs.start(ctx, tool, picks.toList(), vals.toMap()) },
                enabled = !running && need <= 0,
                shape = RoundedCornerShape(16.dp), modifier = Modifier.fillMaxWidth().height(54.dp)
            ) { Text(if (need > 0) "اختار $need ملف كمان على الأقل" else "ابدأ", fontSize = 16.sp, fontWeight = FontWeight.Bold) }

            JobPanel(tool, state)
            Spacer(Modifier.navigationBarsPadding().height(24.dp))
        }
    }
}

@Composable
private fun Label(t: String) = Text(t, fontSize = 13.sp, fontWeight = FontWeight.SemiBold, color = MaterialTheme.colorScheme.onSurfaceVariant)

@Composable
private fun Card(content: @Composable ColumnScope.() -> Unit) {
    Surface(
        shape = RoundedCornerShape(20.dp), color = MaterialTheme.colorScheme.surface,
        border = BorderStroke(1.dp, MaterialTheme.colorScheme.outline), modifier = Modifier.fillMaxWidth()
    ) { Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp), content = content) }
}

@Composable
private fun JobPanel(tool: Tool, s: JobState) {
    val ctx = LocalContext.current
    when (s) {
        is JobState.Running -> Card {
            Text(s.label, fontSize = 14.sp, fontWeight = FontWeight.Medium)
            LinearProgressIndicator(progress = { s.progress }, modifier = Modifier.fillMaxWidth().height(8.dp).clip(RoundedCornerShape(4.dp)))
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
                Text("${(s.progress * 100).toInt()}%", fontSize = 13.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                TextButton(onClick = { Jobs.cancel() }) { Text("إلغاء") }
            }
        }
        is JobState.Failed -> if (s.toolId == tool.id) {
            Surface(shape = RoundedCornerShape(20.dp), color = MaterialTheme.colorScheme.error.copy(alpha = 0.12f), modifier = Modifier.fillMaxWidth()) {
                Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        Icon(Icons.Default.Warning, contentDescription = null, tint = MaterialTheme.colorScheme.error)
                        Text("حصلت مشكلة", fontWeight = FontWeight.Bold, color = MaterialTheme.colorScheme.error)
                    }
                    Text(s.msg, fontSize = 13.sp)
                    TextButton(onClick = { Jobs.reset() }) { Text("تمام") }
                }
            }
        }
        is JobState.Done -> if (s.toolId == tool.id) {
            Card {
                Text("خلصت ✓", fontSize = 16.sp, fontWeight = FontWeight.Bold, color = MaterialTheme.colorScheme.primary)
                if (s.note.isNotBlank()) Text(s.note, fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                s.files.forEach { f ->
                    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                        Column(Modifier.weight(1f)) {
                            Text(f.name, fontSize = 14.sp, fontWeight = FontWeight.Medium, maxLines = 1, overflow = TextOverflow.Ellipsis)
                            Text(Fs.size(f.length()), fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                        TextButton(onClick = { openFile(ctx, f) }) { Text("فتح") }
                        IconButton(onClick = { shareFile(ctx, f) }) { Icon(Icons.Default.Share, contentDescription = "مشاركة") }
                    }
                }
                Button(
                    onClick = {
                        val ok = s.files.count { Fs.saveToDownloads(ctx, it) }
                        Toast.makeText(ctx, if (ok == s.files.size) "اتحفظ في Download/QuickFile" else "فشل حفظ ${s.files.size - ok} ملف", Toast.LENGTH_LONG).show()
                    },
                    shape = RoundedCornerShape(14.dp), modifier = Modifier.fillMaxWidth()
                ) { Text(if (s.files.size > 1) "حفظ الكل في التنزيلات" else "حفظ في التنزيلات") }
            }
        }
        else -> {}
    }
}

private fun openFile(ctx: android.content.Context, f: File) {
    try {
        ctx.startActivity(
            Intent(Intent.ACTION_VIEW).setDataAndType(Fs.uriFor(ctx, f), Fs.mime(f))
                .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        )
    } catch (_: Exception) {
        Toast.makeText(ctx, "مفيش تطبيق يفتح الملف ده، احفظه وافتحه من الملفات", Toast.LENGTH_LONG).show()
    }
}

private fun shareFile(ctx: android.content.Context, f: File) {
    val i = Intent(Intent.ACTION_SEND).setType(Fs.mime(f)).putExtra(Intent.EXTRA_STREAM, Fs.uriFor(ctx, f))
        .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
    ctx.startActivity(Intent.createChooser(i, "مشاركة").addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
}
