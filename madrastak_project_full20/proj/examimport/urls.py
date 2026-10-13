from django.urls import path

from . import views as v

app_name = "examimport"

urlpatterns = [
    path("quiz/<int:quiz_pk>/new/", v.batch_new, name="batch_new"),
    path("batch/<int:pk>/", v.batch_review, name="batch_review"),
    path("batch/<int:pk>/apply/", v.batch_apply, name="batch_apply"),
    path("batch/<int:pk>/reprocess/", v.batch_reprocess, name="batch_reprocess"),
    path("file/<int:pk>/", v.batch_file, name="batch_file"),
    path("question/<int:pk>/edit/", v.question_edit, name="question_edit"),
    path("question/<int:pk>/reject/", v.question_reject, name="question_reject"),
]
