# QuickFile (Android): handoff notes for an AI assistant

Paste this file together with the project zip when asking an AI to change the app.

## What this is
Android app (Kotlin, Jetpack Compose Material3) with file tools: PDF (pdfbox-android), OCR
(tesseract4android, Arabic + English), media conversion (ffmpeg-kit-16kb 6.1.1), keep-alive service.
Package/namespace/applicationId: com.edunurse.quickfile. minSdk 29, compileSdk/targetSdk 34, arm64-v8a only.
Compose BOM 2024.09.03, Kotlin compose compiler extension 1.5.14, Gradle 8.7, JDK 17.
Source files (app/src/main/java/com/edunurse/quickfile): Engine.kt, KeepAliveService.kt, MainActivity.kt,
Media.kt, Ocr.kt, Pdf.kt, Tools.kt, Ui.kt. (Roles inferred from names: read the code before editing.)
The repo root also has main.py, flip_exe_to_pdf.py, requirements.txt and an `env` file: these are NOT part of the
Android build. Never put real tokens/keys into `env` or any file in this public repo.

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
