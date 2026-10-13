# Madrastak — Original Project + Updated UI

Base: the supplied original project.

The backend and application logic were kept from the original project. Only the following UI templates/assets were overlaid from the supplied modified-UI bundle:

- templates/teacher/hub.html
- templates/teacher/course_form.html
- templates/teacher/courses.html
- templates/teacher/course_detail.html
- templates/teacher/quiz_form.html
- templates/teacher/quiz_detail.html
- templates/teacher/unit_form.html
- templates/teacher/unit_detail.html
- templates/examimport/batch_review.html
- templates/student/course.html
- templates/student/unit.html
- static/teacher/icons/lesson-video.png
- static/teacher/icons/exercises.png

Compatibility adjustments made inside UI only:

1. Removed the UI reference to `teacher:question_edit`, because the original backend/URL configuration does not expose that route. The edit-looking control is kept visually but disabled rather than pointing to a nonexistent endpoint.
2. `teacher/course_form.html` now preserves course type, grade, term label, publish status, and publish date when editing an existing course. New courses still submit as `published`, matching the updated UI flow.
3. `teacher/unit_form.html` submits new units as `published` and preserves the existing status when editing.

No Python/backend file from the modified-UI archive was copied into this project.

---

## Update: Teacher dashboard redesign (home + content management)

Files changed/added on top of the base project:

- `templates/teacher/home.html` — new dashboard design (real data: stats, first course, latest enrollments).
- `templates/teacher/manage_content.html` — new content-management design (filters, edit, delete, pagination).
- `templates/teacher/_sidebar.html` — NEW: shared teacher sidebar (`{% include "teacher/_sidebar.html" with active="home" %}`).
- `templates/teacher/_sidebar_head.html` — NEW: fonts + CSS required by the shared sidebar.
- `teacher/views.py` — `home()`: `select_related("student", "student__student_profile")` for the grade column.
- `uikit/context_processors.py` — teacher nav: removed the tabs «الطلاب», «الرسائل», «الإعلانات», «الدعم والمساعدة».

No migrations were added. Note: if a "no such column ... example_translation" error appears, run `python manage.py migrate`.

---

## Update: students module integration + soft/vibrant redesign of reports & subscriptions

Merged (uploaded updates, now the baseline for future work):

- `teacher/views.py` — `students()` gained CSV export (`?export=csv`); `student_detail()` now supplies remaining days, per-course performance, activity donut, 7-day trend, attendance rate, strengths, gauge values.
- `templates/teacher/students.html` — new student-list design (uses the shared sidebar, `active="students"`).
- `templates/teacher/student_detail.html` — new student-detail dashboard (uses the shared sidebar, `active="students"`).

Redesigned (same views, no backend change):

- `templates/teacher/student_reports.html` (`/teacher/reports/`) — soft vibrant palette, gradient banner, colored rows/pills; shared sidebar with `active="reports"`.
- `templates/teacher/subscriptions.html` (`/teacher/subscriptions/`) — soft vibrant palette, stat cards, status distribution bar, remaining-days bar; shared sidebar with `active="subscriptions"`.
- `templates/teacher/_pager_soft.html` — NEW: pager used by the two pages above.

Both pages now include `teacher/_sidebar.html` and the same top header as the home page, so the sidebar is byte-for-byte the same component.

---

## Update: Student vocabulary page redesign

- `templates/student/vocabulary.html` — standalone page built from the supplied design (no sidebar/header). Tabs: التعليم / التدريب / الاختبار / كلمات للمراجعة. Clicking a small word card shows that word in the large card; the other tabs render their quiz inside the same large card.
- `student/views.py` — `vocabulary()` now validates `mode`, and passes word data (meaning, example, translation, estimated level, progress streak) as a list rendered with `json_script`.

No migrations and no model changes.


---

## Update: Student home page redesign (new standalone design)

- `templates/student/home.html` — now a standalone page built from the supplied design (own sidebar + header, no `student/base.html`). Everything is wired to real data: stat cards, upcoming quizzes/homework, calendar, performance chart, recent lessons, "continue where you left off" banner, and the teacher/subject card (progress, homework, attendance, answered questions). Kept original functions: search, notifications bell, profile link, sidebar links, teacher switcher menu, POST+CSRF logout, Django messages (toasts), admin-impersonation bar, announcements, other-teachers cards, and empty states.
- `uikit/templatetags/ui_extras.py` — `student_home_data()` additionally returns chart coordinates for the new SVG (`dpoints`, `dline`, `darea`, `dlabels`); new tag `student_focus_data(student, lesson)` feeds the teacher card.
- The "next class date" badge in the design was left out: the project has no class-schedule data.

No migrations and no model changes. If a "no such column ... text_answer / example_translation" error appears, run `python manage.py migrate`.


---

## Update: Teacher home, courses page and sidebar refresh

- templates/teacher/home.html — new welcome hero (uses static/ui/img/hero-study.png), sidebar included with home_toggle=True.
- templates/teacher/courses.html — new courses hero (uses static/ui/img/course-icon.png), 2-column course grid, updated card styling.
- templates/teacher/_sidebar.html — optional home_toggle flag (student-dashboard style collapse button + Tajawal font); other pages are unaffected since the flag is off by default.
- static/ui/img/hero-study.png, static/ui/img/course-icon.png — NEW images.

No Python/backend changes, no migrations.

---

## Update: Teacher courses page + Student workspace (courses) redesign

- `templates/teacher/courses.html` — updated design (soft pastel cards, new hero). Fix applied during integration: the book-image `{% cycle %}` is now declared with `as book_img silent` before the cover check, so card colours stay in sync even when some courses have a cover image.
- `templates/student/workspace.html` — now a standalone page (extends `ui/document.html`, own top bar) with filter panel and quick stats. Fix applied during integration: added `{% teacher_public_stats t as TS %}` (the new file used `TS.*` without defining it, so the stats would have rendered blank).

No Python/backend changes, no migrations. Verified: `manage.py check` clean; both pages and the main teacher/student pages return HTTP 200 on the bundled database.
