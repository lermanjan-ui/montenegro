"""POS-доступы поверх существующего механизма foodcost UserProfile.

Не меняем foodcost: POS-права — это дополнительные строки в том же
UserProfile.allowed_sections (JSON-список). Пока сотруднику не назначили ни
одной POS-роли, работает прежняя логика (секции «Заказы»/«Настройки», роль
kitchen_staff) — ничего не ломается. Как только POS-права назначены явно, они
и решают, что человек видит.

Возможности:
  pos_cashier  — касса/зал (создание и оплата заказов)
  pos_kds      — экран кухни KDS
  pos_settings — настройки кассы

Пресеты: официант = касса; повар = KDS; менеджер = всё.
"""

from django.http import HttpResponseForbidden

from django.utils.translation import gettext_lazy as _
from foodcost.models import UserProfile

POS_CASHIER = "pos_cashier"
POS_KDS = "pos_kds"
POS_SETTINGS = "pos_settings"
POS_CAPS = [POS_CASHIER, POS_KDS, POS_SETTINGS]

CAP_LABELS = {
    POS_CASHIER: _("Касса / зал"),
    POS_KDS: _("Кухня (KDS)"),
    POS_SETTINGS: _("Настройки кассы"),
}

ROLE_PRESETS = {
    "waiter": [POS_CASHIER],
    "cook": [POS_KDS],
    "manager": [POS_CASHIER, POS_KDS, POS_SETTINGS],
}
PRESET_LABELS = {"waiter": _("Официант"), "cook": _("Повар/кухня"), "manager": _("Менеджер")}


def _profile(user):
    return getattr(user, "profile", None)


def _is_admin_role(role):
    return role in (UserProfile.ROLE_SUPER_ADMIN, UserProfile.ROLE_ADMIN)


def pos_can(user, cap):
    """Есть ли у пользователя POS-право cap."""
    if not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False):
        return True
    prof = _profile(user)
    if prof is None:
        return False
    role = getattr(prof, "role", None)
    if _is_admin_role(role):
        return True
    secs = prof.allowed_sections or []
    explicit = [c for c in secs if c in POS_CAPS]
    if explicit:
        return cap in explicit
    # fallback: POS-роли не настраивались — по старым секциям/роли
    if cap == POS_CASHIER:
        return UserProfile.SECTION_ORDERS in secs
    if cap == POS_KDS:
        return role == UserProfile.ROLE_KITCHEN_STAFF or UserProfile.SECTION_ORDERS in secs
    if cap == POS_SETTINGS:
        return UserProfile.SECTION_SETTINGS in secs
    return False


def pos_require(user, cap):
    """Возвращает HttpResponseForbidden или None (как require_section_access)."""
    if pos_can(user, cap):
        return None
    return HttpResponseForbidden("Нет доступа к этому разделу кассы.")


def pos_caps_of(user):
    return {c: pos_can(user, c) for c in POS_CAPS}


def explicit_caps(profile):
    """Явно назначенные POS-права пользователя (без fallback)."""
    secs = profile.allowed_sections or []
    return [c for c in secs if c in POS_CAPS]


def set_pos_caps(profile, caps):
    """Записывает POS-права в allowed_sections, не затрагивая прочие секции."""
    keep = [c for c in (profile.allowed_sections or []) if c not in POS_CAPS]
    keep += [c for c in POS_CAPS if c in caps]
    profile.allowed_sections = keep
    profile.save(update_fields=["allowed_sections"])
