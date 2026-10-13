from django.urls import path
from django.views.generic import RedirectView

from . import views_student as vs
from . import views_teacher as vt

app_name = "uikit"

urlpatterns = [
    # ---- الطالب
    path("student/content/", vs.content_entry, name="student_content"),
    path("student/performance/", vs.performance, name="student_performance"),
    path("student/search/", vs.search, name="student_search"),
    path("student/t/<str:code>/lesson/<int:pk>/video/", vs.lesson_video, name="lesson_video"),
    path("student/t/<str:code>/lesson/<int:pk>/tests/", vs.lesson_tests, name="lesson_tests"),
    path("student/t/<str:code>/lesson/<int:pk>/exercises/", vs.lesson_exercises, name="lesson_exercises"),
    # ---- المدرّس
    # صفحة «محتوى المنهج» القديمة أُلغيت: العنوان القديم يحوّل مباشرة إلى قائمة الدورات
    path("teacher/curriculum/", RedirectView.as_view(pattern_name="teacher:courses", permanent=False)),
    path("teacher/content/", vt.content_manage, name="content_manage"),
    path("teacher/content/delete/", vt.content_delete, name="content_delete"),
    path("teacher/create/unit/", vt.unit_wizard, name="unit_wizard"),
    path("teacher/create/quiz/", vt.quiz_wizard, name="quiz_wizard"),
    path("teacher/ai/vocab/", vt.ai_vocab, name="ai_vocab_pick"),
    path("teacher/ai/vocab/<int:lesson_pk>/", vt.ai_vocab, name="ai_vocab"),
    path("teacher/ai/vocab/<int:lesson_pk>/enrich/", vt.ai_vocab_enrich, name="ai_vocab_enrich"),
    path("teacher/ai/script/", vt.ai_script, name="ai_script_pick"),
    path("teacher/ai/script/<int:lesson_pk>/", vt.ai_script, name="ai_script"),
    path("teacher/students-hub/", vt.students_hub, name="students_hub"),
    path("teacher/reports/", vt.student_reports, name="student_reports"),
    path("teacher/search/", vt.search, name="teacher_search"),
]
