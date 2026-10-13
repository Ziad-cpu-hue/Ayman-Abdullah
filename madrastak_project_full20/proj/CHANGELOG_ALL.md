# ملخص كل التحديثات في هذه النسخة

1. قالبا الواجهة الجديدان: templates/teacher/courses.html و templates/student/workspace.html (مع إصلاحين صغيرين). التفاصيل في UI_INTEGRATION_REPORT.md
2. الحضور والغياب (المنصة + السنتر برفع PDF + فلتر الصف + رقم الطالب ID). التفاصيل في ATTENDANCE_UPDATE.md
3. المساعد الذكي (طالب + مدرّس) مرن: دردشة طبيعية + إجابات منهجية موثّقة، وإدارة مفاتيح الذكاء الاصطناعي للمالك. التفاصيل في ASSISTANT_UPDATE.md

## بعد فك الضغط (مرة واحدة)
    python manage.py migrate
    python manage.py runserver
