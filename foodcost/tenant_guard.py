# -*- coding: utf-8 -*-
"""
Применение тарифа «вглубь»: модуль, которого нет у компании по тарифу,
нельзя открыть по прямой ссылке (middleware) и он не показывается в меню
(context_processor использует те же хелперы).

Принципы:
  • Арендатор определяется из URL /c/<slug>/...
  • Без подписки у компании ограничений нет (tenant_has_module → True) —
    обратная совместимость с текущим единственным клиентом.
  • Django-суперюзер (владелец платформы) видит всё, тарифом не ограничен.
  • Неизвестный сегмент пути не блокируется (fail-open) — чтобы случайно
    не закрыть служебную страницу.
"""
import re

from django.shortcuts import redirect

from .models import Country
from .module_access import tenant_has_module

# Первый сегмент пути после /c/<slug>/ → раздел (код модуля).
# Сюда входят только «модульные» разделы. Служебные (settings, users, team,
# company, dish*, вход на главную) намеренно не перечислены — они не
# ограничиваются тарифом.
_SEGMENT_SECTION = {
    "orders": "orders",
    "products": "products",
    "preparations": "preparations",
    "packaging": "packaging",
    "utilities": "utilities",
    "writeoffs": "writeoffs",
    "customers": "customers",
    "stock": "stock",
    "purchases": "purchases",
    "suppliers": "suppliers",
    "inventory": "inventory",
    "transfers": "transfers",
    "finance": "finance",
    "schedule": "schedule",
    "shifts": "shifts",
    "employees": "employees",
    "techcards": "techcards",
}

_PATH_RE = re.compile(r"^/c/(?P<slug>[^/]+)/(?P<seg>[^/]+)")


def section_for_segment(seg):
    return _SEGMENT_SECTION.get(seg)


def current_country(request):
    """Арендатор из пути /c/<slug>/... или None."""
    m = re.match(r"^/c/(?P<slug>[^/]+)/", request.path or "")
    if not m:
        return None
    return Country.objects.filter(slug=m.group("slug")).first()


class TenantModuleMiddleware:
    """Блокирует заход в раздел, которого нет у компании по тарифу."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        blocked = self._blocked(request)
        if blocked is not None:
            return blocked
        return self.get_response(request)

    def _blocked(self, request):
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            return None
        if user.is_superuser:
            return None  # владелец платформы — без ограничений

        m = _PATH_RE.match(request.path or "")
        if not m:
            return None

        section = section_for_segment(m.group("seg"))
        if section is None:
            return None

        country = Country.objects.filter(slug=m.group("slug")).first()
        if country is None:
            return None

        if tenant_has_module(country, section):
            return None

        # модуля нет у компании по тарифу → возвращаем на главную кабинета
        return redirect(f"/c/{country.slug}/")
