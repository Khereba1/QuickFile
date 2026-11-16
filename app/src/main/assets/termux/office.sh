SOFF="$(command -v soffice || command -v libreoffice)"
if [ -z "$SOFF" ]; then
  echo "LibreOffice is not installed in this environment"
  exit 3
fi
PROFILE_DIR="${HOME:-/tmp}/.cache/qf_lo_profile"
mkdir -p "$PROFILE_DIR"
PROFILE="file://$PROFILE_DIR"
rc=0
for f in in/*; do
  echo "convert: $f"
  "$SOFF" --headless "-env:UserInstallation=$PROFILE" @INFILTER@ --convert-to @TARGET@ --outdir "$PWD/out" "$PWD/$f" || rc=1
done
exit $rc
