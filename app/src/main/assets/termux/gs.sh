GS="$(command -v gs)"
if [ -z "$GS" ]; then
  echo "Ghostscript is not installed in this environment"
  exit 3
fi
rc=0
for f in in/*; do
  b="$(basename "$f")"
  echo "gs: $b"
  "$GS" -sDEVICE=pdfwrite -dCompatibilityLevel=1.5 -dPDFSETTINGS=/@Q@ -dNOPAUSE -dQUIET -dBATCH -sOutputFile="out/$b" "$f" || rc=1
done
exit $rc
