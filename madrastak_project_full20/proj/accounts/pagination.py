from django.core.paginator import Paginator


def paginate(request, qs, per=20):
    page = Paginator(qs, per).get_page(request.GET.get("page"))
    params = request.GET.copy()
    params.pop("page", None)
    qs_str = params.urlencode()
    return page, (qs_str + "&") if qs_str else ""
