# -*- coding: utf-8 -*-
"""
Доступ по филиалам — вторая ось доступа (рядом с секциями allowed_sections).
Единая точка правды: какие филиалы видит пользователь. Используется формами
(Фаза 1) и операционными экранами (Фаза 2).
"""


def user_location_ids(user):
    """None  → все филиалы (супер-админ, is_superuser, или филиалы не назначены).
    set(id) → только эти филиалы."""
    if getattr(user, "is_superuser", False):
        return None
    profile = getattr(user, "profile", None)
    if profile is None:
        return None
    if profile.is_super_admin():
        return None
    ids = set(profile.locations.values_list("id", flat=True))
    if not ids and getattr(profile, "location_id", None):
        ids = {profile.location_id}  # back-compat со старым одиночным филиалом
    return ids or None  # пусто → все (ничего не ограничиваем)


def scope_by_location(qs, user, field="location"):
    """Сузить queryset по филиалам пользователя. None → без изменений."""
    ids = user_location_ids(user)
    if ids is None:
        return qs
    return qs.filter(**{f"{field}__in": list(ids)})


def user_allowed_locations(user, country):
    """Филиалы (Location) этой компании, доступные пользователю — для
    чекбоксов/выпадашек в формах. Админ/без привязки → все филиалы страны."""
    from .models import Location
    qs = Location.objects.filter(country=country).order_by("site_sort_order", "name")
    ids = user_location_ids(user)
    if ids is None:
        return qs
    return qs.filter(id__in=list(ids))
