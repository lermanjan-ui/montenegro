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


def scope_locations(location_qs, user):
    """Сузить справочник складов/филиалов (Location) до филиалов пользователя.
    None из user_location_ids = полный доступ (супер-админ)."""
    ids = user_location_ids(user)
    return location_qs if ids is None else location_qs.filter(id__in=list(ids))


def scope_any_location(qs, user, fields=("from_warehouse", "to_warehouse")):
    """Документ виден, если ЛЮБОЙ из складов-полей принадлежит филиалам
    пользователя (хаб-правило для перемещений). None = полный доступ."""
    ids = user_location_ids(user)
    if ids is None:
        return qs
    from django.db.models import Q
    cond = Q()
    for f in fields:
        cond |= Q(**{f"{f}__in": list(ids)})
    return qs.filter(cond)


def scope_if_field(qs, user, field="location"):
    """Как scope_by_location, но тихо пропускает фильтр, если у модели нет
    такого поля (например, расход без привязки к филиалу)."""
    try:
        qs.model._meta.get_field(field)
    except Exception:
        return qs
    return scope_by_location(qs, user, field=field)
