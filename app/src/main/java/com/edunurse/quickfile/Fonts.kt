package com.edunurse.quickfile

import androidx.compose.ui.text.font.Font
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight

/** خطوط مدمجة في التطبيق (عشان تشتغل حتى لو خط النظام متغيّر) */
val CairoFamily = FontFamily(
    Font(R.font.cairo_regular, FontWeight.Normal),
    Font(R.font.cairo_medium, FontWeight.Medium),
    Font(R.font.cairo_semibold, FontWeight.SemiBold),
    Font(R.font.cairo_bold, FontWeight.Bold),
    Font(R.font.cairo_extrabold, FontWeight.ExtraBold)
)

val PlexFamily = FontFamily(
    Font(R.font.plex_regular, FontWeight.Normal),
    Font(R.font.plex_medium, FontWeight.Medium),
    Font(R.font.plex_semibold, FontWeight.SemiBold),
    Font(R.font.plex_bold, FontWeight.Bold)
)

/** Mono حقيقي (DejaVu Sans Mono). العربي بيرجع لخط النظام لأن الخط ده مفيهوش حروف عربية. */
val MonoFamily = FontFamily(
    Font(R.font.mono_regular, FontWeight.Normal),
    Font(R.font.mono_bold, FontWeight.Bold)
)
