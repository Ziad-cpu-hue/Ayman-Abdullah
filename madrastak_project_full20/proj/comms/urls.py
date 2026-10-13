from django.urls import path
from . import views

app_name = "comms"
urlpatterns = [
    path("", views.ticket_list, name="tickets"),
    path("new/", views.ticket_new, name="ticket_new"),
    path("<int:pk>/", views.ticket_detail, name="ticket_detail"),
]
