# QuickFile (Android, offline)

1. ارفع محتوى المجلد على ريبو GitHub (ممكن Public، مفيش أسرار في الكود).
2. تاب **Actions** ← Build APK ← بعد ما يخلص نزّل الـ artifact `QuickFile-apk`.
3. ثبّت الـ APK على الموبايل.

ملاحظات:
- الـ workflow بينزّل بيانات OCR (ara/eng من tessdata_fast) وخط Noto Naskh Arabic وقت البناء. لو بنيت محليًا حطهم في `app/src/main/assets/` (`tessdata/ara.traineddata`, `tessdata/eng.traineddata`, `NotoNaskhArabic.ttf`).
- لدقة OCR أعلى استبدل tessdata_fast بـ tessdata_best في build.yml (أدق وأبطأ وأكبر).
- مكتبة FFmpeg: `com.moizhassan.ffmpeg:ffmpeg-kit-16kb:6.1.1`. التطبيق بيفحص الـ encoders المتاحة وقت التشغيل وبيدّي رسالة واضحة لو صيغة مش مدعومة.
- التوقيع: من غير Secrets بيتوقّع بمفتاح مؤقت (التحديث لازم يتثبت بعد حذف القديم). عشان التحديثات تتثبت فوق بعض ضيف Secrets: KEYSTORE_BASE64 و KEYSTORE_PASSWORD و KEY_ALIAS و KEY_PASSWORD.

## تحديث 1.1
- فتح ومشاركة الملفات من مدير الملفات (PDF / صور / فيديو / صوت) وبتظهر الأدوات المناسبة لنوع الملف.
- قارئ PDF خفيف + تنظيم الصفحات (ترتيب، حذف، تدوير، تكرار، إضافة صور وملفات).
- أدوات PDF جديدة: حذف صفحات، تقسيم لمجموعات، تدوير، ترقيم، علامة مائية، حماية بباسورد.
- واجهة بأنيميشن وبحث وتصنيفات، وأيقونة جديدة.
