# -*- coding: utf-8 -*-
"""
Консоль платформенного супер-админа (/platform/).

Отдельная область управления арендаторами (клиентами), НЕ привязанная к
конкретному клиенту через /c/<slug>/. Доступ — только у платформенного
супер-админа (Django superuser или UserProfile.role == super_admin).

Что умеет на этом шаге:
  • список клиентов с подпиской/тарифом/модулями и базовой статистикой;
  • карточка клиента: тариф, статус подписки, матрица модулей (вкл/выкл,
    докупленные допы), drill-in статистика;
  • создание клиента (арендатор + первый филиал + админ клиента);
  • назначение тарифа, вкл/выкл отдельных модулей, приостановка/активация,
    продление триала;
  • «открыть кабинет клиента» — переход в его ERP (/c/<slug>/).

Ничего из существующего кода не меняет — только добавляет.
"""

import datetime

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.db.models import Count, Sum
from django.http import HttpResponseForbidden, Http404
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.utils.text import slugify

from .models import Country, Location, Order, UserProfile
from .models_billing import Module, Plan, TenantSubscription


# ----------------------------------------------------------------------------
# Доступ
# ----------------------------------------------------------------------------
def is_platform_admin(user):
    """Доступ к консоли платформы: супер-админ ИЛИ платформенный менеджер."""
    if not getattr(user, "is_authenticated", False):
        return False
    if user.is_superuser:
        return True
    prof = getattr(user, "profile", None)
    if prof is None:
        return False
    return prof.role in (
        UserProfile.ROLE_SUPER_ADMIN,
        UserProfile.ROLE_PLATFORM_MANAGER,
    )


def is_platform_owner(user):
    """Владелец платформы (супер-админ): только он заводит/снимает менеджеров."""
    if not getattr(user, "is_authenticated", False):
        return False
    if user.is_superuser:
        return True
    prof = getattr(user, "profile", None)
    return bool(prof and prof.role == UserProfile.ROLE_SUPER_ADMIN)


def _deny():
    return HttpResponseForbidden("Доступ только для платформенного администратора")


# ----------------------------------------------------------------------------
# Вспомогательное
# ----------------------------------------------------------------------------
def _subscription(country):
    """Подписка клиента или None (OneToOne reverse бросает DoesNotExist)."""
    try:
        return country.subscription
    except TenantSubscription.DoesNotExist:
        return None


def _client_stats(country, since=None):
    """Базовая статистика по клиенту."""
    orders = Order.objects.filter(country=country)
    agg = orders.aggregate(cnt=Count("id"), revenue=Sum("total_amount"))
    stats = {
        "orders_total": agg["cnt"] or 0,
        "revenue_total": agg["revenue"] or 0,
        "users": UserProfile.objects.filter(countries=country).distinct().count(),
        "locations": Location.objects.filter(country=country).count(),
    }
    if since is not None:
        stats["orders_period"] = orders.filter(order_date__gte=since).count()
        stats["revenue_period"] = (
            orders.filter(order_date__gte=since)
            .aggregate(s=Sum("total_amount"))["s"]
            or 0
        )
    return stats


def _invoices(country):
    try:
        from .models_invoices import SubscriptionInvoice
        return list(SubscriptionInvoice.objects.filter(country=country)[:50])
    except Exception:
        return []


def _module_matrix(country):
    """
    Матрица модулей для карточки клиента: каждый модуль с пометкой,
    входит ли он в тариф, докуплен ли и включён ли фактически.
    """
    sub = _subscription(country)
    plan_codes = set()
    extra_codes = set()
    disabled_codes = set()
    if sub is not None:
        if sub.plan_id:
            plan_codes = set(sub.plan.modules.values_list("code", flat=True))
        extra_codes = set(sub.extra_modules.values_list("code", flat=True))
        disabled_codes = set(sub.disabled_modules.values_list("code", flat=True))

    rows = []
    for m in Module.objects.all().order_by("is_addon", "sort_order", "name"):
        in_plan = m.code in plan_codes
        is_extra = m.code in extra_codes
        is_disabled = m.code in disabled_codes
        effective = (in_plan or is_extra) and not is_disabled
        rows.append({
            "code": m.code,
            "name": m.name,
            "is_addon": m.is_addon,
            "monthly_price": m.monthly_price,
            "in_plan": in_plan,
            "is_extra": is_extra,
            "is_disabled": is_disabled,
            "effective": effective,
        })
    return rows


# ----------------------------------------------------------------------------
# Список клиентов
# ----------------------------------------------------------------------------
@login_required(login_url="/login/")
def platform_dashboard(request):
    if not is_platform_admin(request.user):
        return _deny()

    since = timezone.now() - datetime.timedelta(days=30)

    countries = (
        Country.objects.all()
        .select_related("subscription", "subscription__plan")
        .annotate(
            orders_cnt=Count("orders", distinct=True),
            locations_cnt=Count("locations", distinct=True),
        )
        .order_by("name")
    )

    clients = []
    for c in countries:
        sub = _subscription(c)
        orders_30d = Order.objects.filter(
            country=c, order_date__gte=since
        ).count()
        clients.append({
            "country": c,
            "sub": sub,
            "plan_name": (sub.plan.name if (sub and sub.plan_id) else "—"),
            "status": (sub.status if sub else "none"),
            "status_label": (sub.get_status_display() if sub else "Без подписки"),
            "is_usable": (sub.is_usable() if sub else True),
            "module_count": (len(sub.effective_module_codes()) if sub else None),
            "orders_total": c.orders_cnt,
            "orders_30d": orders_30d,
            "locations": c.locations_cnt,
        })

    totals = {
        "clients": len(clients),
        "active": sum(1 for x in clients if x["status"] == "active"),
        "trial": sum(1 for x in clients if x["status"] == "trial"),
        "suspended": sum(1 for x in clients if x["status"] == "suspended"),
        "orders_30d": sum(x["orders_30d"] for x in clients),
    }

    return render(request, "foodcost/platform/dashboard.html", {
        "clients": clients,
        "totals": totals,
        "is_owner": is_platform_owner(request.user),
    })


# ----------------------------------------------------------------------------
# Карточка клиента
# ----------------------------------------------------------------------------
@login_required(login_url="/login/")
def platform_client_detail(request, country_slug):
    if not is_platform_admin(request.user):
        return _deny()

    country = get_object_or_404(Country, slug=country_slug)

    if request.method == "POST":
        action = request.POST.get("action")
        sub, _ = TenantSubscription.objects.get_or_create(country=country)

        if action == "assign_plan":
            plan_id = request.POST.get("plan_id")
            if plan_id:
                sub.plan = get_object_or_404(Plan, id=plan_id)
            else:
                sub.plan = None
            sub.save()

        elif action == "toggle_module":
            code = request.POST.get("code")
            module = get_object_or_404(Module, code=code)
            plan_codes = set()
            if sub.plan_id:
                plan_codes = set(sub.plan.modules.values_list("code", flat=True))
            if code in plan_codes:
                # модуль из тарифа → переключаем «выключен»
                if sub.disabled_modules.filter(id=module.id).exists():
                    sub.disabled_modules.remove(module)
                else:
                    sub.disabled_modules.add(module)
            else:
                # допмодуль → переключаем «докуплен»
                if sub.extra_modules.filter(id=module.id).exists():
                    sub.extra_modules.remove(module)
                    sub.disabled_modules.remove(module)
                else:
                    sub.extra_modules.add(module)

        elif action == "set_status":
            new_status = request.POST.get("status")
            valid = {c for c, _ in TenantSubscription.STATUS_CHOICES}
            if new_status in valid:
                sub.status = new_status
                sub.save()

        elif action == "extend_trial":
            days = 14
            try:
                days = max(1, int(request.POST.get("days") or 14))
            except (TypeError, ValueError):
                days = 14
            base = sub.trial_ends_at or timezone.now()
            if base < timezone.now():
                base = timezone.now()
            sub.trial_ends_at = base + datetime.timedelta(days=days)
            if sub.status == "suspended":
                sub.status = "trial"
            sub.save()

        elif action == "issue_invoice":
            from .models_invoices import SubscriptionInvoice
            amount = 0
            plan_name = ""
            if sub.plan_id:
                amount += sub.plan.monthly_price or 0
                plan_name = sub.plan.name
            for m in sub.extra_modules.all():
                amount += m.monthly_price or 0
            today = timezone.now().date()
            SubscriptionInvoice.objects.create(
                country=country, plan_name=plan_name, amount=amount,
                period_start=today,
                period_end=today + datetime.timedelta(days=30))

        elif action == "mark_invoice_paid":
            from .models_invoices import SubscriptionInvoice
            inv = get_object_or_404(
                SubscriptionInvoice,
                id=request.POST.get("invoice_id"), country=country)
            if inv.status != inv.STATUS_PAID:
                inv.status = inv.STATUS_PAID
                inv.paid_at = timezone.now()
                inv.save()
                # оплата активирует подписку и продлевает период
                sub.status = sub.STATUS_ACTIVE
                if inv.period_end:
                    end_dt = datetime.datetime.combine(
                        inv.period_end, datetime.time(23, 59))
                    if getattr(settings, "USE_TZ", False):
                        end_dt = timezone.make_aware(
                            end_dt, timezone.get_current_timezone())
                    sub.current_period_end = end_dt
                sub.save()

        elif action == "void_invoice":
            from .models_invoices import SubscriptionInvoice
            inv = get_object_or_404(
                SubscriptionInvoice,
                id=request.POST.get("invoice_id"), country=country)
            if inv.status == inv.STATUS_PENDING:
                inv.status = inv.STATUS_VOID
                inv.save()

        return redirect("platform_client_detail", country_slug=country.slug)

    sub = _subscription(country)
    since = timezone.now() - datetime.timedelta(days=30)

    return render(request, "foodcost/platform/client_detail.html", {
        "country": country,
        "sub": sub,
        "plans": Plan.objects.filter(is_active=True).order_by("sort_order", "monthly_price"),
        "modules": _module_matrix(country),
        "stats": _client_stats(country, since=since),
        "status_choices": TenantSubscription.STATUS_CHOICES,
        "is_owner": is_platform_owner(request.user),
        "invoices": _invoices(country),
    })


# ----------------------------------------------------------------------------
# Создание клиента
# ----------------------------------------------------------------------------
@login_required(login_url="/login/")
def platform_client_create(request):
    if not is_platform_admin(request.user):
        return _deny()

    error = None
    if request.method == "POST":
        name = (request.POST.get("name") or "").strip()
        slug = (request.POST.get("slug") or "").strip() or slugify(name, allow_unicode=False)
        admin_login = (request.POST.get("admin_login") or "").strip()
        admin_password = request.POST.get("admin_password") or ""
        plan_id = request.POST.get("plan_id")
        location_name = (request.POST.get("location_name") or "").strip() or "Основной филиал"

        if not name or not slug:
            error = "Укажи название и slug клиента"
        elif Country.objects.filter(slug=slug).exists():
            error = f"Клиент со slug «{slug}» уже существует"
        elif not admin_login or not admin_password:
            error = "Укажи логин и пароль админа клиента"
        elif User.objects.filter(username=admin_login).exists():
            error = f"Пользователь «{admin_login}» уже существует"
        else:
            country = Country.objects.create(name=name, slug=slug)
            Location.objects.create(country=country, name=location_name)

            admin_user = User.objects.create_user(
                username=admin_login,
                password=admin_password,
                is_superuser=False,
                is_staff=False,
            )
            prof, _ = UserProfile.objects.get_or_create(user=admin_user)
            prof.role = UserProfile.ROLE_ADMIN
            # Админ компании получает полный набор разделов своей компании —
            # дальше он сам раздаёт сотрудникам подмножество (в рамках тарифа).
            prof.allowed_sections = [c for c, _ in UserProfile.SECTION_CHOICES]
            prof.save()
            prof.countries.set([country])

            sub = TenantSubscription.objects.create(country=country)
            if plan_id:
                plan = Plan.objects.filter(id=plan_id).first()
                if plan:
                    sub.plan = plan
                    sub.save()

            return redirect("platform_client_detail", country_slug=country.slug)

    return render(request, "foodcost/platform/client_create.html", {
        "plans": Plan.objects.filter(is_active=True).order_by("sort_order", "monthly_price"),
        "error": error,
        "is_owner": is_platform_owner(request.user),
    })


# ----------------------------------------------------------------------------
# Открыть кабинет клиента
# ----------------------------------------------------------------------------
@login_required(login_url="/login/")
def platform_open_client(request, country_slug):
    if not is_platform_admin(request.user):
        return _deny()
    country = get_object_or_404(Country, slug=country_slug)
    return redirect(f"/c/{country.slug}/")


# ----------------------------------------------------------------------------
# Команда платформы (менеджеры) — только для владельца
# ----------------------------------------------------------------------------
@login_required(login_url="/login/")
def platform_team(request):
    if not is_platform_owner(request.user):
        return _deny()

    error = None
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "create_manager":
            login_ = (request.POST.get("login") or "").strip()
            password = request.POST.get("password") or ""
            if not login_ or not password:
                error = "Укажи логин и пароль"
            elif User.objects.filter(username=login_).exists():
                error = f"Пользователь «{login_}» уже существует"
            else:
                u = User.objects.create_user(
                    username=login_, password=password,
                    is_superuser=False, is_staff=False,
                )
                prof, _ = UserProfile.objects.get_or_create(user=u)
                prof.role = UserProfile.ROLE_PLATFORM_MANAGER
                prof.save()
                return redirect("platform_team")

    staff = (
        UserProfile.objects
        .filter(role__in=[UserProfile.ROLE_SUPER_ADMIN, UserProfile.ROLE_PLATFORM_MANAGER])
        .select_related("user")
        .order_by("role", "user__username")
    )
    return render(request, "foodcost/platform/team.html", {
        "staff": staff,
        "error": error,
        "is_owner": True,
        "me_id": request.user.id,
    })


@login_required(login_url="/login/")
def platform_team_remove(request, user_id):
    """Снять менеджера (понижаем до «просмотр»). Супер-админа и себя — нельзя."""
    if not is_platform_owner(request.user):
        return _deny()
    if request.method == "POST":
        prof = get_object_or_404(UserProfile, user_id=user_id)
        if (prof.role == UserProfile.ROLE_PLATFORM_MANAGER
                and prof.user_id != request.user.id):
            prof.role = UserProfile.ROLE_VIEWER
            prof.save()
    return redirect("platform_team")
