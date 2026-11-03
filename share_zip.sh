#!/data/data/com.termux/files/usr/bin/bash
# يعمل ملف zip نضيف من المشروع علشان تبعته لموديل AI.
# بيستبعد: المفاتيح وملفات env وملفات البناء والـ APK، وبيفحص الباقي لو فيه أي سر.
# الاستخدام (من جوه مجلد المشروع):  bash share_zip.sh
set -e
cd "$(dirname "$0")"
NAME=$(basename "$PWD")
command -v zip >/dev/null 2>&1 || pkg install -y zip
command -v unzip >/dev/null 2>&1 || pkg install -y unzip
mkdir -p "$HOME/storage/downloads" 2>/dev/null || true
OUT="$HOME/storage/downloads/${NAME}-for-AI.zip"
rm -f "$OUT"
zip -qr "$OUT" . \
  -x ".git/*" "*/.git/*" "build/*" "*/build/*" ".gradle/*" "*/.gradle/*" ".idea/*" \
     "*.jks" "*.keystore" "*.p12" "*.pem" "*.apk" "*.aab" \
     "env" ".env" "*.env" "local.properties" "signing-secrets.txt" "*.log" \
     "*KEY-BACKUP*" "app/src/main/assets/tessdata/*" "app/src/main/assets/*.ttf"
# فحص أخير: لو لقى حاجة شكلها سر يمسح الملف ويقولك
if unzip -p "$OUT" 2>/dev/null | grep -aEq 'ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|BEGIN [A-Z ]*PRIVATE KEY|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{30}|[0-9]{8,10}:[A-Za-z0-9_-]{35}'; then
  rm -f "$OUT"
  echo "تحذير: لقيت حاجة شكلها مفتاح/توكن جوه الملفات، فمسحت الـ zip. شوفها بـ:"
  echo "  grep -rnE 'ghp_|github_pat_|PRIVATE KEY|AKIA|AIza' . --exclude-dir=.git"
  exit 1
fi
echo "تم ✅  $OUT"
ls -lh "$OUT"
echo "الملف ده نضيف: ابعته للـ AI مع AI_HANDOFF.md"
