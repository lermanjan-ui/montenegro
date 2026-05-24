"""
🌐 Public Read-Only API for Raccoon.uz website.

Part 2: menu / catalog read endpoints. No auth, GET only.

Endpoints (all under /api/public/):
    GET /api/public/locations
    GET /api/public/categories
    GET /api/public/products
    GET /api/public/products/<slug:slug>
    GET /api/public/search

Country awareness:
    Default country slug is "uzbekistan". Override via ?country_slug=...
    Models are not country-hardcoded; this module is the only place where
    Uzbekistan is treated as the public default.

This module intentionally exposes ONLY website-safe data:
    NO tech_card, NO cost/margin/foodcost, NO supplier/employee data.
"""

from decimal import Decimal

from django.db.models import Min, Q
from django.http import JsonResponse
from django.views.decorators.http import require_GET
from django.views.decorators.csrf import csrf_exempt

from .models import (
    Country,
    Location,
    DishCategory,
    Dish,
    DishAvailability,
    AddonGroup,
    DishAddonGroup,
    CategoryAddonGroup,
)


# =============================================================================
# 🔧 RESPONSE HELPERS
# =============================================================================

def api_success(data, status=200):
    """Standard success envelope."""
    return JsonResponse(
        {
            "success": True,
            "data": data,
        },
        status=status,
        json_dumps_params={"ensure_ascii": False},
    )


def api_error(code, message, details=None, status=400):
    """Standard error envelope."""
    return JsonResponse(
        {
            "success": False,
            "error": {
                "code": code,
                "message": message,
                "details": details or {},
            },
        },
        status=status,
        json_dumps_params={"ensure_ascii": False},
    )


# =============================================================================
# 🌍 COUNTRY RESOLUTION
# =============================================================================

DEFAULT_COUNTRY_SLUG = "uzbekistan"


def get_public_country(request):
    """
    Resolve the public country from request.

    Order of resolution:
      1. ?country_slug=... query param
      2. Default "uzbekistan"
      3. Fallback: any Country whose name contains "uzbek" (case-insensitive)

    Returns:
      (country, error_response)
      - On success: (Country instance, None)
      - On failure: (None, JsonResponse error)
    """
    requested_slug = (request.GET.get("country_slug") or "").strip().lower()

    if not requested_slug:
        requested_slug = DEFAULT_COUNTRY_SLUG

    country = Country.objects.filter(slug=requested_slug).first()

    if country is None:
        # Permissive fallback: e.g. name "Узбекистан" / "Uzbekistan"
        country = Country.objects.filter(name__icontains="uzbek").first()

    if country is None:
        return None, api_error(
            "COUNTRY_NOT_FOUND",
            "Public country is not configured",
            details={"requested_slug": requested_slug},
            status=404,
        )

    return country, None


# =============================================================================
# 🧭 SHARED HELPERS
# =============================================================================

def _to_float(value):
    """Convert Decimal/None to float/None for JSON serialization."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    return value


def _abs_url(request, file_field):
    """Build absolute URL for an ImageField; safely return None if empty."""
    if not file_field:
        return None
    try:
        url = file_field.url
    except (ValueError, AttributeError):
        return None
    if not url:
        return None
    if request is not None:
        try:
            return request.build_absolute_uri(url)
        except Exception:
            return url
    return url


def _safe_int(value, default, min_value=None, max_value=None):
    """Parse int from query param with bounds and a safe default."""
    try:
        result = int(value)
    except (TypeError, ValueError):
        return default
    if min_value is not None and result < min_value:
        result = min_value
    if max_value is not None and result > max_value:
        result = max_value
    return result


def _resolve_location(country, location_id_raw):
    """
    Resolve an optional location_id within the given country.
    Returns Location instance or None. Invalid IDs are silently ignored.
    """
    if not location_id_raw:
        return None
    try:
        location_id = int(location_id_raw)
    except (TypeError, ValueError):
        return None
    return Location.objects.filter(
        id=location_id,
        country=country,
    ).first()


# =============================================================================
# 🚦 DISH AVAILABILITY LOGIC
# =============================================================================

def is_dish_available(dish, location=None):
    """
    Determine whether a dish is currently available to website visitors.

    Rules:
      - dish.is_stop_list True  → unavailable
      - dish.is_visible_on_site False → unavailable
      - if location given and a DishAvailability row exists for
        (country, dish, location):
            * is_available=False → unavailable
            * is_stop_list=True   → unavailable
      - otherwise → available (no record means "available by default")
    """
    if dish is None:
        return False
    if getattr(dish, "is_stop_list", False):
        return False
    if not getattr(dish, "is_visible_on_site", False):
        return False

    if location is not None:
        availability = DishAvailability.objects.filter(
            country=dish.country,
            dish=dish,
            location=location,
        ).first()
        if availability is not None:
            if not availability.is_available:
                return False
            if availability.is_stop_list:
                return False

    return True


def _availability_subquery_excluded_dish_ids(country, location):
    """
    Return a set of Dish IDs that should be EXCLUDED from listings because of
    a per-location DishAvailability row marking them unavailable / stop-list.

    Returns an empty set when no location is provided.
    """
    if location is None:
        return set()

    blocked = DishAvailability.objects.filter(
        country=country,
        location=location,
    ).filter(
        Q(is_available=False) | Q(is_stop_list=True)
    ).values_list("dish_id", flat=True)

    return set(blocked)


# =============================================================================
# 🍽 SERIALIZERS
# =============================================================================

def _display_name(obj):
    """Return public_name if set, otherwise the internal name."""
    public = (getattr(obj, "public_name", "") or "").strip()
    if public:
        return public
    return obj.name or ""


def serialize_location(request, location):
    return {
        "id": location.id,
        "name": _display_name(location),
        "public_name": _display_name(location),
        "address": location.address or "",
        "phone": location.phone or "",
        "latitude": _to_float(location.latitude),
        "longitude": _to_float(location.longitude),
        "working_hours": location.working_hours or "",
        "supports_delivery": bool(location.supports_delivery),
        "supports_pickup": bool(location.supports_pickup),
        # Schedule-based open/closed logic comes in a later part.
        "is_open": True,
    }


def serialize_category(request, category, min_price=None):
    return {
        "id": category.id,
        "name": _display_name(category),
        "slug": category.slug or "",
        "photo": _abs_url(request, category.photo),
        "min_price": _to_float(min_price) if min_price is not None else None,
        "sort_order": int(category.site_sort_order or 0),
    }


def _dish_weight(dish):
    """Public weight string; falls back to final_weight formatted."""
    raw = (dish.public_weight or "").strip()
    if raw:
        return raw
    if dish.final_weight is None:
        return ""
    # Render Decimal weight without trailing zeros, e.g. 0.520 -> "0.52"
    value = Decimal(dish.final_weight).normalize()
    return f"{value} кг"


def serialize_product_card(request, dish, location=None):
    category = dish.category
    return {
        "id": dish.id,
        "category_id": category.id if category else None,
        "category_slug": (category.slug if (category and category.slug) else ""),
        "name": _display_name(dish),
        "slug": dish.slug or "",
        "image": _abs_url(request, dish.photo),
        "weight": _dish_weight(dish),
        "price": _to_float(dish.selling_price),
        "old_price": None,
        "badge": dish.badge or "",
        "is_available": is_dish_available(dish, location=location),
    }


def _serialize_addon_item(item):
    return {
        "id": item.id,
        "name": item.name,
        "price": _to_float(item.price),
        "is_available": bool(item.is_available),
    }


def _serialize_addon_group(group):
    items_qs = group.items.all().order_by("sort_order", "name")
    return {
        "id": group.id,
        "name": group.name,
        "code": group.code or "",
        "sort_order": int(group.sort_order or 0),
        "items": [_serialize_addon_item(it) for it in items_qs],
    }


def _collect_dish_addon_groups(dish):
    """
    Aggregate addon groups for a dish.
    Sources:
      - DishAddonGroup direct links to this dish
      - CategoryAddonGroup links to dish.category
    Only active groups are returned, with duplicates removed,
    ordered by (sort_order, name).
    """
    direct_ids = DishAddonGroup.objects.filter(
        dish=dish
    ).values_list("group_id", flat=True)

    category_ids = []
    if dish.category_id:
        category_ids = list(
            CategoryAddonGroup.objects.filter(
                category_id=dish.category_id,
            ).values_list("group_id", flat=True)
        )

    group_ids = set(direct_ids) | set(category_ids)
    if not group_ids:
        return []

    groups = AddonGroup.objects.filter(
        id__in=group_ids,
        is_active=True,
        country=dish.country,
    ).order_by("sort_order", "name")

    return [_serialize_addon_group(g) for g in groups]


def serialize_product_detail(request, dish, location=None):
    category = dish.category
    gallery_raw = dish.gallery or []
    # gallery is a JSONField default=list; keep it a list of strings.
    if not isinstance(gallery_raw, list):
        gallery_raw = []

    return {
        "id": dish.id,
        "category_id": category.id if category else None,
        "category_slug": (category.slug if (category and category.slug) else ""),
        "name": _display_name(dish),
        "slug": dish.slug or "",
        "composition": dish.composition or "",
        "short_description": dish.short_description or "",
        "public_description": dish.public_description or "",
        "image": _abs_url(request, dish.photo),
        "gallery": [str(item) for item in gallery_raw if item],
        "weight": _dish_weight(dish),
        "cooking_time": dish.cooking_time or "",
        "spice_level": dish.spice_level or "",
        "price": _to_float(dish.selling_price),
        "old_price": None,
        "badge": dish.badge or "",
        "is_new": bool(dish.is_new),
        "is_featured": bool(dish.is_featured),
        "is_spicy": bool(dish.is_spicy),
        "is_vegetarian": bool(dish.is_vegetarian),
        "is_available": is_dish_available(dish, location=location),
        "addons": _collect_dish_addon_groups(dish),
        # Reserved for later. Empty for now so the website can render safely.
        "upsell_products": [],
    }


# =============================================================================
# 🔍 QUERYSET BUILDERS
# =============================================================================

def _visible_dishes_qs(country, location=None):
    """
    Base queryset for dishes that should be shown publicly.
    Excludes dishes blocked per-location via DishAvailability.
    """
    qs = Dish.objects.filter(
        country=country,
        is_visible_on_site=True,
        is_stop_list=False,
    ).select_related("category")

    excluded = _availability_subquery_excluded_dish_ids(country, location)
    if excluded:
        qs = qs.exclude(id__in=excluded)

    return qs


# =============================================================================
# 📍 ENDPOINT: LOCATIONS
# =============================================================================

@csrf_exempt
@require_GET
def locations(request):
    country, err = get_public_country(request)
    if err:
        return err

    qs = Location.objects.filter(
        country=country,
        is_active=True,
        is_visible_on_site=True,
    ).order_by("site_sort_order", "name")

    return api_success({
        "locations": [serialize_location(request, loc) for loc in qs],
    })


# =============================================================================
# 🏷 ENDPOINT: CATEGORIES
# =============================================================================

@csrf_exempt
@require_GET
def categories(request):
    country, err = get_public_country(request)
    if err:
        return err

    location = _resolve_location(country, request.GET.get("location_id"))
    # fulfillment_method is accepted as a future-proofing param;
    # current logic does not branch on it yet.
    _ = request.GET.get("fulfillment_method")

    visible_dishes_qs = _visible_dishes_qs(country, location=location)

    # min_price per category among visible+available dishes
    aggregates = (
        visible_dishes_qs
        .values("category_id")
        .annotate(min_price=Min("selling_price"))
    )
    min_price_by_cat = {
        row["category_id"]: row["min_price"]
        for row in aggregates
        if row["category_id"] is not None
    }

    cats_qs = DishCategory.objects.filter(
        country=country,
        is_visible_on_site=True,
        id__in=min_price_by_cat.keys(),
    ).order_by("site_sort_order", "name")

    result = [
        serialize_category(request, cat, min_price=min_price_by_cat.get(cat.id))
        for cat in cats_qs
    ]

    return api_success({"categories": result})


# =============================================================================
# 🍕 ENDPOINT: PRODUCTS (LIST)
# =============================================================================

DEFAULT_PAGE_LIMIT = 24
MAX_PAGE_LIMIT = 100


@csrf_exempt
@require_GET
def products(request):
    country, err = get_public_country(request)
    if err:
        return err

    location = _resolve_location(country, request.GET.get("location_id"))
    category_slug = (request.GET.get("category_slug") or "").strip()
    search_term = (request.GET.get("search") or "").strip()
    _ = request.GET.get("fulfillment_method")  # reserved

    page = _safe_int(request.GET.get("page"), default=1, min_value=1)
    limit = _safe_int(
        request.GET.get("limit"),
        default=DEFAULT_PAGE_LIMIT,
        min_value=1,
        max_value=MAX_PAGE_LIMIT,
    )

    qs = _visible_dishes_qs(country, location=location)

    if category_slug:
        qs = qs.filter(category__slug=category_slug)

    if search_term:
        qs = qs.filter(
            Q(name__icontains=search_term)
            | Q(public_name__icontains=search_term)
            | Q(composition__icontains=search_term)
            | Q(short_description__icontains=search_term)
        )

    qs = qs.order_by("site_sort_order", "name")

    total = qs.count()
    start = (page - 1) * limit
    end = start + limit
    page_qs = qs[start:end]

    items = [serialize_product_card(request, d, location=location) for d in page_qs]

    return api_success({
        "products": items,
        "pagination": {
            "page": page,
            "limit": limit,
            "total": total,
        },
    })


# =============================================================================
# 🍕 ENDPOINT: PRODUCT DETAIL
# =============================================================================

@csrf_exempt
@require_GET
def product_detail(request, slug):
    country, err = get_public_country(request)
    if err:
        return err

    location = _resolve_location(country, request.GET.get("location_id"))

    dish = Dish.objects.filter(
        country=country,
        slug=slug,
        is_visible_on_site=True,
    ).select_related("category").first()

    if dish is None:
        return api_error(
            "PRODUCT_NOT_FOUND",
            "Product not found",
            details={"slug": slug},
            status=404,
        )

    return api_success({
        "product": serialize_product_detail(request, dish, location=location),
    })


# =============================================================================
# 🔎 ENDPOINT: SEARCH
# =============================================================================

@csrf_exempt
@require_GET
def search(request):
    country, err = get_public_country(request)
    if err:
        return err

    location = _resolve_location(country, request.GET.get("location_id"))
    _ = request.GET.get("fulfillment_method")  # reserved

    query = (request.GET.get("q") or "").strip()
    if not query:
        return api_error(
            "SEARCH_QUERY_REQUIRED",
            "Search query is required",
            status=400,
        )

    # --- product search ---
    dishes_qs = _visible_dishes_qs(country, location=location).filter(
        Q(name__icontains=query)
        | Q(public_name__icontains=query)
        | Q(composition__icontains=query)
        | Q(short_description__icontains=query)
    ).order_by("site_sort_order", "name")[:MAX_PAGE_LIMIT]

    products_payload = [
        serialize_product_card(request, d, location=location) for d in dishes_qs
    ]

    # --- category search ---
    # Match by name or public_name, restricted to categories that have at
    # least one visible+available dish.
    visible_dishes_qs = _visible_dishes_qs(country, location=location)
    category_ids_with_dishes = (
        visible_dishes_qs
        .values_list("category_id", flat=True)
        .distinct()
    )

    cats_qs = DishCategory.objects.filter(
        country=country,
        is_visible_on_site=True,
        id__in=[cid for cid in category_ids_with_dishes if cid is not None],
    ).filter(
        Q(name__icontains=query) | Q(public_name__icontains=query)
    ).order_by("site_sort_order", "name")

    # min_price for matched categories
    aggregates = (
        visible_dishes_qs
        .filter(category_id__in=cats_qs.values_list("id", flat=True))
        .values("category_id")
        .annotate(min_price=Min("selling_price"))
    )
    min_price_by_cat = {
        row["category_id"]: row["min_price"] for row in aggregates
    }

    categories_payload = [
        serialize_category(request, cat, min_price=min_price_by_cat.get(cat.id))
        for cat in cats_qs
    ]

    return api_success({
        "products": products_payload,
        "categories": categories_payload,
    })
