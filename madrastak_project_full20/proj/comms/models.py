from django.conf import settings
from django.db import models
from django.utils import timezone

from accounts.models import TeacherProfile

User = settings.AUTH_USER_MODEL


class Notification(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="notifications")
    kind = models.CharField(max_length=20, default="info")
    title = models.CharField(max_length=200)
    body = models.CharField(max_length=400, blank=True)
    url = models.CharField(max_length=300, blank=True)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class Announcement(models.Model):
    AUDIENCE = [("all", "الجميع"), ("students", "كل الطلاب"), ("teachers", "كل المدرّسين")]
    author = models.ForeignKey(User, on_delete=models.CASCADE, related_name="announcements")
    teacher = models.ForeignKey(TeacherProfile, null=True, blank=True, on_delete=models.CASCADE, related_name="announcements",
                                help_text="إن كان فارغًا فالإعلان من المنصة")
    audience = models.CharField(max_length=10, choices=AUDIENCE, default="students")
    title = models.CharField("العنوان", max_length=200)
    body = models.TextField("النص")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class Conversation(models.Model):
    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name="conversations")
    teacher = models.ForeignKey(TeacherProfile, on_delete=models.CASCADE, related_name="conversations")
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        unique_together = ("student", "teacher")
        ordering = ["-updated_at"]


class ChatMessage(models.Model):
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="messages")
    sender = models.ForeignKey(User, on_delete=models.CASCADE, related_name="+")
    text = models.TextField("الرسالة", blank=True)
    image = models.ImageField("صورة", upload_to="chat/", blank=True)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]


class Ticket(models.Model):
    CATEGORIES = [("payment", "مشكلة في الدفع"), ("access", "مشكلة في الدخول"), ("content", "مشكلة في المحتوى"),
                  ("technical", "مشكلة تقنية"), ("other", "أخرى")]
    STATUS = [("new", "جديدة"), ("in_progress", "قيد المعالجة"), ("resolved", "محلولة")]
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="tickets")
    subject = models.CharField("عنوان المشكلة", max_length=200)
    category = models.CharField("النوع", max_length=12, choices=CATEGORIES, default="other")
    message = models.TextField("التفاصيل")
    status = models.CharField(max_length=12, choices=STATUS, default="new")
    assignee = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="assigned_tickets")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]


class TicketReply(models.Model):
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="replies")
    author = models.ForeignKey(User, on_delete=models.CASCADE, related_name="+")
    text = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
