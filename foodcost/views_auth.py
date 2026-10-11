# -*- coding: utf-8 -*-
"""
Веб-авторизация юрлиц.

Модель входа:
  • ВХОД по ПАРОЛЮ (/login/): e-mail / телефон / логин + пароль. Один вход для
    всех — компания (e-mail или телефон), сотрудник (логин), супер-админ (любое).
  • ВОССТАНОВЛЕНИЕ (/login/forgot/ → /login/forgot/verify/): код (OTP) на
    e-mail или телефон → задать новый пароль. Код не шлётся в тестах/без каналов
    — тогда показывается на экране (otp_core).
  • РЕГИСТРАЦИЯ (/signup/ → /signup/verify/): данные компании → OTP подтверждает
    e-mail/телефон → создаём компанию+админа+триал, логиним.

Запасной вход по паролю для персонала остаётся на /login/password/.
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
MIN_PASSWORD_LEN = 6


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


def _looks_like_phone(raw):
    """Строка похожа на телефон: после нормализации ≥ 10 цифр."""
    phone = otp_core.normalize_phone(raw)
    return phone if len(phone) >= 10 else ""


def _find_user_by_identifier(identifier):
    """Найти пользователя по e-mail, телефону ИЛИ логину (username).
    Порядок: e-mail (есть «@») → телефон (похоже на номер) → логин."""
    identifier = (identifier or "").strip()
    if not identifier:
        return None
    if "@" in identifier:
        return User.objects.filter(email__iexact=identifier).order_by("id").first()
    phone = _looks_like_phone(identifier)
    if phone:
        prof = (UserProfile.objects
                .filter(phone=phone).select_related("user").first())
        if prof:
            return prof.user
    return User.objects.filter(username=identifier).first()


def _find_user_for_reset(raw):
    """Для восстановления: ищем ТОЛЬКО по каналу (e-mail или телефон), т.к.
    код шлём на канал. Возвращает (user, channel, target)."""
    raw = (raw or "").strip()
    if "@" in raw:
        user = User.objects.filter(email__iexact=raw).order_by("id").first()
        return user, "email", raw
    phone = otp_core.normalize_phone(raw)
    prof = (UserProfile.objects.filter(phone=phone).select_related("user").first()
            if phone else None)
    return (prof.user if prof else None), "sms", phone


# ---------------------------------------------------------------------------
# Вход по паролю
# ---------------------------------------------------------------------------
def login_password(request):
    if request.user.is_authenticated:
        return redirect("/")

    error = None
    form = {}
    if request.method == "POST":
        identifier = (request.POST.get("identifier") or "").strip()
        password = request.POST.get("password") or ""
        form = {"identifier": identifier}

        user = _find_user_by_identifier(identifier)
        if not identifier or not password:
            error = "Введите логин/почту/телефон и пароль"
        elif user is None or not user.is_active or not user.check_password(password):
            # Единое сообщение — не раскрываем, существует ли аккаунт.
            error = "Неверные данные для входа"
        else:
            login(request, user, backend=_BACKEND)
            return redirect("/")

    return render(request, "foodcost/login_password.html", {"error": error, "form": form})


# ---------------------------------------------------------------------------
# Восстановление пароля по коду
# ---------------------------------------------------------------------------
def forgot_password(request):
    if request.user.is_authenticated:
        return redirect("/")

    error = None
    form = {}
    if request.method == "POST":
        raw = (request.POST.get("identifier") or "").strip()
        form = {"identifier": raw}
        user, channel, target = _find_user_for_reset(raw)
        if not raw:
            error = "Введите e-mail или телефон"
        elif user is None:
            error = "Аккаунт с такими данными не найден"
        else:
            _, debug = otp_core.start_otp(
                request, target, channel, purpose="reset",
                extra={"user_id": user.id})
            request.session["otp_debug"] = debug or ""
            request.session["otp_target"] = target
            return redirect("/login/forgot/verify/")

    return render(request, "foodcost/forgot_password.html", {"error": error, "form": form})


def forgot_verify(request):
    if request.user.is_authenticated:
        return redirect("/")

    error = None
    if request.method == "POST":
        code = request.POST.get("code")
        new_password = request.POST.get("password") or ""
        if len(new_password) < MIN_PASSWORD_LEN:
            error = f"Пароль должен быть не короче {MIN_PASSWORD_LEN} символов"
        else:
            ok, err, out = otp_core.verify_otp(request, code, purpose="reset")
            if not ok:
                error = err
            else:
                uid = (out.get("extra") or {}).get("user_id")
                user = User.objects.filter(id=uid).first()
                if user is None:
                    error = "Аккаунт не найден"
                else:
                    user.set_password(new_password)
                    user.is_active = True
                    user.save()
                    request.session.pop("otp", None)
                    request.session.pop("otp_debug", None)
                    request.session.pop("otp_target", None)
                    login(request, user, backend=_BACKEND)
                    return redirect("/")

    return render(request, "foodcost/forgot_verify.html", {
        "error": error,
        "target": request.session.get("otp_target", ""),
        "debug_code": request.session.get("otp_debug", ""),
    })


# ---------------------------------------------------------------------------
# Регистрация (OTP подтверждает e-mail/телефон)
#   ВНИМАНИЕ: задание пароля при регистрации — отдельный шаг (нужен signup.html).
#   Пока новый админ создаётся без пароля; первый пароль задаётся через
#   «Восстановление пароля» по коду. (Будет заменено, когда добавим поле пароля
#   в форму регистрации.)
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
        password = request.POST.get("password") or ""
        password2 = request.POST.get("password2") or ""
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
        elif len(password) < MIN_PASSWORD_LEN:
            error = f"Пароль должен быть не короче {MIN_PASSWORD_LEN} символов"
        elif password != password2:
            error = "Пароли не совпадают"
        else:
            request.session["signup"] = {
                "company_name": name, "inn": inn,
                "phone": phone, "email": email, "password": password}
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
            _pwd = data.get("password") or ""
            if _pwd:
                user.set_password(_pwd)        # пароль из формы регистрации
            else:
                user.set_unusable_password()   # старые сессии без пароля
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
            request.session.pop("otp", None)
            request.session.pop("otp_debug", None)
            request.session.pop("otp_target", None)
            login(request, user, backend=_BACKEND)
            return redirect(f"/c/{country.slug}/")

    return render(request, "foodcost/signup_verify.html", {
        "error": error,
        "target": request.session.get("otp_target", ""),
        "debug_code": request.session.get("otp_debug", ""),
    })
