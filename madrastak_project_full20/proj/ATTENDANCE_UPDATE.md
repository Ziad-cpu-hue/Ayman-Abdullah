# تحديث: نظام الحضور والغياب (المنصة + السنتر)

## طريقة التركيب
1. انسخ محتويات هذا الملف المضغوط فوق جذر المشروع (بنفس المسارات) ووافق على استبدال الملفات الموجودة.
2. شغّل:  python manage.py migrate
   (يمنح الطلاب الحاليين أرقامًا تسلسلية 1، 2، 3... بترتيب تاريخ تسجيلهم، وينشئ جدول حضور السنتر.)
3. لا مكتبات جديدة. قراءة الـ PDF الممسوح ضوئيًا تحتاج Poppler وTesseract (نفس متطلبات استيراد الاختبارات).
   للخط اليدوي الأدق: ضع مفتاح Groq أو Anthropic في إعدادات المنصة (لوحة المالك).

## الملفات الجديدة
- teacher/center_service.py، teacher/center_views.py
- templates/teacher/center_attendance.html
- accounts/migrations/0003_student_number.py، learning/migrations/0006_center_attendance.py

## الملفات المعدّلة (تعديلات صغيرة)
- accounts/models.py: حقل student_number + توليده تلقائيًا + الصفوف الإعدادية.
- accounts/views.py: رقم الطالب في رسالة الترحيب.
- learning/models.py: نموذج CenterAttendance (جدول مستقل، لا يمس Attendance القديم).
- teacher/urls.py: مسار /teacher/attendance/center/
- teacher/views.py: فلتر الصف (اختياري) في صفحة حضور المنصة.
- templates/teacher/attendance.html: تبويب المنصة/السنتر + أزرار الصف + رقم الطالب.
- templates/student/profile.html: عرض رقم الطالب.
