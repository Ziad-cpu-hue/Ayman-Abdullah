from django.urls import path
from . import views as v

app_name = "student"
urlpatterns = [
    path("", v.home, name="home"),
    path("teachers/", v.teachers, name="teachers"),
    path("join/", v.join, name="join"),
    path("subscriptions/", v.subscriptions, name="subscriptions"),
    path("notifications/", v.notifications, name="notifications"),
    path("notifications/read-all/", v.notifications_read_all, name="notifications_read_all"),
    path("notifications/<int:pk>/", v.notification_go, name="notification_go"),
    path("profile/", v.profile, name="profile"),
    path("file/<int:pk>/", v.lesson_file, name="lesson_file"),
    path("video/<int:pk>/", v.lesson_video, name="lesson_video"),
    path("save/<int:pk>/", v.bank_save, name="bank_save"),
    # مساحة المدرّس الحالي
    path("t/<str:code>/", v.workspace, name="workspace"),
    path("t/<str:code>/subscribe/", v.subscribe, name="subscribe"),
    path("t/<str:code>/course/<int:pk>/", v.course, name="course"),
    path("t/<str:code>/unit/<int:pk>/", v.unit, name="unit"),
    path("t/<str:code>/lesson/<int:pk>/", v.lesson, name="lesson"),
    path("t/<str:code>/lesson/<int:pk>/complete/", v.lesson_complete, name="lesson_complete"),
    path("t/<str:code>/quiz/<int:pk>/", v.quiz, name="quiz"),
    path("t/<str:code>/attempt/<int:pk>/", v.result, name="result"),
    path("t/<str:code>/assignments/", v.assignments, name="assignments"),
    path("t/<str:code>/bank/", v.bank, name="bank"),
    path("t/<str:code>/bank/start/", v.bank_start, name="bank_start"),
    path("t/<str:code>/bank/q/<int:pk>/", v.bank_question, name="bank_question"),
    path("t/<str:code>/mistakes/", v.mistakes, name="mistakes"),
    path("t/<str:code>/mistakes/<int:pk>/reviewed/", v.mistake_reviewed, name="mistake_reviewed"),
    path("t/<str:code>/progress/", v.progress, name="progress"),
    path("t/<str:code>/vocabulary/", v.vocabulary, name="vocabulary"),
    path("t/<str:code>/vocabulary/record/", v.vocab_record, name="vocab_record"),
    path("t/<str:code>/messages/", v.messages_view, name="messages"),
    path("t/<str:code>/announcements/", v.announcements, name="announcements"),
]
