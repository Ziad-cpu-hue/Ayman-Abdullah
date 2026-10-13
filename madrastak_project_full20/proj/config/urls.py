from django.conf import settings
from django.conf.urls.static import static
from django.urls import include, path

from accounts import views as account_views

urlpatterns = [
    path("", include("accounts.urls")),
    # الواجهة الجديدة (صفحات مساندة للطالب/المدرّس) — تُسجَّل قبل student/ و teacher/
    path("", include("uikit.urls")),
    path("support/", include("comms.urls")),
    #   (/ ) :     
    path("", include("assistant.urls")),
    path("student/", include("student.urls")),
    # يُسجَّل هنا (لا داخل teacher/urls.py) حتى يبقى "examimport" مساحة اسم (namespace)
    # على المستوى الأعلى، فتعمل {% url 'examimport:...' %} في القوالب مباشرة دون تعشيش.
    path("teacher/import/", include("examimport.urls")),
    path("teacher/", include("teacher.urls")),
    path("owner/", include("owner.urls")),
    # صفحة المدرّس العامة برابطه الخاص: يجب أن تبقى في النهاية
    path("<slug:slug>/", account_views.teacher_public, name="teacher_public"),
]
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
handler404 = "accounts.views.error_404"
