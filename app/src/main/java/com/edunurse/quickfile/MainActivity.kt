package com.edunurse.quickfile

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.slideInVertically
import androidx.compose.animation.slideOutVertically
import androidx.compose.animation.togetherWith
import androidx.compose.runtime.*
import androidx.compose.ui.platform.LocalContext
import kotlinx.coroutines.flow.MutableStateFlow

sealed class Screen(val depth: Int) {
    data object Home : Screen(0)
    data class Choose(val files: List<Picked>) : Screen(1)
    data class ToolS(val tool: Tool, val files: List<Picked>) : Screen(2)
    data class Viewer(val file: Picked) : Screen(2)
    data class Organize(val file: Picked?) : Screen(2)
}

/** ملفات جاية من مدير الملفات (فتح بـ / مشاركة) */
object Inbox {
    val incoming = MutableStateFlow<List<Picked>?>(null)
}

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        Jobs.cleanup(applicationContext)
        if (Build.VERSION.SDK_INT >= 33 &&
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1)
        handle(intent)
        setContent { AppTheme { Root() } }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        handle(intent)
    }

    @Suppress("DEPRECATION")
    private fun streamUri(i: Intent): Uri? =
        if (Build.VERSION.SDK_INT >= 33) i.getParcelableExtra(Intent.EXTRA_STREAM, Uri::class.java)
        else i.getParcelableExtra<Uri>(Intent.EXTRA_STREAM)

    @Suppress("DEPRECATION")
    private fun streamUris(i: Intent): List<Uri>? =
        if (Build.VERSION.SDK_INT >= 33) i.getParcelableArrayListExtra(Intent.EXTRA_STREAM, Uri::class.java)
        else i.getParcelableArrayListExtra<Uri>(Intent.EXTRA_STREAM)

    private fun handle(i: Intent?) {
        if (i == null) return
        val uris = ArrayList<Uri>()
        when (i.action) {
            Intent.ACTION_VIEW -> i.data?.let { uris.add(it) }
            Intent.ACTION_SEND -> streamUri(i)?.let { uris.add(it) }
            Intent.ACTION_SEND_MULTIPLE -> streamUris(i)?.let { uris.addAll(it) }
        }
        if (uris.isEmpty()) return
        val picked = uris.mapNotNull { try { Fs.pick(applicationContext, it) } catch (_: Exception) { null } }
        if (picked.isNotEmpty()) Inbox.incoming.value = picked
        i.action = null
    }
}

private fun route(t: Tool, files: List<Picked>): Screen = when (t.special) {
    "organize" -> Screen.Organize(files.firstOrNull())
    "viewer" -> Screen.Viewer(files.first())
    else -> Screen.ToolS(t, files)
}

@Composable
fun Root() {
    val ctx = LocalContext.current
    val stack = remember { mutableStateListOf<Screen>(Screen.Home) }
    val inc by Inbox.incoming.collectAsState()

    LaunchedEffect(inc) {
        val files = inc ?: return@LaunchedEffect
        stack.clear()
        stack.add(Screen.Home)
        if (files.size == 1 && files[0].kind == "pdf") stack.add(Screen.Viewer(files[0])) else stack.add(Screen.Choose(files))
        Inbox.incoming.value = null
    }

    val pickPdf = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { u ->
        if (u != null) stack.add(Screen.Viewer(Fs.pick(ctx, u)))
    }
    fun back() { if (stack.size > 1) stack.removeAt(stack.lastIndex) }
    BackHandler(stack.size > 1) { back() }

    AnimatedContent(
        targetState = stack.last(),
        transitionSpec = {
            if (targetState.depth >= initialState.depth)
                (fadeIn(tween(240)) + slideInVertically(tween(280)) { it / 14 }) togetherWith fadeOut(tween(140))
            else
                fadeIn(tween(240)) togetherWith (fadeOut(tween(160)) + slideOutVertically(tween(240)) { it / 14 })
        },
        label = "nav"
    ) { s ->
        when (s) {
            is Screen.Home -> Home { t ->
                when (t.special) {
                    "viewer" -> pickPdf.launch(arrayOf("application/pdf"))
                    "organize" -> stack.add(Screen.Organize(null))
                    else -> stack.add(Screen.ToolS(t, emptyList()))
                }
            }
            is Screen.Choose -> ChooserScreen(s.files, { back() }) { t, f -> stack.add(route(t, f)) }
            is Screen.ToolS -> ToolScreen(s.tool, s.files) { back() }
            is Screen.Viewer -> ViewerScreen(s.file, { back() }) { p -> stack.add(Screen.Choose(listOf(p))) }
            is Screen.Organize -> OrganizerScreen(s.file) { back() }
        }
    }
}
