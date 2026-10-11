# -*- coding: utf-8 -*-
"""
Веб-авторизация юрлиц по коду (SMS/e-mail).

Регистрация (2 шага):
  /signup/         — данные компании (название, ИНН, телефон, e-mail, канал)
  /signup/verify/  — ввод кода → создаём компанию+админа+триал, логиним
Вход (2 шага):
  /login/          — e-mail ИЛИ телефон → шлём код
  /login/verify/   — ввод кода → вход в сессию
Запасной вход по паролю остаётся на /login/password/ (для супер-админа/персонала).
"""
import datetime

from django.contrib.auth import login
from django.contrib.auth.models import User
from django.shortcuts import render, redirect
from django.utils import timezone
from django.utils.text import slugify

from .models import Country, Location, UserProfile
from .models_billing import Plan, TenantSubscription
from . import otp_core

TRIAL_DAYS = 7
DEFAULT_TRIAL_PLAN_CODE = "start"
_BACKEND = "django.contrib.auth.backends.ModelBackend"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _digits(s):
    return "".join(ch for ch in str(s or "") if ch.isdigit())


def _unique_username(base):
    base = slugify(base, allow_unicode=False) or "user"
    username = base
    i = 2
    while User.objects.filter(username=username).exists():
        username = f"{base}{i}"
        i += 1
    return username


def _unique_slug(base):
    base = slugify(base, allow_unicode=False) or "company"
    slug = base
    i = 2
    while Country.objects.filter(slug=slug).exists():
        slug = f"{base}-{i}"
        i += 1
    return slug


def _inn_taken(inn):
    try:
        from .models_company import CompanyProfile
        return CompanyProfile.objects.filter(inn=inn).exists()
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Регистрация
# ---------------------------------------------------------------------------
def signup(request):
    if request.user.is_authenticated:
        return redirect("/")

    error = None
    form = {}
    if request.method == "POST":
        name = (request.POST.get("company_name") or "").strip()
        inn = _digits(request.POST.get("inn"))
        phone = otp_core.normalize_phone(request.POST.get("phone"))
        email = (request.POST.get("email") or "").strip()
        channel = request.POST.get("channel") or "sms"
        form = {"company_name": name, "inn": inn, "phone": phone,
                "email": email, "channel": channel}

        if not name:
            error = "Укажи название компании"
        elif len(inn) != 9:
            error = "ИНН (СТИР) должен содержать 9 цифр"
        elif _inn_taken(inn):
            error = "Компания с таким ИНН уже зарегистрирована"
        elif len(phone) < 10:
            error = "Укажи корректный номер телефона"
        elif "@" not in email:
            error = "Укажи корректный e-mail"
        elif channel not in ("sms", "email"):
            error = "Выбери способ получения кода"
        else:
            request.session["signup"] = {
                "company_name": name, "inn": inn,
                "phone": phone, "email": email}
            identifier = email if channel == "email" else phone
            _, debug = otp_core.start_otp(
                request, identifier, channel, purpose="signup")
            request.session["otp_debug"] = debug or ""
            request.session["otp_target"] = identifier
            return redirect("/signup/verify/")

    return render(request, "foodcost/signup.html", {
        "error": error, "form": form, "trial_days": TRIAL_DAYS})


def signup_verify(request):
    if request.user.is_authenticated:
        return redirect("/")
    data = request.session.get("signup")
    if not data:
        return redirect("/signup/")

    error = None
    if request.method == "POST":
        ok, err, _ = otp_core.verify_otp(
            request, request.POST.get("code"), purpose="signup")
        if not ok:
            error = err
        else:
            name = data["company_name"]
            slug = _unique_slug(name)
            country = Country.objects.create(name=name, slug=slug)
            Location.objects.create(country=country, name="Основной филиал")

            username = _unique_username(data["email"].split("@")[0] or "admin")
            user = User.objects.create_user(
                username=username, email=data["email"],
                is_superuser=False, is_staff=False)
            user.set_unusable_password()   # вход только по коду
            user.save()

            prof = UserProfile.objects.create(
                user=user, role=UserProfile.ROLE_ADMIN,
                allowed_sections=[c for c, _ in UserProfile.SECTION_CHOICES])
            prof.countries.set([country])
            if hasattr(prof, "phone"):
                prof.phone = data["phone"]
                prof.save(update_fields=["phone"])

            sub = TenantSubscription.objects.create(
                country=country, status=TenantSubscription.STATUS_TRIAL,
                trial_ends_at=timezone.now() + datetime.timedelta(days=TRIAL_DAYS))
            plan = Plan.objects.filter(
                code=DEFAULT_TRIAL_PLAN_CODE, is_active=True).first()
            if plan:
                sub.plan = plan
                sub.save()

            try:
                from .models_company import CompanyProfile
                CompanyProfile.objects.get_or_create(
                    country=country,
                    defaults={"legal_name": name, "inn": data["inn"],
                              "phone": data["phone"], "email": data["email"]})
            except Exception:
                pass

            request.session.pop("signup", None)
            request.session.pop("otp_debug", None)
            request.session.pop("otp_target", None)
            login(request, user, backend=_BACKEND)
            return redirect(f"/c/{country.slug}/")

    return render(request, "foodcost/signup_verify.html", {
        "error": error,
        "target": request.session.get("otp_target", ""),
        "debug_code": request.session.get("otp_debug", ""),
    })


# ---------------------------------------------------------------------------
# Вход по коду
# ---------------------------------------------------------------------------
def _find_user_by_identifier(identifier):
    identifier = (identifier or "").strip()
    if "@" in identifier:
        return (User.objects.filter(email__iexact=identifier)
                .exclude(is_superuser=True).order_by("id").first()), "email", identifier
    phone = otp_core.normalize_phone(identifier)
    try:
        prof = (UserProfile.objects
                .filter(phone=phone).select_related("user").first())
    except Exception:
        prof = None
    return (prof.user if prof else None), "sms", phone


def login_otp(request):
    if request.user.is_authenticated:
        return redirect("/")

    error = None
    form = {}
    if request.method == "POST":
        raw = (request.POST.get("identifier") or "").strip()
        form = {"identifier": raw}
        user, channel, target = _find_user_by_identifier(raw)
        if not raw:
            error = "Введите e-mail или телефон"
        elif user is None:
            error = "Аккаунт с такими данными не найден"
        else:
            _, debug = otp_core.start_otp(
                request, target, channel, purpose="login",
                extra={"user_id": user.id})
            request.session["otp_debug"] = debug or ""
            request.session["otp_target"] = target
            return redirect("/login/verify/")

    return render(request, "foodcost/login_otp.html", {"error": error, "form": form})


def login_verify(request):
    if request.user.is_authenticated:
        return redirect("/")

    error = None
    if request.method == "POST":
        ok, err, out = otp_core.verify_otp(
            request, request.POST.get("code"), purpose="login")
        if not ok:
            error = err
        else:
            uid = (out.get("extra") or {}).get("user_id")
            user = User.objects.filter(id=uid).first()
            if user is None:
                error = "Аккаунт не найден"
            else:
                request.session.pop("otp_debug", None)
                request.session.pop("otp_target", None)
                login(request, user, backend=_BACKEND)
                return redirect("/")

    return render(request, "foodcost/login_verify.html", {
        "error": error,
        "target": request.session.get("otp_target", ""),
        "debug_code": request.session.get("otp_debug", ""),
    })
