{
  echo "arch: $(uname -m)"
  for b in soffice libreoffice pandoc gs; do
    p="$(command -v $b)"
    echo "$b: ${p:--}"
  done
  soffice --version 2>/dev/null | head -1
} > out/check.txt
