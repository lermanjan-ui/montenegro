"""Uzum Tezkor (Retail API) — серверная часть. Этап 1: OAuth2 + каталог + доступность.

Uzum опрашивает НАС по своему контракту. Здесь реализованы:
  • POST  /security/oauth/token              — выдача токена (client_credentials)
  • GET   /v1/nomenclature/{storeId}/composition   — каталог (категории + товары)
  • GET   /v1/nomenclature/{storeId}/availability  — доступность (стоки)

storeId = id нашей точки (Location). Приём заказов — Этап 2.
Ошибки отдаём списком [{code, description}] по их схеме ErrorListV1.
"""

import hashlib
import secrets

from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from .models import Location, DishCategory, Dish, UzumApp

# Актуальная версия модели каталога по спецификации Uzum.
NOMENCLATURE_CT = "application/vnd.eda.picker.nomenclature.v1+json"


def _err(code, description, status):
    """Ответ-ошибка в формате ErrorListV1: массив [{code, description}]."""
    return JsonResponse(
        [{"code": code, "description": description}],
        status=status, safe=False,
    )


def _bearer_app(request):
    """UzumApp по токену из заголовка Authorization: Bearer, иначе None."""
    auth = request.META.get("HTTP_AUTHORIZATION", "") or ""
    if not auth.lower().startswith("bearer "):
        return None
    token = auth[7:].strip()
    if not token:
        return None
    return UzumApp.objects.filter(access_token=token, is_active=True).first()


def _store(store_id):
    try:
        return Location.objects.filter(id=int(store_id)).first()
    except (TypeError, ValueError):
        return None


def _dish_price(dish):
    """Цена для Uzum: своя uzum_price, иначе selling_price."""
    price = dish.uzum_price if dish.uzum_price is not None else dish.selling_price
    try:
        return float(price or 0)
    except (TypeError, ValueError):
        return 0.0


@csrf_exempt
def oauth_token(request):
    """OAuth2 client_credentials: выдаём access_token по client_id/secret."""
    if request.method != "POST":
        return _err(405, "Method not allowed", 405)

    client_id = (request.POST.get("client_id") or "").strip()
    client_secret = (request.POST.get("client_secret") or "").strip()
    grant_type = (request.POST.get("grant_type") or "").strip()

    if grant_type and grant_type != "client_credentials":
        return _err(400, "Unsupported grant_type", 400)

    app = UzumApp.objects.filter(client_id=client_id, is_active=True).first()
    if not app or app.client_secret != client_secret:
        return _err(401, "Invalid client credentials", 401)

    token = secrets.token_urlsafe(48)
    app.access_token = token
    app.token_issued_at = timezone.now()
    app.save(update_fields=["access_token", "token_issued_at"])

    return JsonResponse({
        "access_token": token,
        "token_type": "bearer",
        "scope": "read write",
    })


def composition(request, store_id):
    """Каталог точки: категории + товары в схеме PickerNomenclatureV1."""
    app = _bearer_app(request)
    if not app:
        return _err(401, "Access token missing or expired", 401)

    store = _store(store_id)
    if not store:
        return _err(404, "Restaurant not found", 404)

    country = store.country

    categories = [
        {
            "id": str(c.id),
            "name": (c.public_name or c.name or "").strip() or c.name,
            "sortOrder": int(c.site_sort_order or 0),
        }
        for c in DishCategory.objects.filter(country=country).order_by(
            "site_sort_order", "name"
        )
    ]

    items = []
    dishes = (
        Dish.objects.filter(country=country, is_archived=False)
        .select_related("category")
    )
    for d in dishes:
        if not d.category_id:
            continue
        price = _dish_price(d)
        if price <= 0:
            continue  # позиции с нулевой ценой Uzum отбрасывает

        grams = int(round(float(d.final_weight or 0) * 1000))
        if grams <= 0:
            grams = 1

        item = {
            "id": str(d.id),
            "categoryId": str(d.category_id),
            "name": (d.public_name or d.name or "").strip() or d.name,
            "description": {"general": (d.public_name or d.name or "").strip()},
            "images": [],
            "isCatchWeight": False,
            "measure": {"unit": "GRM", "value": grams, "quantum": 1},
            "price": price,
            "vendorCode": str(d.id),
            "barcode": {"value": str(d.id), "weightEncoding": "none"},
        }
        try:
            if d.old_price and float(d.old_price) > price:
                item["oldPrice"] = float(d.old_price)
        except (TypeError, ValueError):
            pass
        # Фискальные коды — заводим, но передаём только если заполнены.
        mxik = (getattr(d, "mxik_code", "") or "").strip()
        if mxik:
            sc = {"mxikCodeUz": mxik}
            pkg = (getattr(d, "package_code", "") or "").strip()
            if pkg:
                sc["packageCodeUz"] = pkg
            item["serviceCodesUz"] = sc

        items.append(item)

    return JsonResponse(
        {"categories": categories, "items": items},
        content_type=NOMENCLATURE_CT,
    )


def availability(request, store_id):
    """Доступность: {items:[{id, stock}]}. Нет в списке → недоступно.

    Точка выключена для Uzum (uzum_enabled=False) → пустой список = всё недоступно.
    Блюдо недоступно, если общий стоп, стоп Uzum, скрыто или цена 0.
    """
    app = _bearer_app(request)
    if not app:
        return _err(401, "Access token missing or expired", 401)

    store = _store(store_id)
    if not store:
        return _err(404, "Restaurant not found", 404)

    items = []
    if store.uzum_enabled:
        dishes = Dish.objects.filter(country=store.country, is_archived=False)
        for d in dishes:
            if not d.category_id:
                continue
            if _dish_price(d) <= 0:
                continue
            available = not (
                d.is_stop_list or d.uzum_stop or not d.is_visible_on_site
            )
            items.append({"id": str(d.id), "stock": 999 if available else 0})

    return JsonResponse({"items": items})
