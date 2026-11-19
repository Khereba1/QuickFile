P="$(command -v pandoc)"
if [ -z "$P" ]; then
  echo "pandoc is not installed in this environment"
  exit 3
fi
T="@TO@"
EXT="$T"
SA=""
[ "$T" = "html" ] && SA="-s"
case "$T" in
  md) FMT=gfm ;;
  txt) FMT=plain ;;
  *) FMT="$T" ;;
esac
rc=0
for f in in/*; do
  b="$(basename "$f")"
  echo "pandoc: $b"
  "$P" $SA "$f" -t "$FMT" -o "out/${b%.*}.$EXT" || rc=1
done
exit $rc
