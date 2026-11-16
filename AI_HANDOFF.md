# QuickFile (Android): handoff notes for an AI assistant

Paste this file together with the project zip when asking an AI to change the app.

## What this is
Android app (Kotlin, Jetpack Compose Material3), 100% offline file tools: PDF (pdfbox-android + PdfRenderer),
OCR (tesseract4android, Arabic + English), media (ffmpeg-kit-16kb 6.1.1), keep-alive foreground service,
and exe flip-book to PDF through embedded Python (Chaquopy 17.2.0, Python 3.12, pillow).
Package/namespace/applicationId: com.edunurse.quickfile. minSdk 29, compileSdk/targetSdk 34, arm64-v8a only.
Compose BOM 2024.09.03, Kotlin compose compiler extension 1.5.14, Gradle 8.7, JDK 17. UI is Arabic, forced RTL.
Only core Material icons are used (material-icons-core); tool icons are emojis.

## Source map (app/src/main/java/com/edunurse/quickfile)
- Tools.kt: data model (Group, Opt.Choice/Text/Toggle, In, Ctx, Tool), `kindOf()`, `Tool.accepts(kind)` and the
  registry `TOOLS`. Special tools (`special = "organize" | "viewer"`) open their own screens instead of ToolScreen.
- Engine.kt: Picked, JobState, `Fs` (SAF copy, `Fs.save` = chosen SAF folder or MediaStore Download/QuickFile, FileProvider), `Jobs`:
  ONE running job + a queue (`Jobs.queue`, `QJob`); starting a job while busy enqueues it; wake lock + KeepAliveService stay on until the queue is empty;
  results go to `History` and optionally auto-save (`Prefs.autoSave`).
- Media.kt: FFmpegKit wrapper (`FF.exec` is a cancellable suspend fun) and video/audio operations. Encoders are
  probed at runtime (`FF.encoders()`), because the fork's codec set is unverified.
- Pdf.kt: `Img` (decode/EXIF/flatten) and `Pdf` operations: merge, split (extract/delete/ranges/every), rotate,
  page numbers, watermark (text rendered with Canvas so Arabic shapes correctly), protect, unlock, compress,
  PDF to images, images to PDF, convert images, `organize` (export for the page organizer).
- Ocr.kt: Tesseract OCR to a searchable PDF (invisible text layer) + TXT. Data and font come from build-time downloads.
- Viewer.kt: light PDF viewer (v1): LazyColumn of PdfRenderer bitmaps, pinch zoom + pan + double-tap (Initial-pass gesture handler),
  in-file search (page list), page-text bottom sheet with real selection/copy (PdfBox text), jump-to-page, night mode.
  A heavier "reader v2" (tile rendering + in-page selection/highlight) was tried in 1.3 and REVERTED on purpose: the owner judged it
  not close enough to Google Drive's viewer. It exists in git history (commit "v1.3: reader v2"). Do not rebuild a full reader;
  the product direction is "tools hub": invest in conversion/compression/organizing quality instead. `PdfDoc` here is also used by Organizer.
- Preview.kt: light media preview (VideoView + MediaController for video/audio, zoomable image).
- Organizer.kt: page organizer (thumbnail grid, select, move, rotate, duplicate, delete, add PDFs/images, export via `Jobs`).
- Exe.kt + app/src/main/python/{flip_exe_to_pdf.py, exe_bridge.py}: exe flip-book converter. Kotlin calls
  `exe_bridge.run(src, dst, level, cb)` through Chaquopy; `cb` is the public `ExeCallback` class (progress/cancelled).
  flip_exe_to_pdf.py is the owner's tested module: keep it identical to reference/flip_exe_to_pdf.py.
- History.kt: persisted recent results (SharedPreferences JSON, max 30 files / 1.5 GB, files live in filesDir/out/<stamp>), Home 'recent' section and HistoryScreen.
- Options can be conditional: `Opt.showIf`. Compression tools share `targetOpts()` (mode level|target, mb, mbc) and `Ctx.isTarget()/targetMb()`;
  PDF target = binary search over `PDF_LEVELS`, video target = bitrate from duration (+1 retry), audio target = bitrate clamp 16-320k. `Tool.compare` shows before/after size.
- Organizer supports drag-and-drop (grid-level long-press drag, swaps as the pointer crosses items) and blank pages (`OPage.blank`).
- OCR skips pages that already have text (PdfBox) and shows ETA.
- Prefs.kt: persisted settings (motion 0/1/2, language ar/en, font system|cairo|plex|mono, text scale). Default motion = 1 (light).
- Fonts.kt + res/font/: bundled fonts: Cairo (static instances cut from the variable font), IBM Plex Sans Arabic, DejaVu Sans Mono.
  Mono is bundled because the owner's phone has a custom system font that also replaces `monospace`. The mono font has no Arabic glyphs (falls back to the system font).
- I18n.kt: `tr()` Arabic -> English dictionary (+ whole-word fragment replacement for dynamic labels; never replaces inside another Arabic word) and a package-level `Text`
  wrapper that shadows Material's Text and translates automatically. Use `raw = true` for file names / extracted text.
  When adding any new Arabic string, add its English to `I18n.en`. Avoid "x من y" in labels, use "x/y".
- Ui.kt: theme (RTL for ar, LTR for en, mono typography, fixed font scale), motion-aware helpers (`bounceClick`, `enter`,
  `Pop`), Home (search, category chips, settings dialog), ChooserScreen, ToolScreen (renders any Tool's options),
  `JobPanel` (progress / error / result).
- MainActivity.kt: intent handling (VIEW / SEND / SEND_MULTIPLE incl. .exe -> `Inbox`), navigation stack (`Screen`),
  transitions depend on `Prefs.motion`.
- KeepAliveService.kt: foreground service (dataSync) to keep long jobs alive.

## How to add a tool
1. Write `fun myOp(c: Ctx): List<File>` (or `suspend` for ffmpeg) in Media.kt / Pdf.kt. Use `c.s("key")`, `c.on("key")`,
   `c.inputs`, `c.out("name.ext")`, `c.progress(0..1, "label")`. Throw IllegalStateException("Arabic message") for user errors.
2. Add a `Tool(...)` entry in `TOOLS` (Tools.kt) with its options. Nothing else is needed: Home, the chooser for
   files shared from other apps, and ToolScreen pick it up automatically.

## Repo layout
`reference/` holds the Telegram bot (quickfile_bot_main.py), flip_exe_to_pdf.py and requirements.txt: reference only,
NOT part of the Android build (the workflow ignores changes there). Use them as the source of truth when porting
features (ffmpeg arguments, PyMuPDF logic, the exe flip-book converter). Never put tokens/keys in this public repo.

## Known gaps / unverified (be honest about these)
- Nothing here has ever been compiled by the assistant; the owner's GitHub build is the only compiler.
- Chaquopy needs Python 3.12 on the build machine (workflow uses actions/setup-python 3.12) and downloads Pillow wheels at build time.
  APK grows by roughly 20-30 MB because of the embedded interpreter. exe conversion itself has never been run on a device.
- ffmpeg-kit-16kb codec set (libx264, libmp3lame, libopus, libvpx) is unverified.
- Tesseract ResultIterator is not recycled per page (small native leak); PDF protect uses 128-bit via pdfbox-android (unverified).
- PDF viewer is intentionally basic (zoom re-renders after settling; text copy per page via a sheet, no in-page selection). Organizer reorders with buttons (no drag and drop).
- Not done yet: HTML to PDF, docx/pptx text extraction, audio tags editor, N-up, document scanner.

