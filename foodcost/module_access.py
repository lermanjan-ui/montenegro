"""
Доступ арендатора к модулям системы.

tenant_allowed_modules(country):
    None  → ограничений нет (все модули). Так для арендаторов БЕЗ подписки —
            обратная совместимость с текущим единственным клиентом.
    set() → конкретный набор кодов модулей по подписке.

tenant_has_module(country, code):
    True, если ограничений нет или код входит в набор.
"""


def _subscription(country):
    if country is None:
        return None
    try:
        return country.subscription
    except Exception:
        return None


def tenant_allowed_modules(country):
    sub = _subscription(country)
    if sub is None:
        return None            # без подписки — без ограничений
    return sub.effective_module_codes()


def tenant_has_module(country, code):
    allowed = tenant_allowed_modules(country)
    return allowed is None or code in allowed
