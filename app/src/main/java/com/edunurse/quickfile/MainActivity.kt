package com.edunurse.quickfile

import android.Manifest
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        Jobs.cleanup(applicationContext)
        if (Build.VERSION.SDK_INT >= 33 &&
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1)
        setContent {
            AppTheme {
                var open by rememberSaveable { mutableStateOf<String?>(null) }
                val tool = TOOLS.firstOrNull { it.id == open }
                if (tool == null) {
                    Home { open = it.id }
                } else {
                    BackHandler { open = null }
                    ToolScreen(tool) { open = null }
                }
            }
        }
    }
}
