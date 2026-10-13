"""صفحة حضور وغياب السنتر (رفع ملف PDF بأرقام الطلاب)."""
import os
import tempfile
from datetime import date as date_cls

from django.contrib import messages
from django.db.models import Count, Q
from django.shortcuts import redirect, render

from accounts.decorators import teacher_required
from accounts.models import StudentProfile
from learning.models import CenterAttendance

from . import center_service as svc

MAX_UPLOAD = 15 * 1024 * 1024
ALLOWED_EXT = (".pdf",) + svc.IMAGE_EXTENSIONS


def _grade(value):
    try:
        g = int(value)
    except (TypeError, ValueError):
        return 0
    return g if g in dict(StudentProfile.GRADES) else 0


def _date(value):
    try:
        return date_cls.fromisoformat(value) if value else date_cls.today()
    except ValueError:
        return date_cls.today()


def _page_rows(t, d, grade):
    roster = list(svc.roster_queryset(t, grade).order_by("student_number"))
    recs = {r.student_id: r for r in CenterAttendance.objects.filter(teacher=t, date=d)}
    totals = {row["student_id"]: row for row in
              CenterAttendance.objects.filter(teacher=t, student__student_profile__in=roster)
              .values("student_id").annotate(p=Count("id", filter=Q(present=True)), a=Count("id", filter=Q(present=False)))}
    rows = []
    for sp in roster:
        rec = recs.get(sp.user_id)
        tot = totals.get(sp.user_id, {})
        rows.append({"sp": sp, "status": "none" if rec is None else ("present" if rec.present else "absent"),
                     "p": tot.get("p", 0), "a": tot.get("a", 0)})
    return rows


def handle_center_post(request, t, d, grade):
    """يعالج رفع الملف / تحديث المعاينة / تأكيد الحفظ لحضور السنتر.

    يعيد (review, response): review هي بيانات نافذة المراجعة (أو None)،
    وresponse يكون غير فارغ فقط عند إعادة التوجيه بعد الحفظ الناجح.
    مشتركة بين صفحة /teacher/attendance/ وصفحة /teacher/attendance/center/.
    """
    review = None
    action = request.POST.get("action")
    # الصندوق غير المحدَّد لا يُرسَل أصلًا؛ عند أول رفع نفترض التفعيل
    mark_absent = True if action == "upload" else request.POST.get("mark_absent") == "1"
    numbers, method, warning = None, "", ""
    if action == "upload":
        f = request.FILES.get("file")
        ext = os.path.splitext(f.name)[1].lower() if f else ""
        if not f:
            messages.error(request, "اختر ملف PDF أولًا.")
        elif ext not in ALLOWED_EXT:
            messages.error(request, "الصيغة غير مدعومة. ارفع ملف PDF (أو صورة JPG/PNG).")
        elif f.size > MAX_UPLOAD:
            messages.error(request, "حجم الملف كبير (الحد الأقصى 15 ميجابايت).")
        else:
            tmp = tempfile.NamedTemporaryFile(suffix=ext, delete=False)
            try:
                for chunk in f.chunks():
                    tmp.write(chunk)
                tmp.close()
                numbers, method, warning = svc.extract_numbers(tmp.name, f.name)
            except Exception as e:
                messages.error(request, f"تعذّرت قراءة الملف: {e}")
            finally:
                try:
                    os.unlink(tmp.name)
                except OSError:
                    pass
            if numbers is not None and not numbers:
                messages.warning(request, "لم يتم العثور على أي أرقام في الملف. يمكنك كتابة الأرقام يدويًا بالأسفل.")
                numbers = []
    elif action in ("preview", "confirm"):
        numbers = svc.parse_numbers(request.POST.get("numbers", ""))
        if action == "confirm":
            if not numbers:
                messages.error(request, "لا توجد أرقام لحفظها.")
            else:
                p, a = svc.apply_attendance(t, d, numbers, grade, mark_absent)
                messages.success(request, f"تم حفظ حضور السنتر: {p} حاضر و{a} غائب.")
                return None, redirect(f"{request.path}?date={d.isoformat()}&grade={grade}")
            numbers = None
    if numbers is not None:
        prev = svc.build_preview(t, numbers, grade)
        review = {"numbers_text": " ".join(map(str, numbers)), "count": len(numbers), "method": method,
                  "warning": warning, "mark_absent": mark_absent, **prev}
    return review, None


@teacher_required
def center_attendance(request):
    t = request.user.teacher_profile
    grade = _grade(request.GET.get("grade") or request.POST.get("grade"))
    d = _date(request.GET.get("date") or request.POST.get("date"))
    review = None

    if request.method == "POST":
        review, response = handle_center_post(request, t, d, grade)
        if response:
            return response

    rows = _page_rows(t, d, grade)
    stats = {"total": len(rows), "present": sum(r["status"] == "present" for r in rows),
             "absent": sum(r["status"] == "absent" for r in rows),
             "none": sum(r["status"] == "none" for r in rows)}
    stats["rate"] = round(stats["present"] * 100 / (stats["present"] + stats["absent"])) if (stats["present"] + stats["absent"]) else 0
    return render(request, "teacher/center_attendance.html", {
        "d": d.isoformat(), "grade": grade, "grades": StudentProfile.GRADES, "rows": rows,
        "stats": stats, "review": review})
