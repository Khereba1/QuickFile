O="$(command -v ocrmypdf)"
if [ -z "$O" ]; then
  echo "ocrmypdf is not installed in this environment"
  exit 3
fi
rc=0
for f in in/*; do
  b="$(basename "$f")"
  echo "ocr: $b"
  "$O" -l @LANG@ --skip-text --optimize 1 "$f" "out/${b%.*}_ocr.pdf" || rc=1
done
exit $rc
