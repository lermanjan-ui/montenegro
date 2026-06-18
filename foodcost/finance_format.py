from django import template

register = template.Library()


@register.filter
def money(value):
    """Деньги целыми, с пробелом-разделителем тысяч: 100 000 000.

    Совпадает с _fmt_money в shift_views (единый формат по проекту).
    """
    try:
        n = int(round(float(value or 0)))
    except (TypeError, ValueError):
        return value
    s = f"{abs(n):,}".replace(",", " ")
    return f"-{s}" if n < 0 else s
