from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from learning.services import visible_courses, visible_lessons_qs

from .forms import ContactForm, ForgotForm, LoginForm, RegisterForm
from .models import ContactMessage, LoginAttempt, SiteSetting, TeacherProfile, Testimonial, User
from .utils import client_ip, normalize_phone


def home_for(user):
    return {"student": "/student/", "teacher": "/teacher/"}.get(user.role, "/owner/")


def landing(request):
    if request.user.is_authenticated and request.GET.get("stay") != "1":
        pass  # نسمح للمسجّل بمشاهدة الصفحة الرئيسية، مع زر «لوحتي»
    teachers = TeacherProfile.objects.filter(is_listed=True, user__is_active=True).select_related("user")[:6]
    return render(request, "site/landing.html", {"teachers": teachers, "testimonials": Testimonial.objects.filter(is_active=True)[:6]})


def pricing(request):
    return render(request, "site/pricing.html")


def about(request):
    return render(request, "site/about.html")


def privacy(request):
    return render(request, "site/legal.html", {"title": "سياسة الخصوصية", "text": SiteSetting.get().privacy_text, "kind": "privacy"})


def terms(request):
    return render(request, "site/legal.html", {"title": "الشروط والأحكام", "text": SiteSetting.get().terms_text, "kind": "terms"})


def contact(request):
    form = ContactForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        c = form.cleaned_data
        ContactMessage.objects.create(kind="contact", name=c["name"], phone=c["phone"], message=c["message"])
        messages.success(request, "وصلتنا رسالتك وسنتواصل معك قريبًا.")
        return redirect("contact")
    return render(request, "site/contact.html", {"form": form})


def teachers_directory(request):
    q = request.GET.get("q", "").strip()
    qs = TeacherProfile.objects.filter(is_listed=True, user__is_active=True).select_related("user")
    if q:
        qs = qs.filter(Q(user__full_name__icontains=q) | Q(subject__icontains=q) | Q(teacher_code__iexact=q))
    return render(request, "site/teachers.html", {"teachers": qs, "q": q})


def teacher_public(request, slug):
    t = get_object_or_404(TeacherProfile.objects.select_related("user"), slug=slug, user__is_active=True)
    courses = visible_courses(t)
    return render(request, "site/teacher_public.html", {"t": t, "courses": courses, "lessons_count": visible_lessons_qs(teacher=t).count()})


def register(request):
    if request.user.is_authenticated:
        return redirect("go")
    if not SiteSetting.get().registration_open:
        messages.error(request, "التسجيل مغلق مؤقتًا. تواصل مع الدعم.")
        return redirect("landing")
    code = request.GET.get("t") or request.POST.get("t") or ""
    join_teacher = TeacherProfile.objects.filter(teacher_code=code).select_related("user").first() if code else None
    form = RegisterForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        login(request, user)
        sn = getattr(getattr(user, "student_profile", None), "student_number", None)
        messages.success(request, f"أهلًا بك يا {user.first_name}! تم إنشاء حسابك بنجاح." + (f" رقمك (ID) هو {sn}." if sn else ""))
        if code and TeacherProfile.objects.filter(teacher_code=code).exists():
            return redirect("student:workspace", code=code)
        return redirect("student:home")
    return render(request, "auth/register.html", {"form": form, "code": code, "join_teacher": join_teacher})


def login_view(request):
    if request.user.is_authenticated:
        return redirect("go")
    form = LoginForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        phone = normalize_phone(form.cleaned_data["phone"])
        ip = client_ip(request)
        since = timezone.now() - timedelta(minutes=settings.LOGIN_LOCK_MINUTES)
        fails = LoginAttempt.objects.filter(phone=phone, success=False, created_at__gte=since).count()
        if fails >= settings.MAX_FAILED_LOGINS:
            form.add_error(None, f"تم إيقاف المحاولة مؤقتًا بسبب تكرار الخطأ. حاول بعد {settings.LOGIN_LOCK_MINUTES} دقيقة.")
        else:
            user = authenticate(request, username=phone, password=form.cleaned_data["password"])
            ua = request.META.get("HTTP_USER_AGENT", "")[:250]
            LoginAttempt.objects.create(phone=phone, ip=ip, user_agent=ua, success=bool(user))
            if user is None:
                exists = User.objects.filter(phone=phone, is_active=False).exists()
                form.add_error(None, "هذا الحساب موقوف. تواصل مع الدعم." if exists else "رقم الهاتف أو كلمة المرور غير صحيحة.")
            else:
                login(request, user)
                if not form.cleaned_data["remember"]:
                    request.session.set_expiry(0)
                nxt = request.GET.get("next", "")
                if nxt.startswith("/") and not nxt.startswith("//"):
                    return redirect(nxt)
                return redirect("go")
    return render(request, "auth/login.html", {"form": form})


@require_POST
def logout_view(request):
    logout(request)
    messages.info(request, "تم تسجيل الخروج.")
    return redirect("landing")


@login_required
def go(request):
    return redirect(home_for(request.user))


def forgot_password(request):
    form = ForgotForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        c = form.cleaned_data
        ContactMessage.objects.create(kind="password_reset", phone=c["phone"], message=c["note"])
        messages.success(request, "تم إرسال طلبك. ستتواصل معك الإدارة لإعادة تعيين كلمة المرور.")
        return redirect("login")
    return render(request, "auth/forgot.html", {"form": form})


def error_404(request, exception=None):
    return render(request, "404.html", status=404)
