package com.edunurse.quickfile

import android.content.Context
import android.content.SharedPreferences
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue

/** إعدادات التطبيق (محفوظة في SharedPreferences) */
object Prefs {
    private var sp: SharedPreferences? = null

    /** 0 بدون حركات، 1 خفيفة (الافتراضي)، 2 كاملة */
    var motion by mutableIntStateOf(1)
    var lang by mutableStateOf("ar")
    var mono by mutableStateOf(false)
    var textScale by mutableFloatStateOf(1f)

    fun init(ctx: Context) {
        val s = ctx.getSharedPreferences("qf", Context.MODE_PRIVATE)
        sp = s
        motion = s.getInt("motion", 1)
        lang = s.getString("lang", "ar") ?: "ar"
        mono = s.getBoolean("mono", false)
        textScale = s.getFloat("ts", 1f)
    }

    fun setLanguage(l: String) {
        lang = l
        mono = l == "en"
        save()
    }

    fun save() {
        sp?.edit()
            ?.putInt("motion", motion)
            ?.putString("lang", lang)
            ?.putBoolean("mono", mono)
            ?.putFloat("ts", textScale)
            ?.apply()
    }
}
