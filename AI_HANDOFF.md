# QuickFile (Android): handoff notes for an AI assistant

Paste this file together with the project zip when asking an AI to change the app.

## What this is
Android app (Kotlin, Jetpack Compose Material3), 100% offline file tools: PDF (pdfbox-android + PdfRenderer),
OCR (tesseract4android, Arabic + English), media (ffmpeg-kit-16kb 6.1.1), keep-alive foreground service.
Package/namespace/applicationId: com.edunurse.quickfile. minSdk 29, compileSdk/targetSdk 34, arm64-v8a only.
Compose BOM 2024.09.03, Kotlin compose compiler extension 1.5.14, Gradle 8.7, JDK 17. UI is Arabic, forced RTL.
Only core Material icons are used (material-icons-core); tool icons are emojis.

## Source map (app/src/main/java/com/edunurse/quickfile)
- Tools.kt: data model (Group, Opt.Choice/Text/Toggle, In, Ctx, Tool), `kindOf()`, `Tool.accepts(kind)` and the
  registry `TOOLS`. Special tools (`special = "organize" | "viewer"`) open their own screens instead of ToolScreen.
- Engine.kt: Picked, JobState, `Fs` (SAF copy, MediaStore save to Download/QuickFile, FileProvider), `Jobs`
  (single global job runner with wake lock + KeepAliveService; progress via StateFlow `Jobs.state`).
- Media.kt: FFmpegKit wrapper (`FF.exec` is a cancellable suspend fun) and video/audio operations. Encoders are
  probed at runtime (`FF.encoders()`), because the fork's codec set is unverified.
- Pdf.kt: `Img` (decode/EXIF/flatten) and `Pdf` operations: merge, split (extract/delete/ranges/every), rotate,
  page numbers, watermark (text rendered with Canvas so Arabic shapes correctly), protect, unlock, compress,
  PDF to images, images to PDF, convert images, `organize` (export for the page organizer).
- Ocr.kt: Tesseract OCR to a searchable PDF (invisible text layer) + TXT. Data and font come from build-time downloads.
- Viewer.kt: `PdfDoc` (PdfRenderer wrapper with LruCache) and the light PDF viewer (zoom buttons, share, "tools on file").
- Organizer.kt: page organizer (thumbnail grid, select, move, rotate, duplicate, delete, add PDFs/images, export via `Jobs`).
- Ui.kt: theme, animations (`bounceClick`, `enter`, `Pop`), Home (search + category chips), ChooserScreen,
  ToolScreen (renders any Tool's options automatically), `JobPanel` (progress / error / result).
- MainActivity.kt: intent handling (VIEW / SEND / SEND_MULTIPLE from file managers -> `Inbox`), navigation stack
  (`Screen`) with AnimatedContent transitions.
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
- ffmpeg-kit-16kb codec set (libx264, libmp3lame, libopus, libvpx) is unverified.
- Tesseract ResultIterator is not recycled per page (small native leak); PDF protect uses 128-bit via pdfbox-android (unverified).
- Organizer reorders with buttons (no drag and drop yet). Viewer zoom is +/- buttons (no pinch).
- Not done yet: exe flip-book to PDF (reference/flip_exe_to_pdf.py is pure Python, ~900 lines: SWF vector pages,
  fonts, bitmaps, custom PDF writer; needs either a careful Kotlin port or Chaquopy, plus a sample .exe to test),
  HTML to PDF, docx/pptx text extraction, audio tags editor, N-up, document scanner.

## How it is built (IMPORTANT)
The owner has only a phone. There is NO local build. Code is pushed from Termux to GitHub and
GitHub Actions (.github/workflows/build.yml) builds the APK. So you cannot compile: be careful and conservative.
- OCR language data and the Arabic font are downloaded at build time by the workflow (not committed).
- Avoid Kotlin JVM signature clashes (a `var x` with `private set` plus `fun setX(...)`).
- Use only library versions/APIs that exist for the versions above. Do not bump versions unless asked.
- Return ONLY changed files, complete (not snippets), with paths relative to the project root, ideally as a zip.
- If the build fails, the owner sends the lines containing `e: ` from the Actions log.

## Signing and releases
Stable signing key lives in GitHub Secrets: KEYSTORE_BASE64, KEYSTORE_PASSWORD, KEY_ALIAS (quickfile), KEY_PASSWORD.
Never print, commit, or ask for them. The APK of every build is published at
github.com/Khereba1/QuickFile/releases/tag/latest (QuickFile.apk) and as the Actions artifact QuickFile-apk.
Increase versionCode in app/build.gradle.kts when releasing a meaningful update.

## Owner workflow
cd ~/QuickFile && unzip -o ~/storage/downloads/fix.zip -d . && git add . && git commit -m "msg" && git push

## Sharing the project safely
`bash share_zip.sh` (from the project folder in Termux) builds `~/storage/downloads/<folder>-for-AI.zip`
without keys, env files, build output or APKs, and refuses if it finds anything that looks like a token.

## REQUIRED answer format (the owner has only a phone + Termux)
1. Briefly say what you changed and why (the owner writes Egyptian Arabic mixed with English terms, reply in the same).
2. Deliver ONE zip containing only the changed/new files, complete (never snippets), paths relative to the project root.
   Name it `fixN.zip`. If you cannot create a zip, give each file in full in its own code block with its path, and say so.
3. Raise `versionCode` (+1) and `versionName` in `app/build.gradle.kts` for functional changes, and include that file.
4. Give the install command, ready to copy:
   `cd ~/QuickFile && unzip -o ~/storage/downloads/fixN.zip -d . && git add . && git commit -m "short message" && git push`
   (GitHub repo: Khereba1/QuickFile, APK: github.com/Khereba1/QuickFile/releases/tag/latest)
5. Say honestly that nothing was compiled or tested, and what to check first on the phone.
6. If the build fails, the owner sends lines containing `e: ` from the Actions log; fix only those.
