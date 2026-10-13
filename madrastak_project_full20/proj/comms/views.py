from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from comms.services import notify_many

from .models import Ticket, TicketReply

_MONTHS = ["يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو", "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر"]


def _home_url(user):
    if user.role == "student":
        return reverse("student:home")
    if user.role == "teacher":
        return reverse("teacher:home")
    return reverse("owner:overview")


def _fmt_time(dt):
    dt = timezone.localtime(dt)
    h = dt.hour % 12 or 12
    return f"{h}:{dt.minute:02d} {'ص' if dt.hour < 12 else 'م'}"


def _fmt_date(dt):
    d = timezone.localtime(dt).date()
    label = f"{d.day} {_MONTHS[d.month - 1]} {d.year}"
    return f"اليوم - {label}" if d == timezone.localdate() else label


def _build_thread(user, tickets):
    """يحوّل تذاكر المستخدم وردودها إلى سلسلة رسائل لواجهة المحادثة (للعرض فقط)."""
    thread, last_day = [], None

    def day_marker(dt):
        nonlocal last_day
        day = timezone.localtime(dt).date()
        if day != last_day:
            last_day = day
            thread.append({"kind": "date", "label": _fmt_date(dt)})

    for t in tickets:
        day_marker(t.created_at)
        first_line = t.message.strip().splitlines()[0] if t.message.strip() else ""
        subject = "" if (t.subject == first_line or first_line.startswith(t.subject.rstrip("…"))) else t.subject
        thread.append({"kind": "out", "text": t.message, "subject": subject, "time": _fmt_time(t.created_at)})
        for r in t.replies.all():
            day_marker(r.created_at)
            thread.append({"kind": "out" if r.author_id == t.user_id else "in", "text": r.text,
                           "subject": "", "time": _fmt_time(r.created_at)})
        if t.status == "in_progress":
            thread.append({"kind": "status", "label": "قيد المعالجة"})
        elif t.status == "resolved":
            thread.append({"kind": "status", "label": "تم حل هذه المشكلة"})
    return thread


@login_required
def ticket_list(request):
    if request.method == "POST":
        text = request.POST.get("text", "").strip()
        if not text:
            messages.error(request, "اكتب رسالتك أولاً.")
            return redirect("comms:tickets")
        latest = Ticket.objects.filter(user=request.user).order_by("-created_at", "-pk").first()
        if latest and latest.status != "resolved":
            # تذكرة مفتوحة: الرسالة تُضاف كتعليق عليها (كما كان في صفحة تفاصيل التذكرة)
            TicketReply.objects.create(ticket=latest, author=request.user, text=text)
        else:
            first = text.splitlines()[0].strip()
            subject = first if len(first) <= 60 else first[:60].rstrip() + "…"
            Ticket.objects.create(user=request.user, subject=subject, category="other", message=text)
            notify_many(User.objects.filter(role="owner"), f"تذكرة دعم جديدة: {subject}", request.user.full_name, "/owner/tickets/", "ticket")
            messages.success(request, "تم إرسال طلبك للدعم الفني، سنرد عليك قريبًا.")
        return redirect("comms:tickets")
    items = Ticket.objects.filter(user=request.user).order_by("created_at", "pk").prefetch_related("replies")
    return render(request, "support/list.html", {"items": items, "thread": _build_thread(request.user, items),
                                                 "home_url": _home_url(request.user)})


@login_required
def ticket_new(request):
    if request.method == "POST":
        subject, category, message_ = request.POST.get("subject", "").strip(), request.POST.get("category", "other"), request.POST.get("message", "").strip()
        if subject and message_:
            t = Ticket.objects.create(user=request.user, subject=subject, category=category, message=message_)
            notify_many(User.objects.filter(role="owner"), f"تذكرة دعم جديدة: {subject}", request.user.full_name, "/owner/tickets/", "ticket")
            messages.success(request, "تم إرسال طلبك للدعم الفني، سنرد عليك قريبًا.")
            return redirect("comms:tickets")
        messages.error(request, "اكتب عنوانًا وتفاصيل المشكلة.")
    # الإرسال صار من صفحة الدعم الجديدة مباشرة
    return redirect("comms:tickets")


@login_required
def ticket_detail(request, pk):
    t = get_object_or_404(Ticket, pk=pk, user=request.user)
    if request.method == "POST":
        text = request.POST.get("text", "").strip()
        if text and t.status != "resolved":
            TicketReply.objects.create(ticket=t, author=request.user, text=text)
    # عرض المحادثة كاملة في صفحة الدعم الجديدة
    return redirect("comms:tickets")
