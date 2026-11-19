#!/bin/bash
# qf:name=ضغط PDF (سكريبت مثال)
# qf:emoji=📦
# qf:desc=مثال: بيضغط الـ PDF بـ Ghostscript
# qf:accept=pdf
# qf:multi=true
# qf:arg=الجودة (screen / ebook / printer)|ebook
#
# حط الملف ده في مجلد scripts جوه مجلد التبادل، واضغط «تحديث أدوات السكريبتات».
# السكريبت بيشتغل جوه مجلد فيه: in/ (المدخلات) و out/ (المخرجات).
# قيمة الخانة بتوصل في المتغير ARG.
for f in in/*; do
  b="$(basename "$f")"
  gs -sDEVICE=pdfwrite -dPDFSETTINGS=/"$ARG" -dNOPAUSE -dQUIET -dBATCH -sOutputFile="out/$b" "$f"
done
