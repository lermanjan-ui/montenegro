"""
🌐 Public Read-Only API for Raccoon.uz website.

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

Part 4 changes:
    - Categories use Dish.public_categories (M2M) with fallback to dish.category.
    - One dish can appear in multiple categories.
    - Product card weight = final_weight in grams (e.g. "520 г").
    - Product card cooking_time = cooking_minutes (e.g. "25 мин").
    - Gallery comes from DishGalleryImage active uploaded images.
    - Addons come from DishAddon (Dish-as-addon), grouped by group_name.
      The legacy AddonGroup / AddonItem / DishAddonGroup / CategoryAddonGroup
      models remain in the schema but are no longer surfaced here.
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
    DishGalleryImage,
    DishAddon,
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
# 🏷 CATEGORY RESOLUTION FOR A DISH
# =============================================================================

def _dish_public_categories(dish):
    """
    Return the categories a dish should appear under on the public site.

    Rule:
      - If dish.public_categories has entries → use those.
      - Otherwise fall back to the legacy dish.category FK (single category).
    Returns a list of DishCategory instances (possibly empty).
    """
    public = list(dish.public_categories.all())
    if public:
        return public
    if dish.category_id:
        return [dish.category]
    return []


def _dish_public_category_ids(dish):
    """Same as above but only IDs — used for filtering and aggregation."""
    return [c.id for c in _dish_public_categories(dish)]


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


def _format_weight(dish):
    """
    Public weight string. Priority:
      1. Explicit dish.public_weight if set.
      2. Convert final_weight (kg) to grams: 0.520 → "520 г".
    Returns "" if both are missing.
    """
    raw = (getattr(dish, "public_weight", "") or "").strip()
    if raw:
        return raw
    weight_kg = getattr(dish, "final_weight", None)
    if weight_kg is None:
        return ""
    try:
        grams = int(round(float(weight_kg) * 1000))
    except (TypeError, ValueError):
        return ""
    if grams <= 0:
        return ""
    return f"{grams} г"


def _format_cooking_time(dish):
    """
    Public cooking_time string. Priority:
      1. Explicit dish.cooking_time if set.
      2. Format cooking_minutes as "X мин".
    Returns "" if both are missing.
    """
    raw = (getattr(dish, "cooking_time", "") or "").strip()
    if raw:
        return raw
    minutes = getattr(dish, "cooking_minutes", None)
    if minutes is None:
        return ""
    try:
        minutes_int = int(round(float(minutes)))
    except (TypeError, ValueError):
        return ""
    if minutes_int <= 0:
        return ""
    return f"{minutes_int} мин"


def _primary_category_for_card(dish):
    """
    Pick a single category for the product card's category_id/category_slug
    fields. Prefer the first public_categories entry; fall back to dish.category.
    """
    cats = _dish_public_categories(dish)
    if cats:
        return cats[0]
    return None


def serialize_product_card(request, dish, location=None):
    primary_cat = _primary_category_for_card(dish)
    return {
        "id": dish.id,
        "category_id": primary_cat.id if primary_cat else None,
        "category_slug": (
            primary_cat.slug if (primary_cat and primary_cat.slug) else ""
        ),
        "name": _display_name(dish),
        "slug": dish.slug or "",
        "image": _abs_url(request, dish.photo),
        "weight": _format_weight(dish),
        "cooking_time": _format_cooking_time(dish),
        "price": _to_float(dish.selling_price),
        "old_price": None,
        "badge": dish.badge or "",
        "is_available": is_dish_available(dish, location=location),
    }


def _serialize_dish_gallery(request, dish):
    """Return absolute URLs for active gallery images, ordered by sort_order."""
    images = DishGalleryImage.objects.filter(
        dish=dish, is_active=True
    ).order_by("sort_order", "id")
    out = []
    for img in images:
        url = _abs_url(request, img.image)
        if url:
            out.append(url)
    return out


def _serialize_addon_entry(request, link, location=None):
    """Serialize a single DishAddon link as a website-ready addon item."""
    addon = link.addon_dish
    return {
        "id": addon.id,
        "name": _display_name(addon),
        "slug": addon.slug or "",
        "price": _to_float(addon.selling_price),
        "is_available": is_dish_available(addon, location=location),
    }


def _collect_dish_addons(request, dish, location=None):
    """
    Build the addons payload for a product detail.

    Returns a list of groups:
        [
          {"name": "Соусы", "items": [ {...}, {...} ]},
          {"name": "Добавить к пицце", "items": [ ... ]},
        ]
    Group label defaults to "Дополнительно" when DishAddon.group_name is empty.
    Only active links and addon dishes that are themselves visible on site
    are included (availability per-location is reflected in `is_available`).
    """
    links = (
        DishAddon.objects.filter(dish=dish, is_active=True)
        .select_related("addon_dish")
        .order_by("group_name", "sort_order", "id")
    )

    grouped = {}
    order = []
    for link in links:
        addon = link.addon_dish
        # Only show addons that are at least visible on site. Per-branch
        # availability is reflected via is_available on the serialized item.
        if not getattr(addon, "is_visible_on_site", False):
            continue
        if getattr(addon, "is_stop_list", False):
            continue

        key = (link.group_name or "").strip() or "Дополнительно"
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(_serialize_addon_entry(request, link, location=location))

    return [{"name": k, "items": grouped[k]} for k in order]


def serialize_product_detail(request, dish, location=None):
    primary_cat = _primary_category_for_card(dish)

    return {
        "id": dish.id,
        "category_id": primary_cat.id if primary_cat else None,
        "category_slug": (
            primary_cat.slug if (primary_cat and primary_cat.slug) else ""
        ),
        "category_ids": _dish_public_category_ids(dish),
        "name": _display_name(dish),
        "slug": dish.slug or "",
        "composition": dish.composition or "",
        "short_description": dish.short_description or "",
        "public_description": dish.public_description or "",
        "image": _abs_url(request, dish.photo),
        "gallery": _serialize_dish_gallery(request, dish),
        "weight": _format_weight(dish),
        "cooking_time": _format_cooking_time(dish),
        "spice_level": dish.spice_level or "",
        "price": _to_float(dish.selling_price),
        "old_price": None,
        "badge": dish.badge or "",
        "is_new": bool(dish.is_new),
        "is_featured": bool(dish.is_featured),
        "is_spicy": bool(dish.is_spicy),
        "is_vegetarian": bool(dish.is_vegetarian),
        "is_available": is_dish_available(dish, location=location),
        "addons": _collect_dish_addons(request, dish, location=location),
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


def _filter_dishes_by_category_slug(qs, category_slug):
    """
    Filter a Dish queryset by category slug, matching either:
      - a category in dish.public_categories, OR
      - the legacy dish.category FK (only used when public_categories is empty).
    """
    if not category_slug:
        return qs

    # Dishes that have public_categories set AND one of them matches.
    via_m2m = Q(public_categories__slug=category_slug)

    # Dishes whose legacy category matches AND who have NO public_categories.
    via_legacy = Q(
        category__slug=category_slug,
        public_categories__isnull=True,
    )

    return qs.filter(via_m2m | via_legacy).distinct()


def _dish_ids_in_category(qs, category):
    """
    Return the IDs from `qs` that should appear under `category` according to
    public_categories OR (legacy category fallback when public_categories empty).
    """
    via_m2m = Q(public_categories=category)
    via_legacy = Q(category=category, public_categories__isnull=True)
    return qs.filter(via_m2m | via_legacy).values_list("id", flat=True).distinct()


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
    _ = request.GET.get("fulfillment_method")  # reserved

    visible_dishes_qs = _visible_dishes_qs(country, location=location)

    # Collect public-category presence per category, with min_price.
    # A category is shown if at least one visible+available dish links to it
    # via public_categories OR via the legacy category fallback.
    cats_qs = DishCategory.objects.filter(
        country=country,
        is_visible_on_site=True,
    ).order_by("site_sort_order", "name")

    result = []
    for cat in cats_qs:
        # Compute as a queryset of dishes belonging to this category to
        # compute min_price safely.
        dishes_in_cat = visible_dishes_qs.filter(
            Q(public_categories=cat)
            | Q(category=cat, public_categories__isnull=True)
        ).distinct()

        agg = dishes_in_cat.aggregate(min_price=Min("selling_price"))
        min_price = agg["min_price"]

        if min_price is None:
            # No dishes link to this category → hide it.
            continue

        result.append(serialize_category(request, cat, min_price=min_price))

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
    qs = _filter_dishes_by_category_slug(qs, category_slug)

    if search_term:
        qs = qs.filter(
            Q(name__icontains=search_term)
            | Q(public_name__icontains=search_term)
            | Q(composition__icontains=search_term)
            | Q(short_description__icontains=search_term)
        )

    qs = qs.order_by("site_sort_order", "name").distinct()

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
    ).order_by("site_sort_order", "name").distinct()[:MAX_PAGE_LIMIT]

    products_payload = [
        serialize_product_card(request, d, location=location) for d in dishes_qs
    ]

    # --- category search ---
    visible_dishes_qs = _visible_dishes_qs(country, location=location)

    cats_qs = DishCategory.objects.filter(
        country=country,
        is_visible_on_site=True,
    ).filter(
        Q(name__icontains=query) | Q(public_name__icontains=query)
    ).order_by("site_sort_order", "name")

    categories_payload = []
    for cat in cats_qs:
        dishes_in_cat = visible_dishes_qs.filter(
            Q(public_categories=cat)
            | Q(category=cat, public_categories__isnull=True)
        ).distinct()

        agg = dishes_in_cat.aggregate(min_price=Min("selling_price"))
        min_price = agg["min_price"]
        if min_price is None:
            continue

        categories_payload.append(
            serialize_category(request, cat, min_price=min_price)
        )

    return api_success({
        "products": products_payload,
        "categories": categories_payload,
    })
