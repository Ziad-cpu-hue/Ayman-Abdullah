import secrets
from datetime import timedelta

from django.contrib import messages
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.decorators import PERMISSION_GROUPS, CONFIGURABLE_PERMS, default_staff_perms, has_perm, owner_required
from accounts.models import AuditLog, ContactMessage, LoginAttempt, SiteSetting, TeacherProfile, Testimonial, User
from accounts.pagination import paginate
from accounts.utils import audit, serve_private
from billing.models import Enrollment, Payment
from billing.services import activate_enrollment, reject_payment
from comms.models import Announcement, Notification, Ticket, TicketReply
from comms.services import notify, notify_many
from learning.models import Attempt, Course, ErrorRecord, Lesson, LessonProgress, Quiz

from .forms import SiteSettingForm, StaffCreateForm, TeacherCreateForm, TeacherEditForm


# ------------------------------------------------------------------ نظرة عامة
@owner_required("overview")
def overview(request):
    now = timezone.now()
    since30 = now - timedelta(days=30)
    since7 = now - timedelta(days=7)
    stats = {
        "students": User.objects.filter(role="student").count(),
        "teachers": TeacherProfile.objects.count(),
        "active_teachers": TeacherProfile.objects.filter(user__is_active=True).count(),
        "active_enr": Enrollment.objects.filter(status="active", expires_at__gt=now).count(),
        "new_enr_month": Enrollment.objects.filter(created_at__gte=since30).count(),
        "revenue_month": Payment.objects.filter(status="approved", reviewed_at__gte=since30).aggregate(s=Sum("amount"))["s"] or 0,
        "revenue_today": Payment.objects.filter(status="approved", reviewed_at__date=now.date()).aggregate(s=Sum("amount"))["s"] or 0,
        "lessons": Lesson.objects.filter(status="published").count(),
        "quizzes": Quiz.objects.filter(status="published").count(),
        "pending_payments": Payment.objects.filter(status="pending").count(),
        "open_tickets": Ticket.objects.exclude(status="resolved").count(),
        "new_students_week": User.objects.filter(role="student", date_joined__gte=since7).count(),
    }
    signups = []
    for i in range(6, -1, -1):
        d = (now - timedelta(days=i)).date()
        signups.append({"l": d.strftime("%d/%m"), "p": User.objects.filter(role="student", date_joined__date=d).count()})
    recent_students = User.objects.filter(role="student").order_by("-date_joined")[:6]
    recent_payments = Payment.objects.filter(status="approved").select_related("enrollment__student", "enrollment__teacher__user").order_by("-reviewed_at")[:6]
    pending = Payment.objects.filter(status="pending").select_related("enrollment__student", "enrollment__teacher__user").order_by("created_at")[:6]
    alerts = []
    if stats["pending_payments"]:
        alerts.append(f"يوجد {stats['pending_payments']} طلب اشتراك بانتظار المراجعة.")
    if stats["open_tickets"]:
        alerts.append(f"يوجد {stats['open_tickets']} تذكرة دعم مفتوحة.")
    inactive_teachers = TeacherProfile.objects.filter(user__is_active=False).count()
    if inactive_teachers:
        alerts.append(f"يوجد {inactive_teachers} حساب مدرّس موقوف.")
    return render(request, "owner/overview.html", {"stats": stats, "signups": signups, "recent_students": recent_students,
                                                   "recent_payments": recent_payments, "pending": pending, "alerts": alerts})


# ------------------------------------------------------------------ المدرّسون
@owner_required("teachers")
def teachers(request):
    qs = TeacherProfile.objects.select_related("user").annotate(
        n_students=Count("enrollments", filter=Q(enrollments__status="active", enrollments__expires_at__gt=timezone.now())),
        n_lessons=Count("courses__units__lessons", distinct=True))
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(user__full_name__icontains=q) | Q(subject__icontains=q) | Q(teacher_code__icontains=q) | Q(user__phone__icontains=q))
    page, qstr = paginate(request, qs.order_by("-created_at"), 20)
    return render(request, "owner/teachers.html", {"page": page, "qs": qstr, "q": q})


@owner_required("teachers")
def teacher_new(request):
    form = TeacherCreateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        t = form.save()
        audit(request, "إضافة مدرّس", t.teacher_code, t.name)
        messages.success(request, f"تم إنشاء حساب المدرّس. رقم المدرّس (Teacher ID): {t.teacher_code}")
        return redirect("owner:teacher_detail", pk=t.pk)
    return render(request, "owner/teacher_form.html", {"form": form, "is_new": True})


@owner_required("teachers")
def teacher_detail(request, pk):
    t = get_object_or_404(TeacherProfile.objects.select_related("user"), pk=pk)
    now = timezone.now()
    enr = Enrollment.objects.filter(teacher=t).select_related("student")
    stats = {"students": enr.values("student").distinct().count(), "active": enr.filter(status="active", expires_at__gt=now).count(),
             "courses": Course.objects.filter(teacher=t).count(), "lessons": Lesson.objects.filter(unit__course__teacher=t).count(),
             "revenue": Payment.objects.filter(status="approved", enrollment__teacher=t).aggregate(s=Sum("amount"))["s"] or 0}
    return render(request, "owner/teacher_detail.html", {"t": t, "stats": stats, "recent_enr": enr.order_by("-created_at")[:10]})


@owner_required("teachers")
def teacher_edit(request, pk):
    t = get_object_or_404(TeacherProfile, pk=pk)
    form = TeacherEditForm(request.POST or None, instance=t)
    if request.method == "POST" and form.is_valid():
        form.save()
        audit(request, "تعديل بيانات مدرّس", t.teacher_code)
        messages.success(request, "تم الحفظ.")
        return redirect("owner:teacher_detail", pk=t.pk)
    return render(request, "owner/teacher_form.html", {"form": form, "is_new": False, "t": t})


@owner_required("teachers")
@require_POST
def teacher_toggle(request, pk):
    t = get_object_or_404(TeacherProfile, pk=pk)
    t.user.is_active = not t.user.is_active
    t.user.save(update_fields=["is_active"])
    audit(request, "إيقاف مدرّس" if not t.user.is_active else "تفعيل مدرّس", t.teacher_code)
    messages.success(request, "تم تحديث حالة الحساب.")
    return redirect("owner:teacher_detail", pk=t.pk)


@owner_required("teachers")
@require_POST
def teacher_reset_password(request, pk):
    t = get_object_or_404(TeacherProfile, pk=pk)
    if not has_perm(request.user, "teacher_passwords"):
        messages.error(request, "ليست لديك صلاحية تغيير كلمات مرور المدرّسين.")
        return redirect("owner:teacher_detail", pk=t.pk)
    newpass = request.POST.get("password", "").strip()
    if len(newpass) < 6:
        messages.error(request, "كلمة المرور قصيرة جدًا.")
    else:
        t.user.set_password(newpass)
        t.user.save()
        audit(request, "إعادة تعيين كلمة مرور مدرّس", t.teacher_code)
        messages.success(request, "تم تغيير كلمة مرور المدرّس.")
    return redirect("owner:teacher_detail", pk=t.pk)


# ------------------------------------------------------------------ الطلاب
@owner_required("students")
def students(request):
    qs = User.objects.filter(role="student").select_related("student_profile").annotate(
        n_teachers=Count("enrollments", distinct=True), n_active=Count("enrollments", filter=Q(enrollments__status="active", enrollments__expires_at__gt=timezone.now())))
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(full_name__icontains=q) | Q(name_ar__icontains=q) | Q(phone__icontains=q))
    page, qstr = paginate(request, qs.order_by("-date_joined"), 25)
    return render(request, "owner/students.html", {"page": page, "qs": qstr, "q": q})


def _student_ctx(s, **extra):
    enr = Enrollment.objects.filter(student=s).select_related("teacher__user")
    tickets = Ticket.objects.filter(user=s)
    return {"s": s, "enr": enr, "tickets": tickets, **extra}


@owner_required("students")
def student_detail(request, pk):
    s = get_object_or_404(User.objects.select_related("student_profile"), pk=pk, role="student")
    return render(request, "owner/student_detail.html", _student_ctx(s))


@owner_required("students")
@require_POST
def student_toggle(request, pk):
    s = get_object_or_404(User, pk=pk, role="student")
    s.is_active = not s.is_active
    s.save(update_fields=["is_active"])
    audit(request, "إيقاف طالب" if not s.is_active else "تفعيل طالب", s.phone)
    messages.success(request, "تم تحديث حالة الحساب.")
    return redirect("owner:student_detail", pk=s.pk)


def _temp_password(n=8):
    """كلمة مرور مؤقتة سهلة القراءة (بدون حروف متشابهة مثل l/1 وo/0)."""
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
    return "".join(secrets.choice(alphabet) for _ in range(n))


@owner_required("students")
@require_POST
def student_reset_password(request, pk):
    """تغيير كلمة مرور طالب: يدويًا، أو توليد كلمة مؤقتة تُعرض مرة واحدة فقط.

    كلمات المرور تُخزَّن مشفّرة (hash) ولا يمكن لأحد — ولا للمالك — قراءة الكلمة القديمة؛ الحل الصحيح للنسيان هو
    تعيين كلمة جديدة وتسليمها للطالب. نفك أيضًا إيقاف الدخول المؤقت (بعد المحاولات الخاطئة) ليدخل فورًا.
    """
    s = get_object_or_404(User.objects.select_related("student_profile"), pk=pk, role="student")
    if not has_perm(request.user, "student_passwords"):
        messages.error(request, "ليست لديك صلاحية تغيير كلمات مرور الطلاب.")
        return redirect("owner:student_detail", pk=s.pk)
    generate = request.POST.get("action") == "generate"
    newpass = _temp_password() if generate else request.POST.get("password", "").strip()
    if len(newpass) < 6:
        messages.error(request, "كلمة المرور قصيرة جدًا.")
        return redirect("owner:student_detail", pk=s.pk)
    s.set_password(newpass)
    s.save(update_fields=["password"])
    from django.conf import settings as dj_settings
    since = timezone.now() - timedelta(minutes=dj_settings.LOGIN_LOCK_MINUTES)
    LoginAttempt.objects.filter(phone=s.phone, success=False, created_at__gte=since).delete()
    audit(request, "توليد كلمة مرور مؤقتة لطالب" if generate else "إعادة تعيين كلمة مرور طالب", s.phone)
    if generate:
        # تُعرض في الاستجابة نفسها ولا تُخزَّن في أي مكان (لا رسائل ولا جلسة ولا سجل)
        return render(request, "owner/student_detail.html", _student_ctx(s, new_password=newpass))
    messages.success(request, "تم تغيير كلمة المرور.")
    return redirect("owner:student_detail", pk=s.pk)


# ------------------------------------------------------------------ التعيينات (طالب × مدرّس)
@owner_required("enrollments")
def enrollments(request):
    qs = Enrollment.objects.select_related("student", "teacher__user")
    t = request.GET.get("teacher", "")
    if t.isdigit():
        qs = qs.filter(teacher_id=int(t))
    status = request.GET.get("status", "")
    now = timezone.now()
    if status == "active":
        qs = qs.filter(status="active", expires_at__gt=now)
    elif status == "expired":
        qs = qs.filter(status="active", expires_at__lte=now)
    elif status:
        qs = qs.filter(status=status)
    page, qstr = paginate(request, qs.order_by("-created_at"), 30)
    return render(request, "owner/enrollments.html", {"page": page, "qs": qstr, "teachers": TeacherProfile.objects.select_related("user"), "t": t, "status": status})


@owner_required("enrollments")
def enrollment_manual(request):
    if request.method == "POST":
        sid, tid = request.POST.get("student"), request.POST.get("teacher")
        student = User.objects.filter(pk=sid, role="student").first()
        teacher = TeacherProfile.objects.filter(pk=tid).first()
        if not student or not teacher:
            messages.error(request, "يجب اختيار طالب ومدرّس صحيحين.")
        else:
            enr, _ = Enrollment.objects.get_or_create(student=student, teacher=teacher)
            activate_enrollment(enr, request.user)
            audit(request, "تفعيل يدوي (بدون دفع)", f"{student.phone} ← {teacher.teacher_code}")
            messages.success(request, "تم تفعيل الاشتراك يدويًا.")
        return redirect("owner:enrollments")
    return render(request, "owner/enrollment_manual.html", {"students": User.objects.filter(role="student"), "teachers": TeacherProfile.objects.select_related("user")})


# ------------------------------------------------------------------ المدفوعات
@owner_required("payments")
def payments(request):
    qs = Payment.objects.select_related("enrollment__student", "enrollment__teacher__user")
    status = request.GET.get("status", "pending")
    if status:
        qs = qs.filter(status=status)
    page, qstr = paginate(request, qs.order_by("created_at" if status == "pending" else "-created_at"), 25)
    counts = {"pending": Payment.objects.filter(status="pending").count(), "approved": Payment.objects.filter(status="approved").count(),
             "rejected": Payment.objects.filter(status="rejected").count()}
    return render(request, "owner/payments.html", {"page": page, "qs": qstr, "status": status, "counts": counts})


@owner_required("payments")
def payment_receipt(request, pk):
    p = get_object_or_404(Payment, pk=pk)
    return serve_private(request, p.receipt)


@owner_required("payments")
@require_POST
def payment_approve(request, pk):
    p = get_object_or_404(Payment, pk=pk, status="pending")
    days = request.POST.get("days", "").strip()
    activate_enrollment(p.enrollment, request.user, payment=p, days=int(days) if days.isdigit() else None)
    audit(request, "تفعيل اشتراك", f"{p.enrollment.student.phone} ← {p.enrollment.teacher.teacher_code}", f"{p.amount} جنيه")
    messages.success(request, "تم تفعيل الاشتراك وإشعار الطالب.")
    return redirect(request.META.get("HTTP_REFERER") or "owner:payments")


@owner_required("payments")
@require_POST
def payment_reject(request, pk):
    p = get_object_or_404(Payment, pk=pk, status="pending")
    reject_payment(p, request.user, request.POST.get("note", ""))
    audit(request, "رفض إيصال دفع", f"{p.enrollment.student.phone}", request.POST.get("note", ""))
    messages.success(request, "تم رفض الإيصال وإشعار الطالب.")
    return redirect(request.META.get("HTTP_REFERER") or "owner:payments")


# ------------------------------------------------------------------ الإيرادات
@owner_required("revenue")
def revenue(request):
    now = timezone.now()
    approved = Payment.objects.filter(status="approved")
    stats = {
        "total": approved.aggregate(s=Sum("amount"))["s"] or 0,
        "today": approved.filter(reviewed_at__date=now.date()).aggregate(s=Sum("amount"))["s"] or 0,
        "month": approved.filter(reviewed_at__gte=now.replace(day=1)).aggregate(s=Sum("amount"))["s"] or 0,
        "prev_month": approved.filter(reviewed_at__year=(now.replace(day=1) - timedelta(days=1)).year,
                                      reviewed_at__month=(now.replace(day=1) - timedelta(days=1)).month).aggregate(s=Sum("amount"))["s"] or 0,
    }
    by_teacher = (approved.values("enrollment__teacher__user__full_name", "enrollment__teacher_id")
                  .annotate(total=Sum("amount"), n=Count("id")).order_by("-total")[:10])
    daily = []
    for i in range(13, -1, -1):
        d = (now - timedelta(days=i)).date()
        daily.append({"l": d.strftime("%d/%m"), "p": approved.filter(reviewed_at__date=d).aggregate(s=Sum("amount"))["s"] or 0})
    maxv = max([d["p"] for d in daily] + [1])
    for d in daily:
        d["pct"] = round(float(d["p"]) * 100 / float(maxv)) if maxv else 0
    return render(request, "owner/revenue.html", {"stats": stats, "by_teacher": by_teacher, "daily": daily})


# ------------------------------------------------------------------ التحليلات
@owner_required("analytics")
def analytics(request):
    now = timezone.now()
    since30 = now - timedelta(days=30)
    stats = {
        "new_students_30": User.objects.filter(role="student", date_joined__gte=since30).count(),
        "active_students": Enrollment.objects.filter(status="active", expires_at__gt=now).values("student").distinct().count(),
        "stopped": Enrollment.objects.filter(status="active", expires_at__lte=now, expires_at__gte=since30).count(),
    }
    top_teachers = (TeacherProfile.objects.annotate(n=Count("enrollments", filter=Q(enrollments__status="active", enrollments__expires_at__gt=now)))
                    .order_by("-n")[:8])
    top_lessons = LessonProgress.objects.filter(completed=True).values("lesson__title").annotate(n=Count("id")).order_by("-n")[:8]
    hard_q = (Attempt.objects.none())
    from learning.models import Question
    hardest_q = (Question.objects.annotate(wrong=Count("student_answers", filter=Q(student_answers__is_correct=False)),
                                           total=Count("student_answers")).filter(total__gte=1).order_by("-wrong")[:8])
    completion_avg = LessonProgress.objects.filter(completed=True).count()
    exam_completion = Attempt.objects.filter(submitted_at__isnull=False).count()
    return render(request, "owner/analytics.html", {"stats": stats, "top_teachers": top_teachers, "top_lessons": top_lessons,
                                                    "hardest_q": hardest_q, "completion_avg": completion_avg, "exam_completion": exam_completion})


# ------------------------------------------------------------------ المحتوى
@owner_required("content")
def content(request):
    courses = Course.objects.select_related("teacher__user").annotate(n_lessons=Count("units__lessons", distinct=True))
    return render(request, "owner/content.html", {"courses": courses})


# ------------------------------------------------------------------ الإشعارات
@owner_required("notifications")
def notifications(request):
    items = Announcement.objects.filter(teacher__isnull=True)
    contacts = ContactMessage.objects.all()[:15]
    return render(request, "owner/notifications.html", {"items": items, "contacts": contacts})


@owner_required("notifications")
def notification_new(request):
    if request.method == "POST":
        title, body, audience = request.POST.get("title", "").strip(), request.POST.get("body", "").strip(), request.POST.get("audience", "students")
        if title and body:
            Announcement.objects.create(author=request.user, teacher=None, audience=audience, title=title, body=body)
            if audience in ("all", "students"):
                notify_many(User.objects.filter(role="student", is_active=True), title, body[:150], "/student/notifications/", "announcement")
            if audience in ("all", "teachers"):
                notify_many(User.objects.filter(role="teacher", is_active=True), title, body[:150], "/teacher/announcements/", "announcement")
            audit(request, "إعلان عام", audience, title)
            messages.success(request, "تم إرسال الإعلان.")
        return redirect("owner:notifications")
    return redirect("owner:notifications")


# ------------------------------------------------------------------ الدعم
@owner_required("tickets")
def tickets(request):
    qs = Ticket.objects.select_related("user")
    status = request.GET.get("status", "")
    if status:
        qs = qs.filter(status=status)
    page, qstr = paginate(request, qs, 25)
    counts = {"new": Ticket.objects.filter(status="new").count(), "in_progress": Ticket.objects.filter(status="in_progress").count(),
             "resolved": Ticket.objects.filter(status="resolved").count()}
    return render(request, "owner/tickets.html", {"page": page, "qs": qstr, "status": status, "counts": counts})


@owner_required("tickets")
def ticket_detail(request, pk):
    t = get_object_or_404(Ticket, pk=pk)
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "reply":
            text = request.POST.get("text", "").strip()
            if text:
                TicketReply.objects.create(ticket=t, author=request.user, text=text)
                t.status = "in_progress"
                t.assignee = t.assignee or request.user
                t.save()
                notify(t.user, f"رد جديد على تذكرتك: {t.subject}", text[:100], "/support/", "ticket")
        elif action == "resolve":
            t.status = "resolved"
            t.save()
            notify(t.user, f"تم حل مشكلتك: {t.subject}", "", "/support/", "ticket")
        elif action == "reopen":
            t.status = "in_progress"
            t.save()
        return redirect("owner:ticket_detail", pk=pk)
    return render(request, "owner/ticket_detail.html", {"t": t, "replies": t.replies.select_related("author")})


# ------------------------------------------------------------------ الإعدادات وفريق العمل
@owner_required("settings")
def settings_view(request):
    s = SiteSetting.get()
    form = SiteSettingForm(request.POST or None, instance=s)
    staff_form = StaffCreateForm()
    if request.method == "POST" and "staff_submit" not in request.POST and form.is_valid():
        form.save()
        audit(request, "تعديل إعدادات المنصة")
        messages.success(request, "تم حفظ الإعدادات.")
        return redirect("owner:settings")
    staff = User.objects.filter(role="staff")
    from accounts.utils import get_ai_keys
    groq_key, anthropic_key = get_ai_keys()
    ai_status = "groq" if groq_key else ("anthropic" if anthropic_key else "none")
    return render(request, "owner/settings.html", {"form": form, "staff": staff, "staff_form": staff_form, "ai_status": ai_status})


@owner_required("settings")
def staff_new(request):
    form = StaffCreateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        u = form.save()
        audit(request, "إضافة عضو فريق", u.phone, u.get_staff_role_display())
        messages.success(request, "تم إنشاء حساب عضو الفريق.")
        return redirect("owner:settings")
    return render(request, "owner/staff_form.html", {"form": form})


@owner_required("settings")
def audit_log(request):
    page, qstr = paginate(request, AuditLog.objects.select_related("actor"), 40)
    return render(request, "owner/audit.html", {"page": page, "qs": qstr})


@owner_required("settings")
def security(request):
    fails = LoginAttempt.objects.filter(success=False).order_by("-created_at")[:40]
    active_sessions_note = True
    return render(request, "owner/security.html", {"fails": fails})


@owner_required("teachers")
@require_POST
def impersonate(request, pk):
    t = get_object_or_404(TeacherProfile, pk=pk)
    if not has_perm(request.user, "teacher_impersonate"):
        messages.error(request, "ليست لديك صلاحية الدخول لحساب مدرّس.")
        return redirect("owner:teacher_detail", pk=pk)
    if not t.user.is_active:
        messages.error(request, "حساب هذا المدرّس موقوف. فعّله أولًا ثم ادخل إليه.")
        return redirect("owner:teacher_detail", pk=pk)
    admin_pk = request.user.pk
    audit(request, "دخول كمدرّس (محاكاة)", t.teacher_code)
    from django.contrib.auth import login
    login(request, t.user)
    # يجب ضبط المفتاح بعد login(): تسجيل الدخول بمستخدم مختلف يمسح الجلسة، ولو ضُبط قبله لضاع زر «العودة لحسابي»
    request.session["impersonator"] = admin_pk
    return redirect("teacher:home")


@require_POST
def stop_impersonate(request):
    admin_id = request.session.pop("impersonator", None)
    if admin_id:
        admin = get_object_or_404(User, pk=admin_id, role__in=("owner", "staff"))
        from django.contrib.auth import login
        login(request, admin)
        messages.info(request, "عدت إلى حسابك.")
    return redirect("owner:overview")


# ------------------------------------------------------------------ صلاحيات فريق الإدارة
@owner_required("settings")
def staff_permissions(request):
    """مصفوفة صلاحيات: صفوف = الصلاحيات، أعمدة = أدوار الفريق. التفعيل يسري على كل من يحمل الدور."""
    from accounts.models import SiteSetting
    roles = list(User.STAFF_ROLES)
    s = SiteSetting.get()
    if request.method == "POST":
        if request.POST.get("action") == "defaults":
            s.staff_permissions = {}
            audit(request, "إعادة صلاحيات الفريق للوضع الافتراضي")
        else:
            data = {}
            for role, _label in roles:
                data[role] = sorted(k for k in CONFIGURABLE_PERMS if request.POST.get(f"perm__{role}__{k}") == "1")
            s.staff_permissions = data
            audit(request, "تعديل صلاحيات فريق الإدارة", detail="; ".join(f"{r}: {len(v)}" for r, v in data.items()))
        s.save(update_fields=["staff_permissions"])
        messages.success(request, "تم حفظ صلاحيات الفريق.")
        return redirect("owner:staff_permissions")
    stored = s.staff_permissions or {}
    current = {role: (set(stored[role]) & CONFIGURABLE_PERMS if role in stored else default_staff_perms(role)) for role, _ in roles}
    groups = [{"title": title, "rows": [{"key": k, "label": lbl, "cells": [{"role": r, "on": k in current[r]} for r, _ in roles]}
                                          for k, lbl in items]} for title, items in PERMISSION_GROUPS]
    counts = {r: User.objects.filter(role="staff", staff_role=r).count() for r, _ in roles}
    return render(request, "owner/staff_permissions.html", {"roles": [(r, l, counts[r]) for r, l in roles], "groups": groups,
                                                            "custom": bool(stored)})
