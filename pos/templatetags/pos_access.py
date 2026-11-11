"""Шаблонные фильтры для POS-доступов (гейтинг меню и экрана доступов)."""

from django import template

from ..access import pos_can, POS_CASHIER, POS_KDS, POS_SETTINGS

register = template.Library()


@register.filter
def get(d, key):
    """Доступ к значению словаря по ключу-переменной: {{ mydict|get:key }}."""
    try:
        return d.get(key)
    except AttributeError:
        return None


@register.filter
def pos_can_cashier(user):
    return pos_can(user, POS_CASHIER)


@register.filter
def pos_can_kds(user):
    return pos_can(user, POS_KDS)


@register.filter
def pos_can_settings(user):
    return pos_can(user, POS_SETTINGS)
