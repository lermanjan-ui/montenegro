"""
⭐️ Избранное (favorites) для вошедшего клиента — сайт и приложение.
Все эндпоинты требуют заголовок Authorization: Bearer <access_token>.
Ответы в обёртке { "success": true, "data": {...} }.

  GET    /api/app/favorites           -> { favorites:[карточки блюд], count }
  POST   /api/app/favorites           -> body { "dish_id": N }  добавить (идемпотентно)
                                         -> { is_favorite:true, dish_id, count }
  GET    /api/app/favorites/ids       -> { ids:[dish_id,...], count }  (сердечки/бейдж)
  GET    /api/app/favorites/<dish_id> -> { is_favorite, dish_id, count }
  DELETE /api/app/favorites/<dish_id> -> убрать -> { is_favorite:false, dish_id, count }

Избранное привязано к блюду (Dish). Дубли исключены (unique customer+dish).
"""

from django.views.decorators.csrf import csrf_exempt

from .models import CustomerFavorite, Dish
from .public_api import api_success, api_error, _parse_json_body, serialize_product_card
from .app_auth import _authenticate


def _fav_qs(customer):
    """Избранные записи клиента по существующим, не архивным блюдам."""
    return CustomerFavorite.objects.filter(
        customer=customer, dish__is_archived=False
    ).select_related("dish")


def _count(customer):
    return _fav_qs(customer).count()


@csrf_exempt
def favorites(request):
    customer, _row, err = _authenticate(request)
    if err:
        return err

    if request.method == "GET":
        rows = _fav_qs(customer).order_by("-created_at")
        cards = [serialize_product_card(request, f.dish) for f in rows]
        return api_success({"favorites": cards, "count": len(cards)})

    if request.method == "POST":
        payload, perr = _parse_json_body(request)
        if perr:
            return perr
        try:
            dish_id = int(payload.get("dish_id") or 0)
        except (TypeError, ValueError):
            dish_id = 0
        if dish_id <= 0:
            return api_error("INVALID_REQUEST", "Нужен dish_id", status=400)
        dish = Dish.objects.filter(
            id=dish_id, country=customer.country, is_archived=False
        ).first()
        if dish is None:
            return api_error("NOT_FOUND", "Блюдо не найдено", status=404)
        CustomerFavorite.objects.get_or_create(customer=customer, dish=dish)
        return api_success({"is_favorite": True, "dish_id": dish_id, "count": _count(customer)})

    return api_error("METHOD_NOT_ALLOWED", "Метод не поддерживается", status=405)


@csrf_exempt
def favorite_ids(request):
    customer, _row, err = _authenticate(request)
    if err:
        return err
    if request.method != "GET":
        return api_error("METHOD_NOT_ALLOWED", "Метод не поддерживается", status=405)
    ids = list(_fav_qs(customer).values_list("dish_id", flat=True))
    return api_success({"ids": ids, "count": len(ids)})


@csrf_exempt
def favorite_detail(request, dish_id):
    customer, _row, err = _authenticate(request)
    if err:
        return err

    if request.method == "GET":
        exists = CustomerFavorite.objects.filter(
            customer=customer, dish_id=dish_id, dish__is_archived=False
        ).exists()
        return api_success({"is_favorite": exists, "dish_id": dish_id, "count": _count(customer)})

    if request.method == "DELETE":
        CustomerFavorite.objects.filter(customer=customer, dish_id=dish_id).delete()
        return api_success({"is_favorite": False, "dish_id": dish_id, "count": _count(customer)})

    return api_error("METHOD_NOT_ALLOWED", "Метод не поддерживается", status=405)
