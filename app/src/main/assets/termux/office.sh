SOFF="$(command -v soffice || command -v libreoffice)"
if [ -z "$SOFF" ]; then
  echo "LibreOffice is not installed in this environment"
  exit 3
fi
PROFILE="file://${TMPDIR:-/tmp}/qf_lo_profile"
rc=0
for f in in/*; do
  echo "convert: $f"
  "$SOFF" --headless "-env:UserInstallation=$PROFILE" @INFILTER@ --convert-to @TARGET@ --outdir out "$f" || rc=1
done
exit $rc
