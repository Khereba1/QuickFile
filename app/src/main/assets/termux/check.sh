{
  echo "arch: $(uname -m)"
  for b in soffice libreoffice pandoc gs; do
    p="$(command -v $b)"
    echo "$b: ${p:--}"
  done
  soffice --version 2>/dev/null | head -1
  if command -v dpkg >/dev/null 2>&1; then
    echo "modules: $(dpkg -l 2>/dev/null | awk '/^ii/ && $2 ~ /^libreoffice-(writer|impress|calc|draw)$/ {print $2}' | sed 's/libreoffice-//' | tr '\n' ' ')"
  fi
} > out/check.txt
