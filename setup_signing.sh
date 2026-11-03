#!/data/data/com.termux/files/usr/bin/bash
# ينشئ مفتاح توقيع ثابت للتطبيق ويرفعه كأسرار (Secrets) في GitHub.
# الاستخدام:  bash setup_signing.sh اسم-حسابك/اسم-الريبو [اسم-المفتاح]
# مثال:       bash setup_signing.sh Khereba1/QuickFile quickfile
set -e
REPO="$1"
NAME="$2"
if [ -z "$REPO" ]; then
  echo "اكتب اسم الريبو هكذا:  bash setup_signing.sh Khereba1/QuickFile quickfile"
  exit 1
fi
if [ -z "$NAME" ]; then NAME=$(echo "${REPO##*/}" | tr 'A-Z' 'a-z'); fi
command -v keytool >/dev/null 2>&1 || { echo "تثبيت Java (لمرة واحدة)..."; pkg install -y openjdk-17; }
command -v gh >/dev/null 2>&1 || pkg install -y gh

DIR="$HOME/${NAME}-key"
KS="$DIR/${NAME}.jks"
mkdir -p "$DIR"
if [ -f "$KS" ]; then
  echo "المفتاح موجود بالفعل في $KS ومش هعمل غيره (عشان ما يضيعش القديم)."
  exit 1
fi

PASS=$(head -c 48 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 24)
ALIAS="$NAME"

keytool -genkeypair -keystore "$KS" -alias "$ALIAS" -keyalg RSA -keysize 2048 \
  -validity 36500 -storepass "$PASS" -keypass "$PASS" \
  -dname "CN=Khereba1, O=Khereba1, C=EG"

printf '%s' "$PASS" > "$DIR/password.txt"
printf '%s' "$ALIAS" > "$DIR/alias.txt"

base64 -w0 "$KS" | gh secret set KEYSTORE_BASE64 -R "$REPO"
printf '%s' "$PASS" | gh secret set KEYSTORE_PASSWORD -R "$REPO"
printf '%s' "$ALIAS" | gh secret set KEY_ALIAS -R "$REPO"
printf '%s' "$PASS" | gh secret set KEY_PASSWORD -R "$REPO"

if [ -d "$HOME/storage/downloads" ]; then
  B="$HOME/storage/downloads/${NAME}-KEY-BACKUP"
  mkdir -p "$B"
  cp "$KS" "$DIR/password.txt" "$DIR/alias.txt" "$B/"
  echo "تم حفظ نسخة احتياطية في: Download/${NAME}-KEY-BACKUP"
fi
echo
echo "تمام ✅ المفتاح اتحفظ في GitHub Secrets. انقل مجلد النسخة الاحتياطية لمكان آمن (Drive مثلاً)."
