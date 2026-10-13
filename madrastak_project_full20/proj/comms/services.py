from .models import Notification


def notify(user, title, body="", url="", kind="info"):
    return Notification.objects.create(user=user, title=title, body=body[:400], url=url, kind=kind)


def notify_many(users, title, body="", url="", kind="info"):
    Notification.objects.bulk_create([Notification(user=u, title=title, body=body[:400], url=url, kind=kind) for u in users])
