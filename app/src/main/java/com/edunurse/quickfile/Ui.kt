@file:OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)

package com.edunurse.quickfile

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.widget.Toast
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.animateContentSize
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.MutableTransitionState
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.spring
import androidx.compose.animation.core.tween
import androidx.compose.animation.expandVertically
import androidx.compose.animation.fadeIn
import androidx.compose.animation.slideInVertically
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.interaction.collectIsPressedAsState
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.Share
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.composed
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalLayoutDirection
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.LayoutDirection
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.delay
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

// ───────────────────────── حركات مشتركة ─────────────────────────

/** ضغطة بترتد (زي الزرار الحي) */
fun Modifier.bounceClick(onClick: () -> Unit): Modifier = composed {
    val src = remember { MutableInteractionSource() }
    val pressed by src.collectIsPressedAsState()
    val s by animateFloatAsState(if (pressed) 0.95f else 1f, spring(dampingRatio = 0.55f, stiffness = 500f), label = "press")
    this.graphicsLayer { scaleX = s; scaleY = s }.clickable(interactionSource = src, indication = null, onClick = onClick)
}

/** ظهور متدرّج: شفافية + طلوع لفوق */
@Composable
fun Modifier.enter(i: Int): Modifier {
    var shown by remember { mutableStateOf(false) }
    LaunchedEffect(Unit) { delay(45L * i.coerceAtMost(8)); shown = true }
    val a by animateFloatAsState(if (shown) 1f else 0f, tween(380), label = "ea")
    val dy by animateFloatAsState(if (shown) 0f else 28f, tween(420, easing = FastOutSlowInEasing), label = "ey")
    return this.graphicsLayer { alpha = a; translationY = dy }
}

@Composable
private fun Pop(content: @Composable () -> Unit) {
    val st = remember { MutableTransitionState(false).apply { targetState = true } }
    AnimatedVisibility(
        visibleState = st,
        enter = fadeIn(tween(250)) + slideInVertically(tween(300)) { it / 6 } + expandVertically()
    ) { content() }
}

@Composable
fun TopBar(title: String, emoji: String?, color: Color?, onBack: () -> Unit, actions: @Composable RowScope.() -> Unit = {}) {
    Row(Modifier.fillMaxWidth().padding(horizontal = 8.dp, vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
        IconButton(onClick = onBack) { Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "رجوع") }
        if (emoji != null) {
            Box(
                Modifier.size(34.dp).clip(RoundedCornerShape(10.dp))
                    .background((color ?: MaterialTheme.colorScheme.primary).copy(alpha = 0.18f)),
                contentAlignment = Alignment.Center
            ) { Text(emoji, fontSize = 17.sp) }
            Spacer(Modifier.width(10.dp))
        }
        Text(title, fontSize = 18.sp, fontWeight = FontWeight.Bold, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
        actions()
    }
}

// ───────────────────────── الرئيسية ─────────────────────────

@Composable
fun Home(onOpen: (Tool) -> Unit) {
    var q by remember { mutableStateOf("") }
    var cat by remember { mutableStateOf<Group?>(null) }
    val filtered = TOOLS.filter {
        (cat == null || it.group == cat) && (q.isBlank() || it.title.contains(q, true) || it.sub.contains(q, true))
    }
    LazyColumn(
        Modifier.fillMaxSize().statusBarsPadding(),
        contentPadding = PaddingValues(horizontal = 20.dp, vertical = 12.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp)
    ) {
        item { Banner() }
        item {
            OutlinedTextField(
                value = q, onValueChange = { q = it }, singleLine = true, shape = RoundedCornerShape(18.dp),
                placeholder = { Text("ابحث عن أداة…") },
                leadingIcon = { Icon(Icons.Default.Search, contentDescription = null) },
                trailingIcon = { if (q.isNotEmpty()) IconButton(onClick = { q = "" }) { Icon(Icons.Default.Close, contentDescription = "مسح") } },
                modifier = Modifier.fillMaxWidth().enter(1)
            )
        }
        item {
            LazyRow(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.enter(2)) {
                item { CatChip("الكل", cat == null) { cat = null } }
                items(Group.values().toList()) { g -> CatChip(g.title, cat == g) { cat = if (cat == g) null else g } }
            }
        }
        var order = 3
        for (g in Group.values()) {
            val ts = filtered.filter { it.group == g }
            if (ts.isEmpty()) continue
            item(key = "h_" + g.name) {
                Row(Modifier.padding(top = 10.dp).animateItem(), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Box(Modifier.size(10.dp).clip(CircleShape).background(Color(g.color)))
                    Text(g.title, fontSize = 17.sp, fontWeight = FontWeight.Bold)
                }
            }
            for (row in ts.chunked(2)) {
                val o = order++
                item(key = "r_" + row.first().id) {
                    Row(Modifier.fillMaxWidth().height(IntrinsicSize.Min).animateItem().enter(o), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                        row.forEach { t -> ToolCard(t, Modifier.weight(1f).fillMaxHeight()) { onOpen(t) } }
                        if (row.size == 1) Spacer(Modifier.weight(1f))
                    }
                }
            }
        }
        if (filtered.isEmpty()) item {
            Text("مفيش أداة بالاسم ده", Modifier.fillMaxWidth().padding(32.dp), color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        item { Spacer(Modifier.navigationBarsPadding().height(24.dp)) }
    }
}

@Composable
private fun CatChip(label: String, selected: Boolean, onClick: () -> Unit) {
    FilterChip(
        selected = selected, onClick = onClick, label = { Text(label) },
        colors = FilterChipDefaults.filterChipColors(
            selectedContainerColor = MaterialTheme.colorScheme.primaryContainer,
            selectedLabelColor = MaterialTheme.colorScheme.onPrimaryContainer
        )
    )
}

@Composable
private fun Banner() {
    val p = MaterialTheme.colorScheme.primary
    Box(
        Modifier.fillMaxWidth().padding(top = 8.dp).clip(RoundedCornerShape(28.dp))
            .background(Brush.linearGradient(listOf(p.copy(alpha = 0.28f), p.copy(alpha = 0.04f))))
            .border(1.dp, p.copy(alpha = 0.25f), RoundedCornerShape(28.dp))
            .padding(22.dp).enter(0)
    ) {
        Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Text(
                "QuickFile",
                style = TextStyle(brush = Brush.linearGradient(listOf(p, MaterialTheme.colorScheme.onSurface)), fontSize = 38.sp, fontWeight = FontWeight.ExtraBold)
            )
            Text("ملفاتك. على جهازك. من غير نت.", fontSize = 15.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Spacer(Modifier.height(4.dp))
            Surface(shape = CircleShape, color = MaterialTheme.colorScheme.surface.copy(alpha = 0.7f)) {
                Text(
                    "🔒 أوفلاين بالكامل · مفيش رفع لأي سيرفر", fontSize = 12.sp, fontWeight = FontWeight.Medium,
                    modifier = Modifier.padding(horizontal = 12.dp, vertical = 6.dp)
                )
            }
        }
    }
}

@Composable
private fun ToolCard(t: Tool, modifier: Modifier, onClick: () -> Unit) {
    val shape = RoundedCornerShape(20.dp)
    val gc = Color(t.group.color)
    Box(
        modifier.clip(shape)
            .background(Brush.linearGradient(listOf(gc.copy(alpha = 0.14f), MaterialTheme.colorScheme.surface)))
            .border(1.dp, MaterialTheme.colorScheme.outline, shape)
            .bounceClick(onClick).padding(14.dp)
    ) {
        Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Box(Modifier.size(44.dp).clip(RoundedCornerShape(14.dp)).background(gc.copy(alpha = 0.22f)), contentAlignment = Alignment.Center) {
                Text(t.emoji, fontSize = 22.sp)
            }
            Spacer(Modifier.height(6.dp))
            Text(t.title, fontSize = 15.sp, fontWeight = FontWeight.SemiBold, maxLines = 1, overflow = TextOverflow.Ellipsis)
            Text(
                t.sub, fontSize = 12.sp, lineHeight = 17.sp, minLines = 2, maxLines = 2, overflow = TextOverflow.Ellipsis,
                color = MaterialTheme.colorScheme.onSurfaceVariant
            )
        }
    }
}

// ───────────────────────── اختيار الأداة لملف وارد ─────────────────────────

private fun kindEmoji(k: String) = when (k) { "pdf" -> "📄"; "image" -> "🖼️"; "video" -> "🎞️"; "audio" -> "🎵"; else -> "📁" }

@Composable
fun ChooserScreen(files: List<Picked>, onBack: () -> Unit, onPick: (Tool, List<Picked>) -> Unit) {
    val kind = files.first().kind
    val use = files.filter { it.kind == kind }
    val tools = TOOLS.filter { it.accepts(kind) }
    Column(Modifier.fillMaxSize().statusBarsPadding()) {
        TopBar("اختار الأداة", "🧰", null, onBack)
        LazyColumn(
            contentPadding = PaddingValues(horizontal = 20.dp, vertical = 8.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp)
        ) {
            item {
                AppCard {
                    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                        Text(kindEmoji(kind), fontSize = 30.sp)
                        Column {
                            Text(use.first().name, fontSize = 15.sp, fontWeight = FontWeight.SemiBold, maxLines = 1, overflow = TextOverflow.Ellipsis)
                            Text(
                                if (use.size > 1) "و ${use.size - 1} ملف تاني" else Fs.size(use.first().size),
                                fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant
                            )
                        }
                    }
                }
            }
            if (tools.isEmpty()) item {
                Text("نوع الملف ده مش مدعوم في الأدوات الحالية.", color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(16.dp))
            }
            itemsIndexed(tools, key = { _, t -> t.id }) { i, t ->
                val gc = Color(t.group.color)
                Row(
                    Modifier.fillMaxWidth().enter(i).clip(RoundedCornerShape(18.dp))
                        .background(MaterialTheme.colorScheme.surface)
                        .border(1.dp, MaterialTheme.colorScheme.outline, RoundedCornerShape(18.dp))
                        .bounceClick { onPick(t, use) }.padding(14.dp),
                    verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)
                ) {
                    Box(Modifier.size(42.dp).clip(RoundedCornerShape(13.dp)).background(gc.copy(alpha = 0.2f)), contentAlignment = Alignment.Center) {
                        Text(t.emoji, fontSize = 20.sp)
                    }
                    Column(Modifier.weight(1f)) {
                        Text(t.title, fontSize = 15.sp, fontWeight = FontWeight.SemiBold)
                        Text(t.sub, fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1, overflow = TextOverflow.Ellipsis)
                    }
                    Text("‹", fontSize = 22.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
            item { Spacer(Modifier.navigationBarsPadding().height(24.dp)) }
        }
    }
}

// ───────────────────────── شاشة الأداة ─────────────────────────

@Composable
fun ToolScreen(tool: Tool, initial: List<Picked>, onBack: () -> Unit) {
    val ctx = LocalContext.current
    val picks = remember(tool.id) {
        mutableStateListOf<Picked>().also { l -> l.addAll(if (tool.multi) initial else initial.take(1)) }
    }
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
            val p = Fs.pick(ctx, u)
            if (!tool.multi) picks.clear()
            picks.add(p)
        }
    }

    val multiL = rememberLauncherForActivityResult(ActivityResultContracts.OpenMultipleDocuments()) { add(it) }
    val oneL = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { u -> if (u != null) add(listOf(u)) }

    Column(Modifier.fillMaxSize().statusBarsPadding()) {
        TopBar(tool.title, tool.emoji, Color(tool.group.color), onBack)
        Column(
            Modifier.weight(1f).verticalScroll(rememberScrollState()).padding(horizontal = 20.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp)
        ) {
            AppCard(Modifier.animateContentSize().enter(0)) {
                Text(if (picks.isEmpty()) tool.sub else "الملفات المختارة (${picks.size})", fontSize = 14.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                picks.forEachIndexed { i, p ->
                    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                        Text(kindEmoji(p.kind), fontSize = 20.sp)
                        Spacer(Modifier.width(10.dp))
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

            tool.opts.forEachIndexed { idx, o ->
                when (o) {
                    is Opt.Choice -> Column(Modifier.enter(idx + 1), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        Label(o.label)
                        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            o.choices.forEach { (k, l) ->
                                val sel = vals[o.key] == k
                                FilterChip(
                                    selected = sel, onClick = { vals[o.key] = k }, label = { Text(l) }, enabled = !running,
                                    leadingIcon = if (sel) { { Icon(Icons.Default.Check, contentDescription = null, modifier = Modifier.size(16.dp)) } } else null,
                                    colors = FilterChipDefaults.filterChipColors(
                                        selectedContainerColor = MaterialTheme.colorScheme.primaryContainer,
                                        selectedLabelColor = MaterialTheme.colorScheme.onPrimaryContainer
                                    )
                                )
                            }
                        }
                    }
                    is Opt.Text -> OutlinedTextField(
                        value = vals[o.key] ?: "", onValueChange = { vals[o.key] = it },
                        label = { Text(o.label) }, placeholder = { Text(o.hint) }, singleLine = true, enabled = !running,
                        keyboardOptions = if (o.numeric) KeyboardOptions(keyboardType = KeyboardType.Number) else KeyboardOptions.Default,
                        shape = RoundedCornerShape(14.dp), modifier = Modifier.fillMaxWidth().enter(idx + 1)
                    )
                    is Opt.Toggle -> Row(
                        Modifier.fillMaxWidth().enter(idx + 1), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically
                    ) {
                        Text(o.label, fontSize = 14.sp, modifier = Modifier.weight(1f))
                        Switch(checked = vals[o.key] == "1", onCheckedChange = { vals[o.key] = if (it) "1" else "0" }, enabled = !running)
                    }
                }
            }

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
fun AppCard(modifier: Modifier = Modifier, content: @Composable ColumnScope.() -> Unit) {
    Surface(
        shape = RoundedCornerShape(20.dp), color = MaterialTheme.colorScheme.surface,
        border = BorderStroke(1.dp, MaterialTheme.colorScheme.outline), modifier = modifier.fillMaxWidth()
    ) { Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp), content = content) }
}

@Composable
fun JobPanel(tool: Tool, s: JobState) {
    val ctx = LocalContext.current
    when (s) {
        is JobState.Running -> Pop {
            AppCard {
                val ap by animateFloatAsState(s.progress, tween(350), label = "prog")
                Text(s.label, fontSize = 14.sp, fontWeight = FontWeight.Medium)
                LinearProgressIndicator(progress = { ap }, modifier = Modifier.fillMaxWidth().height(10.dp).clip(RoundedCornerShape(5.dp)))
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
                    Text("${(s.progress * 100).toInt()}%", fontSize = 13.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    TextButton(onClick = { Jobs.cancel() }) { Text("إلغاء") }
                }
            }
        }
        is JobState.Failed -> if (s.toolId == tool.id) Pop {
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
        is JobState.Done -> if (s.toolId == tool.id) Pop {
            AppCard {
                var on by remember { mutableStateOf(false) }
                LaunchedEffect(Unit) { on = true }
                val sc by animateFloatAsState(if (on) 1f else 0f, spring(dampingRatio = 0.4f, stiffness = 260f), label = "pop")
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    Box(
                        Modifier.size(32.dp).graphicsLayer { scaleX = sc; scaleY = sc }.clip(CircleShape).background(MaterialTheme.colorScheme.primary),
                        contentAlignment = Alignment.Center
                    ) { Text("✓", color = MaterialTheme.colorScheme.onPrimary, fontWeight = FontWeight.Bold) }
                    Text("خلصت", fontSize = 17.sp, fontWeight = FontWeight.Bold, color = MaterialTheme.colorScheme.primary, modifier = Modifier.weight(1f))
                    TextButton(onClick = { Jobs.reset() }) { Text("إخفاء") }
                }
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

private fun openFile(ctx: Context, f: File) {
    try {
        ctx.startActivity(
            Intent(Intent.ACTION_VIEW).setDataAndType(Fs.uriFor(ctx, f), Fs.mime(f))
                .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        )
    } catch (_: Exception) {
        Toast.makeText(ctx, "مفيش تطبيق يفتح الملف ده، احفظه وافتحه من الملفات", Toast.LENGTH_LONG).show()
    }
}

private fun shareFile(ctx: Context, f: File) {
    val i = Intent(Intent.ACTION_SEND).setType(Fs.mime(f)).putExtra(Intent.EXTRA_STREAM, Fs.uriFor(ctx, f))
        .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
    ctx.startActivity(Intent.createChooser(i, "مشاركة").addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
}
