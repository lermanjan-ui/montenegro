"""
🌐 Public API for Raccoon.uz website.

Read-only endpoints (Parts 2 & 4 catalog):
    GET  /api/public/locations
    GET  /api/public/categories
    GET  /api/public/products
    GET  /api/public/products/<slug:slug>
    GET  /api/public/search

Cart & order endpoints (Part 4 write API):
    POST /api/public/cart/calculate/
    POST /api/public/orders/create/
    GET  /api/public/orders/<public_order_number>/

Country awareness:
    Default country slug is "uzbekistan". Override via ?country_slug=... on
    GET endpoints, or via the "country_slug" key in the JSON body on POST
    endpoints. Models are not country-hardcoded; this module is the only
    place where Uzbekistan is treated as the public default.

This module intentionally exposes ONLY website-safe data:
    NO tech_card, NO cost/margin/foodcost, NO supplier/employee data.

Catalog rules (Parts 2 & legacy Part 4):
    - Categories use Dish.public_categories (M2M) with fallback to dish.category.
    - One dish can appear in multiple categories.
    - Product card weight = final_weight in grams (e.g. "520 г").
    - Product card cooking_time = cooking_minutes (e.g. "25 мин").
    - Gallery comes from DishGalleryImage active uploaded images.
    - Addons come from DishAddon (Dish-as-addon), grouped by group_name.
      The legacy AddonGroup / AddonItem / DishAddonGroup / CategoryAddonGroup
      models remain in the schema but are no longer surfaced here.

Media URLs (Part 6):
    - Every image field surfaced by this API (dish.photo, dish.gallery,
      category.photo) is returned as an ABSOLUTE URL via
      request.build_absolute_uri(file.url). When the field is empty the API
      returns null, never an empty string or a bare "/media/..." path.
    - Whether the URL points at local /media/ or at an external storage
      (S3 / Cloudinary / Render disk-mounted host) is decided by Django's
      configured storage backend — this module does not hard-code paths.
    - On production with DEBUG=False, Django itself does NOT serve /media/.
      Mounting a persistent disk + a media-serving route OR switching to an
      S3/Cloudinary backend is required for these absolute URLs to actually
      load in a browser.

Cart & order rules (Part 4 write API):
    - Backend ALWAYS recalculates totals server-side; frontend totals are
      never trusted.
    - addons[] is a list of Dish IDs that must exist in DishAddon links to
      the parent dish AND must themselves be available.
    - Per-location DishAvailability is honored for both dishes and addons.
    - Delivery price comes from DeliveryZone when delivery_zone_id is given;
      otherwise 0. If subtotal ≥ zone.free_delivery_threshold → free.
    - public_order_number format: RCN-{year}-{order.id:06d} (unique by PK).
"""

import json
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Min, Q
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST
from django.views.decorators.csrf import csrf_exempt

from .models import (
    Country,
    Location,
    DishCategory,
    Dish,
    DishAvailability,
    DishGalleryImage,
    DishAddon,
    # Part 4 — write API
    Order,
    OrderItem,
    OrderSource,
    Customer,
    CustomerAddress,
    DeliveryZone,
    # Part 5 — checkout support
    PromoCode,
    PaymentMethod,
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


def _resolve_image(request, file_field, external_url):
    """
    Resolve which image URL to return to the frontend, with strict priority:

      1. external_url — if non-empty (after strip), use it AS-IS. This is a
                        full external URL (CDN / Telegram / any direct link)
                        and is NOT wrapped in build_absolute_uri.
      2. file_field   — if an upload exists, return its absolute URL via
                        _abs_url(request, file_field).
      3. None         — no image at all.
    """
    raw_url = (external_url or "").strip()
    if raw_url:
        return raw_url
    return _abs_url(request, file_field)


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


def _is_addon_available(dish, location=None):
    """
    Availability for an addon item (a Dish used as a modifier of another Dish).

    Differs from is_dish_available() because addon dishes are typically NOT
    listed in the public catalog at all (they exist only to be picked from a
    parent dish's add-on list), so `is_visible_on_site` is irrelevant here.

    Rules:
      - dish.is_stop_list True  → unavailable (hard global stop)
      - per-location DishAvailability row exists and:
            * is_available=False → unavailable
            * is_stop_list=True   → unavailable
      - otherwise → available.
    """
    if dish is None:
        return False
    if getattr(dish, "is_stop_list", False):
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
    """
    Public category payload.

    Returns:
      - "id"          : DishCategory PK
      - "name"        : human-readable name (public_name → name fallback,
                        same convention as serialize_location)
      - "public_name" : raw public_name field (may be empty string)
      - "slug"        : URL slug
      - "photo"       : absolute URL or None — built via build_absolute_uri
                        on category.photo.url, so the storage backend
                        (local FileSystemStorage / S3 / Cloudinary) decides
                        the actual URL.
      - "sort_order"  : site_sort_order (int)
      - "min_price"   : lowest visible-dish price in this category, or None
    """
    return {
        "id": category.id,
        "name": _display_name(category),
        "public_name": (category.public_name or "").strip(),
        "slug": category.slug or "",
        "photo": _resolve_image(
            request,
            category.photo,
            getattr(category, "photo_url", "") or "",
        ),
        "min_price": _to_float(min_price) if min_price is not None else None,
        "sort_order": int(category.site_sort_order or 0),
    }


def _weight_grams_value(dish):
    """
    Return weight in grams as an int, or None if unknown.

    Conventions in this project:
      - Dish.final_weight is stored in KILOGRAMS (Decimal). 0.520 → 520 g.
      - There is no explicit grams field on Dish; the public_weight field is
        a free-form display string (e.g. "350 г", "0.5 кг", "S/M/L").
    """
    weight_kg = getattr(dish, "final_weight", None)
    if weight_kg is None:
        return None
    try:
        grams = int(round(float(weight_kg) * 1000))
    except (TypeError, ValueError):
        return None
    if grams <= 0:
        return None
    return grams


def _format_weight(dish):
    """
    Public weight display string. Priority:
      1. Explicit dish.public_weight if set.
      2. final_weight (kg) → "X г".
      3. Empty string only if absolutely nothing is known.

    The numeric value is exposed separately via _weight_grams_value so the
    frontend can format it however it wants.
    """
    raw = (getattr(dish, "public_weight", "") or "").strip()
    if raw:
        return raw
    grams = _weight_grams_value(dish)
    if grams is not None:
        return f"{grams} г"
    return ""


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
        "image": _resolve_image(
            request,
            dish.photo,
            getattr(dish, "photo_url", "") or "",
        ),
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
    """
    Serialize one DishAddon link as a website-ready addon item.

    The flat shape is what the website cart uses; `group` carries the
    DishAddon.group_name so a UI can still render them grouped if it
    wants to, without an extra round of grouping logic.
    """
    addon = link.addon_dish
    group_label = (link.group_name or "").strip() or "Дополнительно"
    return {
        "id": addon.id,
        "name": _display_name(addon),
        "slug": addon.slug or "",
        "price": _to_float(addon.selling_price),
        # NB: addons use _is_addon_available, not is_dish_available, because
        # operators normally keep addon dishes hidden from the main catalog
        # (is_visible_on_site=False). For addons we only care about stop-list
        # and per-location availability.
        "is_available": _is_addon_available(addon, location=location),
        "group": group_label,
        "image": _resolve_image(
            request,
            addon.photo,
            getattr(addon, "photo_url", "") or "",
        ),
    }


def _build_dish_addons(request, dish, location=None):
    """
    Centralised addon collector — single DB hit, two output shapes.

    Returns a dict:
        {
          "flat":   [ {id, name, slug, price, is_available, group, image}, ... ],
          "groups": [ {"name": "Соусы", "items": [...]}, ... ],
        }

    Filtering policy:
      - Only DishAddon.is_active = True. The link itself decides whether the
        addon is offered for this dish.
      - We do NOT additionally require addon_dish.is_visible_on_site or
        addon_dish.is_stop_list = False. Addons are typically things like
        "Сырный бортик" or "Двойная порция соуса" — they are stored as Dish
        rows so they can have a selling_price + cost, but operators normally
        keep them OFF the main public catalog. Filtering them out by
        is_visible_on_site would silently empty out every product's addon
        list, which is exactly the bug we're fixing.
      - Instead, per-link availability is reflected on each item's
        `is_available` flag so the frontend can grey them out at run time.
        Per-location DishAvailability is honoured the same way (via
        is_dish_available inside _serialize_addon_entry).
    """
    links = (
        DishAddon.objects.filter(dish=dish, is_active=True)
        .select_related("addon_dish", "addon_dish__category")
        .order_by("group_name", "sort_order", "id")
    )

    flat = []
    grouped = {}
    group_order = []

    for link in links:
        addon = link.addon_dish
        # Defensive: if a DishAddon row points at a deleted dish (FK kept
        # by CASCADE so this normally can't happen), skip silently.
        if addon is None:
            continue

        entry = _serialize_addon_entry(request, link, location=location)
        flat.append(entry)

        group_key = entry["group"]
        if group_key not in grouped:
            grouped[group_key] = []
            group_order.append(group_key)
        grouped[group_key].append(entry)

    return {
        "flat": flat,
        "groups": [
            # `id` is a synthetic 1-based index. There is no separate
            # AddonGroup table in the current schema — groups are derived
            # from DishAddon.group_name labels — so we expose a stable
            # per-response index so the frontend can use it as a React key.
            {"id": idx + 1, "name": k, "items": grouped[k]}
            for idx, k in enumerate(group_order)
        ],
    }


def _collect_dish_addons(request, dish, location=None):
    """
    Back-compat wrapper: returns the GROUPED shape.

    Kept so any older caller that imported _collect_dish_addons keeps
    working. New code should use _build_dish_addons() directly.
    """
    return _build_dish_addons(request, dish, location=location)["groups"]


# -----------------------------------------------------------------------------
# 🎯 Upsell / "you may also like" — auto-derived from the same categories.
# -----------------------------------------------------------------------------

DEFAULT_UPSELL_LIMIT = 4


def _build_upsell_products(request, dish, location=None, limit=DEFAULT_UPSELL_LIMIT):
    """
    Build "upsell_products" — other visible, available dishes from the same
    public categories as `dish` (or from the legacy single `category` if
    public_categories is empty). Self is excluded.

    Output shape matches the spec:
        [
          {"id": 52, "name": "Cola", "slug": "cola",
           "image": "https://...", "price": 12000, "is_available": true},
          ...
        ]

    Image priority: photo_url → uploaded photo absolute URL → null,
    same as elsewhere in this module.

    No new DB tables / no manual relations — the picks are derived from
    the existing category links, so this works for any project without
    extra setup.
    """
    if limit <= 0:
        return []

    category_ids = _dish_public_category_ids(dish)
    if not category_ids:
        return []

    # Base queryset: same country, visible, not in global stop-list,
    # excluding the current dish.
    qs = (
        _visible_dishes_qs(dish.country, location=location)
        .filter(
            Q(public_categories__id__in=category_ids)
            | Q(
                category_id__in=category_ids,
                public_categories__isnull=True,
            )
        )
        .exclude(id=dish.id)
        .distinct()
        .order_by("site_sort_order", "name")
    )

    items = []
    seen_ids = set()
    for candidate in qs[: limit * 3]:  # over-fetch in case dup IDs leak in
        if candidate.id in seen_ids:
            continue
        seen_ids.add(candidate.id)
        items.append({
            "id": candidate.id,
            "name": _display_name(candidate),
            "slug": candidate.slug or "",
            "image": _resolve_image(
                request,
                candidate.photo,
                getattr(candidate, "photo_url", "") or "",
            ),
            "price": _to_float(candidate.selling_price),
            "is_available": is_dish_available(candidate, location=location),
        })
        if len(items) >= limit:
            break

    return items


def serialize_product_detail(request, dish, location=None):
    """
    Public product detail payload.

    Stable fields (do NOT remove — the website depends on them):
        id, category_id, category_slug, category_ids,
        name, slug, composition, short_description, public_description,
        image, gallery,
        weight, cooking_time, spice_level,
        price, old_price, badge,
        is_new, is_featured, is_spicy, is_vegetarian, is_available,
        addons, upsell_products.

    Newly populated (were always present but were empty / missing data):
        - "weight"           — never empty when final_weight is known
        - "weight_grams"     — numeric weight in grams, or null
        - "final_weight_kg"  — raw final_weight as float, or null
        - "addons"           — flat list per the website spec
        - "addon_groups"     — original grouped shape (back-compat)
        - "upsell_products"  — auto-derived from the same categories
        - "image"            — now respects photo_url first
    """
    primary_cat = _primary_category_for_card(dish)
    addons_payload = _build_dish_addons(request, dish, location=location)

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
        "image": _resolve_image(
            request,
            dish.photo,
            getattr(dish, "photo_url", "") or "",
        ),
        "gallery": _serialize_dish_gallery(request, dish),
        # Weight: keep the stable string field for back-compat, and expose
        # numeric variants so the frontend can format itself if it wants.
        "weight": _format_weight(dish),
        "weight_grams": _weight_grams_value(dish),
        "final_weight_kg": _to_float(getattr(dish, "final_weight", None)),
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
        # Flat list per the website spec — what the cart UI consumes.
        "addons": addons_payload["flat"],
        # Grouped shape — kept for any older client that already used it.
        "addon_groups": addons_payload["groups"],
        # Auto-derived recommendations from the same public categories.
        "upsell_products": _build_upsell_products(request, dish, location=location),
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

    # Eager-load every relation the serializer / addon / upsell / gallery
    # helpers will touch, so the whole detail view is a small handful of
    # queries instead of one-per-addon and one-per-gallery-image.
    dish = (
        Dish.objects
        .filter(
            country=country,
            slug=slug,
            is_visible_on_site=True,
        )
        .select_related("category", "country")
        .prefetch_related(
            "public_categories",
            "gallery_images",
            "addon_links",
            "addon_links__addon_dish",
            "addon_links__addon_dish__category",
        )
        .first()
    )

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


# =============================================================================
# 🛒  PART 4 — CART / ORDER WRITE API
# =============================================================================
#
# All POST endpoints accept JSON bodies. Country resolution mirrors the GET
# helper above but reads "country_slug" from the parsed JSON payload instead
# of the query string. Totals are always recomputed server-side; nothing
# from the request body is trusted as authoritative.
#
# Public order numbers use the format RCN-{year}-{order.id:06d}, derived
# from the order's primary key right after creation. Uniqueness is then
# guaranteed by the PK.
# =============================================================================


# -----------------------------------------------------------------------------
# Status labels — single source of truth for the public order tracker.
# -----------------------------------------------------------------------------
#
# The spec lists six logical statuses (new / accepted / cooking / delivery /
# done / cancelled). Our Order.STATUS_CHOICES only has five (no "accepted").
# We expose all six labels here so the public tracker keeps working if an
# operator later introduces "accepted" as a custom status, but newly-created
# orders use the model's default ("new").
PUBLIC_STATUS_LABELS = {
    "new":       "Новый",
    "accepted":  "Принят",
    "cooking":   "Готовится",
    "delivery":  "В доставке",
    "done":      "Завершён",
    "cancelled": "Отменён",
}


def _status_label(status):
    """Return the public-facing label for a status code, with safe fallback."""
    return PUBLIC_STATUS_LABELS.get(status, status or "")


# -----------------------------------------------------------------------------
# 🚚 Delivery fee defaults (Part 5 — public checkout)
# -----------------------------------------------------------------------------
#
# When the request does not specify a delivery_zone_id, we fall back to these
# country-wide defaults. The spec for Uzbekistan public site:
#     delivery < 150 000 → fee = 15 000
#     delivery ≥ 150 000 → fee = 0      (free over threshold)
#     pickup             → fee = 0      (always)
#
# A non-null DeliveryZone in the request OVERRIDES these defaults — zone's
# own delivery_price and free_delivery_threshold are used instead, so this
# stays backward-compatible with Part 4 zone-based pricing.

DEFAULT_DELIVERY_FEE = Decimal("15000")
DEFAULT_FREE_DELIVERY_THRESHOLD = Decimal("150000")


# -----------------------------------------------------------------------------
# 💳 Payment method key mapping (Part 5 — public checkout)
# -----------------------------------------------------------------------------
#
# The public website sends payment_method as a short string ("cash" / "click" /
# "payme" / "online_card"). The ERP's Order model points at a PaymentMethod
# record by FK. To keep ERP and website in sync we resolve each key to a
# PaymentMethod row via get_or_create — see _resolve_payment_method below.
#
# `is_cash` is True only for "cash" so cashiers see the right indicator in
# the existing ERP. All other methods are non-cash.

PAYMENT_METHOD_KEYS = ("cash", "click", "payme", "online_card")

PAYMENT_METHOD_LABELS = {
    "cash":        "Наличные",
    "click":       "Click",
    "payme":       "Payme",
    "online_card": "Карта онлайн",
}


# -----------------------------------------------------------------------------
# Request parsing helpers
# -----------------------------------------------------------------------------

def _parse_json_body(request):
    """
    Parse the request body as JSON.

    Returns (payload, error_response). On success payload is a dict and the
    error is None; on failure payload is None and the error is a 400 JSON
    response with code INVALID_JSON.
    """
    try:
        raw = request.body.decode("utf-8") if request.body else ""
    except UnicodeDecodeError:
        return None, api_error(
            "INVALID_JSON",
            "Request body must be UTF-8 encoded JSON",
            status=400,
        )

    if not raw.strip():
        return {}, None

    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        return None, api_error(
            "INVALID_JSON",
            "Request body is not valid JSON",
            status=400,
        )

    if not isinstance(payload, dict):
        return None, api_error(
            "INVALID_JSON",
            "Request body must be a JSON object",
            status=400,
        )

    return payload, None


def _get_country_from_payload(payload):
    """
    Resolve the public country from a parsed JSON payload.

    Mirrors get_public_country() but reads from a dict (POST body) instead of
    request.GET. Returns (country, error_response).
    """
    requested_slug = str(payload.get("country_slug") or "").strip().lower()

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


def _coerce_int(value, default=None):
    """Best-effort int coercion. Returns default for None / invalid input."""
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _money(value):
    """Convert any numeric-ish input to a Decimal usable by DB fields."""
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0")


# -----------------------------------------------------------------------------
# Cart validation + pricing core
# -----------------------------------------------------------------------------
#
# Shared by both /cart/calculate/ and /orders/create/ so the calculation
# logic is implemented exactly once. The result includes per-item line
# details, the matching DishAddon objects (for later persistence), and
# the aggregated totals.
# -----------------------------------------------------------------------------

def _validate_and_price_cart(
    country,
    location,
    items_raw,
    *,
    delivery_zone=None,
    fulfillment_method=Order.FULFILLMENT_DELIVERY,
    promo_code=None,
):
    """
    Validate raw cart items and compute prices server-side.

    Args:
        country:           Country instance.
        location:          Location instance or None.
        items_raw:         List of {dish_id, quantity, addons[]} dicts.
        delivery_zone:     DeliveryZone instance or None.
        fulfillment_method:"delivery" or "pickup". Pickup forces delivery=0.
        promo_code:        PromoCode instance or None. When given, applies
                           percent discount to subtotal.

    Returns:
        (result, error_response)

    On success, `result` is a dict:
        {
            "lines":           [ ...per-item dicts ready for serialization... ],
            "line_objects":    [ {dish, quantity, addon_links, ...} ... ],
            "subtotal":        Decimal,
            "discount_amount": Decimal,
            "discount_percent":Decimal,
            "applied_promo":   PromoCode or None,
            "delivery_price":  Decimal,
            "total":           Decimal,
            "free_delivery":   bool,
            "fulfillment_method": str,
        }
    On failure, returns (None, api_error(...)).
    """
    if not isinstance(items_raw, list) or len(items_raw) == 0:
        return None, api_error(
            "EMPTY_CART",
            "Cart must contain at least one item",
            status=400,
        )

    lines = []
    line_objects = []
    subtotal = Decimal("0")

    for index, raw_item in enumerate(items_raw):
        if not isinstance(raw_item, dict):
            return None, api_error(
                "INVALID_JSON",
                "Each cart item must be a JSON object",
                details={"index": index},
                status=400,
            )

        dish_id = _coerce_int(raw_item.get("dish_id"))
        if not dish_id:
            return None, api_error(
                "DISH_NOT_FOUND",
                "Cart item is missing a valid dish_id",
                details={"index": index},
                status=400,
            )

        quantity = _coerce_int(raw_item.get("quantity"), default=1) or 1
        if quantity < 1:
            return None, api_error(
                "INVALID_QUANTITY",
                "Quantity must be at least 1",
                details={"index": index, "dish_id": dish_id, "quantity": quantity},
                status=400,
            )

        dish = (
            Dish.objects.filter(id=dish_id, country=country)
            .select_related("category")
            .first()
        )
        if dish is None:
            return None, api_error(
                "DISH_NOT_FOUND",
                "Dish does not exist in this country",
                details={"index": index, "dish_id": dish_id},
                status=404,
            )

        if not is_dish_available(dish, location=location):
            return None, api_error(
                "DISH_UNAVAILABLE",
                "Dish is currently unavailable",
                details={
                    "index": index,
                    "dish_id": dish.id,
                    "dish_name": _display_name(dish),
                },
                status=409,
            )

        # ---- Addons ----
        raw_addon_ids = raw_item.get("addons") or []
        if not isinstance(raw_addon_ids, list):
            return None, api_error(
                "INVALID_JSON",
                "Addons must be a list of dish IDs",
                details={"index": index, "dish_id": dish.id},
                status=400,
            )

        addons_payload = []
        addon_links_for_line = []
        addons_price = Decimal("0")

        if raw_addon_ids:
            addon_ids_clean = []
            for raw_aid in raw_addon_ids:
                aid = _coerce_int(raw_aid)
                if not aid:
                    return None, api_error(
                        "ADDON_UNAVAILABLE",
                        "Addon id is invalid",
                        details={
                            "index": index,
                            "dish_id": dish.id,
                            "addon_id": raw_aid,
                        },
                        status=400,
                    )
                addon_ids_clean.append(aid)

            # Only accept addons that are actually linked to this dish via
            # DishAddon, are themselves active links, and whose addon_dish
            # belongs to the same country. Anything else is rejected.
            valid_links = (
                DishAddon.objects.filter(
                    dish=dish,
                    is_active=True,
                    addon_dish_id__in=addon_ids_clean,
                    addon_dish__country=country,
                )
                .select_related("addon_dish")
            )
            valid_links_by_id = {l.addon_dish_id: l for l in valid_links}

            # Preserve the order the caller sent.
            for aid in addon_ids_clean:
                link = valid_links_by_id.get(aid)
                if link is None:
                    return None, api_error(
                        "ADDON_UNAVAILABLE",
                        "Addon is not attached to this dish or is inactive",
                        details={
                            "index": index,
                            "dish_id": dish.id,
                            "addon_id": aid,
                        },
                        status=409,
                    )

                addon_dish = link.addon_dish
                # Use _is_addon_available, not is_dish_available — addon dishes
                # are normally hidden from the public catalog
                # (is_visible_on_site=False); rejecting them here would make
                # order creation impossible for any product with addons.
                if not _is_addon_available(addon_dish, location=location):
                    return None, api_error(
                        "ADDON_UNAVAILABLE",
                        "Addon is currently unavailable",
                        details={
                            "index": index,
                            "dish_id": dish.id,
                            "addon_id": addon_dish.id,
                            "addon_name": _display_name(addon_dish),
                        },
                        status=409,
                    )

                addon_price = _money(addon_dish.selling_price)
                addons_price += addon_price
                addons_payload.append({
                    "id": addon_dish.id,
                    "name": _display_name(addon_dish),
                    "price": _to_float(addon_price),
                })
                addon_links_for_line.append(link)

        base_price = _money(dish.selling_price)
        per_unit = base_price + addons_price
        line_total = per_unit * Decimal(quantity)
        subtotal += line_total

        lines.append({
            "dish": {
                "id": dish.id,
                "name": _display_name(dish),
                "slug": dish.slug or "",
            },
            "quantity": quantity,
            "base_price": _to_float(base_price),
            "addons": addons_payload,
            "addons_price": _to_float(addons_price),
            "total_price": _to_float(line_total),
        })

        line_objects.append({
            "dish": dish,
            "quantity": quantity,
            "base_price": base_price,
            "addons_price": addons_price,
            "per_unit": per_unit,
            "total_price": line_total,
            "addon_links": addon_links_for_line,
        })

    # ---- Discount (promo code) ----
    discount_amount = Decimal("0")
    discount_percent = Decimal("0")
    if promo_code is not None:
        try:
            discount_percent = _money(promo_code.percent)
        except Exception:
            discount_percent = Decimal("0")
        if discount_percent > 0:
            # 2 dp rounding so cashier-facing totals stay clean.
            discount_amount = (subtotal * discount_percent / Decimal("100")).quantize(
                Decimal("0.01")
            )
            if discount_amount > subtotal:
                discount_amount = subtotal

    discounted_subtotal = subtotal - discount_amount

    # ---- Delivery ----
    delivery_price = Decimal("0")
    free_delivery = False

    if fulfillment_method == Order.FULFILLMENT_PICKUP:
        # Pickup is always free — irrespective of zone or threshold.
        delivery_price = Decimal("0")
        free_delivery = True
    elif delivery_zone is not None:
        # Zone overrides the country-wide defaults.
        threshold = _money(delivery_zone.free_delivery_threshold)
        if threshold > 0 and discounted_subtotal >= threshold:
            delivery_price = Decimal("0")
            free_delivery = True
        else:
            delivery_price = _money(delivery_zone.delivery_price)
    else:
        # Public-checkout default: 15 000 below threshold, free at/above.
        if discounted_subtotal >= DEFAULT_FREE_DELIVERY_THRESHOLD:
            delivery_price = Decimal("0")
            free_delivery = True
        else:
            delivery_price = DEFAULT_DELIVERY_FEE

    total = discounted_subtotal + delivery_price

    return {
        "lines": lines,
        "line_objects": line_objects,
        "subtotal": subtotal,
        "discount_amount": discount_amount,
        "discount_percent": discount_percent,
        "applied_promo": promo_code,
        "delivery_price": delivery_price,
        "total": total,
        "free_delivery": free_delivery,
        "fulfillment_method": fulfillment_method,
    }, None


def _resolve_delivery_zone(country, location, payload):
    """
    Resolve an optional DeliveryZone from the payload.

    Returns (zone_or_none, error_response).

    Rules:
      - If delivery_zone_id is omitted or 0, returns (None, None) → free.
      - If given, zone must exist, be active, belong to the same country,
        and (if a location was specified) match that location.
    """
    raw_zone_id = payload.get("delivery_zone_id")
    if raw_zone_id in (None, "", 0, "0"):
        return None, None

    zone_id = _coerce_int(raw_zone_id)
    if zone_id is None:
        return None, api_error(
            "INVALID_JSON",
            "delivery_zone_id must be an integer",
            status=400,
        )

    qs = DeliveryZone.objects.filter(
        id=zone_id,
        country=country,
        is_active=True,
    )
    if location is not None:
        qs = qs.filter(location=location)

    zone = qs.first()
    if zone is None:
        return None, api_error(
            "LOCATION_NOT_FOUND",
            "Delivery zone not found for this location",
            details={"delivery_zone_id": zone_id},
            status=404,
        )

    return zone, None


def _require_location_from_payload(country, payload, required=True):
    """
    Resolve location from the JSON body.

    If required=True and no valid location is found, returns an error.
    If required=False, returns (None, None) when no id is provided.
    """
    raw = payload.get("location_id")
    if raw in (None, "", 0, "0"):
        if required:
            return None, api_error(
                "LOCATION_NOT_FOUND",
                "location_id is required",
                status=400,
            )
        return None, None

    location_id = _coerce_int(raw)
    if location_id is None:
        return None, api_error(
            "LOCATION_NOT_FOUND",
            "location_id must be an integer",
            details={"location_id": raw},
            status=400,
        )

    location = Location.objects.filter(
        id=location_id,
        country=country,
        is_active=True,
    ).first()

    if location is None:
        return None, api_error(
            "LOCATION_NOT_FOUND",
            "Location does not exist in this country",
            details={"location_id": location_id},
            status=404,
        )

    return location, None


# -----------------------------------------------------------------------------
# 🚚 Fulfillment / promo / payment resolvers (Part 5 — public checkout)
# -----------------------------------------------------------------------------

def _resolve_fulfillment_method(payload):
    """
    Return one of Order.FULFILLMENT_DELIVERY / Order.FULFILLMENT_PICKUP.

    Unknown / missing values default to delivery (the spec's default mode).
    """
    raw = str(payload.get("fulfillment_method") or "").strip().lower()
    if raw == Order.FULFILLMENT_PICKUP:
        return Order.FULFILLMENT_PICKUP
    return Order.FULFILLMENT_DELIVERY


def _resolve_promo_code(country, payload, *, required=False):
    """
    Resolve an optional PromoCode by string code.

    Returns (promo_or_none, error_response).

    Lookup:
      - code is case-insensitive (we uppercase before searching).
      - filtered by country and is_active=True.

    Behavior:
      - empty / missing code → (None, None) when required=False;
        otherwise → (None, PROMO_INVALID error).
      - given code but not found / inactive → (None, PROMO_INVALID error).
    """
    raw = (payload.get("promo_code") or "")
    code = str(raw).strip().upper()
    if not code:
        if required:
            return None, api_error(
                "PROMO_INVALID",
                "Promo code is required",
                status=400,
            )
        return None, None

    promo = PromoCode.objects.filter(
        country=country,
        is_active=True,
        code__iexact=code,
    ).first()

    if promo is None:
        return None, api_error(
            "PROMO_INVALID",
            "Promo code is invalid or inactive",
            details={"promo_code": code},
            status=400,
        )

    return promo, None


def _resolve_payment_method(country, payload):
    """
    Resolve the payment method requested by the website.

    Accepted keys (spec): cash | click | payme | online_card.
    The website sends a short string; ERP stores a FK to PaymentMethod, so
    we keep an idempotent get_or_create mapping per country.

    Returns (payment_method_obj_or_none, key_or_empty, error_response).

    - Empty / missing → (None, "", None). This is valid: an order can be
      created without a chosen payment method (payment_status stays pending).
    - Unknown key      → (None, key, PAYMENT_METHOD_INVALID error).
    """
    raw = str(payload.get("payment_method") or "").strip().lower()
    if not raw:
        return None, "", None

    if raw not in PAYMENT_METHOD_KEYS:
        return None, raw, api_error(
            "PAYMENT_METHOD_INVALID",
            "Payment method is not supported",
            details={
                "payment_method": raw,
                "allowed": list(PAYMENT_METHOD_KEYS),
            },
            status=400,
        )

    label = PAYMENT_METHOD_LABELS[raw]
    payment_method, _created = PaymentMethod.objects.get_or_create(
        country=country,
        name=label,
        defaults={
            "is_cash": (raw == "cash"),
            "is_active": True,
        },
    )

    # If a pre-existing row had is_active=False, light it up so future orders
    # don't fail silently.
    if not payment_method.is_active:
        payment_method.is_active = True
        payment_method.save(update_fields=["is_active"])

    return payment_method, raw, None


# -----------------------------------------------------------------------------
# Order helpers — addon summary, public number, website order source
# -----------------------------------------------------------------------------

def _format_addon_summary_for_line(line):
    """
    Build a one-line, human-readable addon summary for cashier_comment.

    Example: "Пепперони ×2: + Сырный соус, + Картофель фри"
    """
    addon_names = [_display_name(l.addon_dish) for l in line["addon_links"]]
    if not addon_names:
        return None
    return "{name} ×{qty}: + {addons}".format(
        name=_display_name(line["dish"]),
        qty=line["quantity"],
        addons=", ".join(addon_names),
    )


def _build_cashier_addon_summary(line_objects):
    """Aggregate addon summaries across all lines into one block of text."""
    parts = []
    for line in line_objects:
        summary = _format_addon_summary_for_line(line)
        if summary:
            parts.append(summary)
    if not parts:
        return ""
    return "Допы с сайта:\n" + "\n".join(parts)


def _generate_public_order_number(order):
    """
    Build RCN-{year}-{id:06d} from an already-saved Order's PK.

    Uniqueness is guaranteed by the PK. The year prefix is taken from
    the order's created_at (auto_now_add) so reruns / backfills are
    consistent with when the order was placed.
    """
    year = (order.created_at or timezone.now()).year
    return "RCN-{year}-{pk:06d}".format(year=year, pk=order.id)


def _get_or_create_website_source(country):
    """Get or create the "Сайт" OrderSource for the given country."""
    source, _ = OrderSource.objects.get_or_create(
        country=country,
        name="Сайт",
        defaults={"is_active": True},
    )
    return source


def _get_or_create_website_customer(country, name, phone):
    """
    Resolve a Customer by phone in this country, or create a new one.

    Phone is normalized only by stripping whitespace; we do not silently
    rewrite it because operators rely on the exact format the user typed.
    """
    phone_clean = (phone or "").strip()
    name_clean = (name or "").strip() or phone_clean

    if not phone_clean:
        # The endpoint should have rejected this earlier; defensive fallback:
        return Customer.objects.create(
            country=country,
            phone="",
            name=name_clean or "Гость с сайта",
        )

    customer = Customer.objects.filter(
        country=country, phone=phone_clean
    ).first()
    if customer is not None:
        if name_clean and customer.name != name_clean:
            customer.name = name_clean
            customer.save(update_fields=["name"])
        return customer

    return Customer.objects.create(
        country=country,
        phone=phone_clean,
        name=name_clean,
    )


# -----------------------------------------------------------------------------
# Order serializers — only website-safe data
# -----------------------------------------------------------------------------

def _serialize_order_item_for_tracking(item):
    """Return a website-safe dict describing one OrderItem."""
    dish = item.dish
    return {
        "dish_id": dish.id if dish else None,
        "name": _display_name(dish) if dish else "",
        "quantity": _to_float(item.quantity),
        "price": _to_float(item.price_snapshot),
        "total_price": _to_float(item.total_price),
    }


def _serialize_order_for_tracking(order):
    """Return the public tracking payload for one Order. No internal data."""
    items_payload = [
        _serialize_order_item_for_tracking(it)
        for it in order.items.select_related("dish").all()
    ]
    return {
        "order_id": order.id,
        "public_order_number": order.public_order_number or "",
        "status": order.status,
        "status_label": _status_label(order.status),
        "created_at": order.created_at.isoformat() if order.created_at else None,
        "fulfillment_method": order.fulfillment_method or "",
        "customer_name": order.customer_name or "",
        "delivery_address": order.delivery_address or "",
        "subtotal": _to_float(order.subtotal_amount),
        "delivery_price": _to_float(order.delivery_amount),
        "total": _to_float(order.total_amount),
        "items": items_payload,
    }


# =============================================================================
# 🛒  ENDPOINT: POST /api/public/cart/calculate/
# =============================================================================

@csrf_exempt
@require_POST
def cart_calculate(request):
    """
    Recalculate cart totals from the items in the request body.

    The frontend sends raw items; we ignore any totals it might have computed
    and recompute everything from authoritative DB prices. Useful for the
    cart preview page right before order submission.

    Body (all optional except items):
        {
          "country_slug":    "uzbekistan",
          "location_id":     1,            # optional; affects availability
          "fulfillment_method": "delivery" | "pickup",  # default "delivery"
          "delivery_zone_id":1,            # optional; overrides default fee
          "promo_code":      "WELCOME10",  # optional
          "items": [ { "dish_id": ..., "quantity": ..., "addons": [..] }, ... ]
        }
    """
    payload, err = _parse_json_body(request)
    if err:
        return err

    country, err = _get_country_from_payload(payload)
    if err:
        return err

    # location is optional for cart calculation — it only affects availability.
    location, err = _require_location_from_payload(country, payload, required=False)
    if err:
        return err

    fulfillment_method = _resolve_fulfillment_method(payload)

    delivery_zone, err = _resolve_delivery_zone(country, location, payload)
    if err:
        return err
    if fulfillment_method == Order.FULFILLMENT_PICKUP:
        # Ignore any zone passed by the client when picking up — pickup is free.
        delivery_zone = None

    promo, err = _resolve_promo_code(country, payload, required=False)
    if err:
        return err

    result, err = _validate_and_price_cart(
        country=country,
        location=location,
        items_raw=payload.get("items") or [],
        delivery_zone=delivery_zone,
        fulfillment_method=fulfillment_method,
        promo_code=promo,
    )
    if err:
        return err

    return api_success({
        "items": result["lines"],
        "subtotal": _to_float(result["subtotal"]),
        "discount_amount": _to_float(result["discount_amount"]),
        "discount_percent": _to_float(result["discount_percent"]),
        "applied_promo_code": (
            result["applied_promo"].code if result["applied_promo"] else None
        ),
        "delivery_price": _to_float(result["delivery_price"]),
        "free_delivery": bool(result["free_delivery"]),
        "fulfillment_method": result["fulfillment_method"],
        "total": _to_float(result["total"]),
    })


# =============================================================================
# 🛒  ENDPOINT: POST /api/public/orders/create/
# =============================================================================

@csrf_exempt
@require_POST
def order_create(request):
    """
    Create an Order + OrderItem rows from the public website.

    Server recomputes prices from DB — never trusts frontend totals.
    Wrapped in a transaction so the order is either fully persisted (with
    public number, promo, payment method, etc.) or not at all.

    Body:
        {
          "country_slug":    "uzbekistan",
          "location_id":     1,
          "fulfillment_method": "delivery" | "pickup",
          "payment_method":  "cash" | "click" | "payme" | "online_card",
          "promo_code":      "WELCOME10",            # optional
          "delivery_zone_id":1,                       # optional
          "customer_name":   "...",
          "customer_phone":  "+998...",
          "delivery_address":"...",                   # required for delivery
          "comment":         "...",                   # optional
          "items":           [ ... ]
        }
    """
    payload, err = _parse_json_body(request)
    if err:
        return err

    country, err = _get_country_from_payload(payload)
    if err:
        return err

    fulfillment_method = _resolve_fulfillment_method(payload)

    # location_id is required for any order — pickup_point for pickup orders,
    # the serving branch for delivery orders.
    location, err = _require_location_from_payload(country, payload, required=True)
    if err:
        return err

    # For pickup orders we also require that this location actually allows
    # pickup. Operators may temporarily disable pickup on a specific branch.
    if (
        fulfillment_method == Order.FULFILLMENT_PICKUP
        and not getattr(location, "supports_pickup", True)
    ):
        return api_error(
            "LOCATION_NOT_FOUND",
            "Selected pickup point does not support pickup",
            details={"location_id": location.id},
            status=400,
        )

    customer_name = str(payload.get("customer_name") or "").strip()
    customer_phone = str(payload.get("customer_phone") or "").strip()
    delivery_address = str(payload.get("delivery_address") or "").strip()
    customer_comment = str(payload.get("comment") or "").strip()

    if not customer_name or not customer_phone:
        return api_error(
            "INVALID_JSON",
            "customer_name and customer_phone are required",
            status=400,
        )

    # Delivery address only required for delivery orders.
    if fulfillment_method == Order.FULFILLMENT_DELIVERY and not delivery_address:
        return api_error(
            "INVALID_JSON",
            "delivery_address is required for delivery orders",
            status=400,
        )

    delivery_zone, err = _resolve_delivery_zone(country, location, payload)
    if err:
        return err

    # Pickup orders ignore delivery fees entirely — drop any zone.
    if fulfillment_method == Order.FULFILLMENT_PICKUP:
        delivery_zone = None
        # The order's stored delivery_address column is a TextField (NOT NULL).
        # Keep an explicit, non-empty marker so cashier UI shows the right
        # context without confusing the address field.
        delivery_address = "Самовывоз"

    promo, err = _resolve_promo_code(country, payload, required=False)
    if err:
        return err

    payment_method_obj, payment_key, err = _resolve_payment_method(country, payload)
    if err:
        return err

    result, err = _validate_and_price_cart(
        country=country,
        location=location,
        items_raw=payload.get("items") or [],
        delivery_zone=delivery_zone,
        fulfillment_method=fulfillment_method,
        promo_code=promo,
    )
    if err:
        return err

    source = _get_or_create_website_source(country)
    customer = _get_or_create_website_customer(country, customer_name, customer_phone)

    # Decide initial payment_status from the chosen method:
    #   - cash               → CASH        (will be paid on hand-off)
    #   - online_card / click / payme → PENDING (gateway will finalize)
    #   - missing            → PENDING
    if payment_key == "cash":
        payment_status_value = Order.PAYMENT_STATUS_CASH
    else:
        payment_status_value = Order.PAYMENT_STATUS_PENDING

    # Assemble the cashier-facing summary: addon details + payment + promo.
    addon_summary = _build_cashier_addon_summary(result["line_objects"])
    extra_lines = []
    if payment_key:
        extra_lines.append(
            f"Оплата с сайта: {PAYMENT_METHOD_LABELS.get(payment_key, payment_key)}"
        )
    if fulfillment_method == Order.FULFILLMENT_PICKUP:
        extra_lines.append(f"Самовывоз из: {location.name}")
    if promo is not None and result["discount_amount"] > 0:
        extra_lines.append(
            f"Промокод {promo.code}: −{result['discount_percent']}% "
            f"(−{result['discount_amount']})"
        )
    cashier_comment = "\n\n".join(
        part for part in (addon_summary, "\n".join(extra_lines)) if part
    )

    with transaction.atomic():
        order = Order.objects.create(
            country=country,
            location=location,
            customer=customer,
            source=source,
            payment_method=payment_method_obj,
            promo_code=promo,
            order_date=timezone.now(),
            customer_name=customer_name,
            customer_phone=customer_phone,
            delivery_address=delivery_address,
            customer_comment=customer_comment,
            cashier_comment=cashier_comment,
            subtotal_amount=result["subtotal"],
            discount_amount=result["discount_amount"],
            delivery_amount=result["delivery_price"],
            total_amount=result["total"],
            status=Order.STATUS_NEW,
            fulfillment_method=fulfillment_method,
            payment_status=payment_status_value,
        )

        # Now we have order.id → generate public_order_number deterministically.
        order.public_order_number = _generate_public_order_number(order)
        order.save(update_fields=["public_order_number"])

        for line in result["line_objects"]:
            try:
                dish_cost = Decimal(str(line["dish"].calculate_cost() or 0))
            except Exception:
                dish_cost = Decimal("0")

            OrderItem.objects.create(
                order=order,
                dish=line["dish"],
                quantity=Decimal(line["quantity"]),
                price_snapshot=line["per_unit"],
                cost_snapshot=dish_cost,
                total_price=line["total_price"],
            )

    return api_success({
        "order_id": order.id,
        "public_order_number": order.public_order_number,
        "status": order.status,
        "status_label": _status_label(order.status),
        "fulfillment_method": order.fulfillment_method,
        "payment_method": payment_key or None,
        "payment_status": order.payment_status,
        "applied_promo_code": promo.code if promo else None,
        "subtotal": _to_float(order.subtotal_amount),
        "discount_amount": _to_float(order.discount_amount),
        "delivery_price": _to_float(order.delivery_amount),
        "total": _to_float(order.total_amount),
    })


# =============================================================================
# 🛒  ENDPOINT: GET /api/public/orders/<public_order_number>/
# =============================================================================

@csrf_exempt
@require_GET
def order_tracking(request, public_order_number):
    """
    Public order tracking endpoint — looks up an order by its public number.

    Returns ONLY website-safe data: no margin, no cost, no employee info,
    no internal cashier comments.
    """
    number = (public_order_number or "").strip()
    if not number:
        return api_error(
            "INVALID_JSON",
            "public_order_number is required",
            status=400,
        )

    order = (
        Order.objects.filter(public_order_number=number)
        .select_related("country", "location")
        .first()
    )

    if order is None:
        return api_error(
            "DISH_NOT_FOUND",  # generic "not found" — we do not leak whether
                               # the number ever existed.
            "Order not found",
            details={"public_order_number": number},
            status=404,
        )

    return api_success({
        "order": _serialize_order_for_tracking(order),
    })

# =============================================================================
# 🚏  ENDPOINT: GET /api/public/pickup-points
# =============================================================================

@csrf_exempt
@require_GET
def pickup_points(request):
    """
    List active public locations that support pickup.

    Filters:
      - country = resolved country
      - is_active = True
      - is_visible_on_site = True
      - supports_pickup = True

    Response shape is intentionally a superset of /api/public/locations so a
    pickup-aware frontend can either reuse the locations payload or call this
    dedicated endpoint and get the same fields.
    """
    country, err = get_public_country(request)
    if err:
        return err

    qs = Location.objects.filter(
        country=country,
        is_active=True,
        is_visible_on_site=True,
        supports_pickup=True,
    ).order_by("site_sort_order", "name")

    return api_success({
        "pickup_points": [serialize_location(request, loc) for loc in qs],
    })


# =============================================================================
# 🏷  ENDPOINT: POST /api/public/promo/check
# =============================================================================

@csrf_exempt
@require_POST
def promo_check(request):
    """
    Validate a promo code and (optionally) preview the discount for a given
    subtotal.

    Body:
        {
          "country_slug": "uzbekistan",
          "code":         "WELCOME10",
          "subtotal":     200000           # optional — number, integer or float
        }

    Success response:
        {
          "valid": true,
          "code": "WELCOME10",
          "percent": 10.00,
          "discount_amount": 20000.0       # only when subtotal was sent
        }

    Failure response (still 200 with success=true; the API surfaces the
    invalid state via "valid": false so the frontend can show a friendly
    message without crashing on 4xx):
        { "valid": false, "code": "WELCOME10" }
    """
    payload, err = _parse_json_body(request)
    if err:
        return err

    country, err = _get_country_from_payload(payload)
    if err:
        return err

    raw_code = str(payload.get("code") or payload.get("promo_code") or "").strip()
    if not raw_code:
        return api_error(
            "PROMO_INVALID",
            "Promo code is required",
            status=400,
        )

    promo = PromoCode.objects.filter(
        country=country,
        is_active=True,
        code__iexact=raw_code.upper(),
    ).first()

    if promo is None:
        return api_success({
            "valid": False,
            "code": raw_code,
        })

    response = {
        "valid": True,
        "code": promo.code,
        "percent": _to_float(promo.percent),
    }

    # Optional subtotal preview.
    raw_subtotal = payload.get("subtotal")
    if raw_subtotal is not None:
        subtotal = _money(raw_subtotal)
        if subtotal < 0:
            subtotal = Decimal("0")
        percent = _money(promo.percent)
        discount = (subtotal * percent / Decimal("100")).quantize(Decimal("0.01"))
        if discount > subtotal:
            discount = subtotal
        response["subtotal"] = _to_float(subtotal)
        response["discount_amount"] = _to_float(discount)
        response["total_after_discount"] = _to_float(subtotal - discount)

    return api_success(response)


# =============================================================================
# 👤  ENDPOINT: POST /api/public/customers/lookup
# =============================================================================

def _serialize_customer_address(address):
    """Public-safe address payload — same shape used by the checkout page."""
    return {
        "id": address.id,
        "address": address.address or "",
        "comment": address.comment or "",
        "is_default": bool(address.is_default),
        "apartment": address.apartment or "",
        "entrance": address.entrance or "",
        "floor": address.floor or "",
        "intercom": address.intercom or "",
        "landmark": address.landmark or "",
        "courier_comment": address.courier_comment or "",
        "latitude": _to_float(address.latitude),
        "longitude": _to_float(address.longitude),
        "location_id": address.location_id,
    }


@csrf_exempt
@require_POST
def customers_lookup(request):
    """
    Look up a returning customer by phone.

    Body:
        {
          "country_slug": "uzbekistan",
          "phone":        "+998901234567"
        }

    Success response (customer not found):
        { "found": false, "phone": "+998..." }

    Success response (customer found):
        {
          "found":     true,
          "phone":     "+998...",
          "customer":  { id, name, phone, is_regular },
          "addresses": [ { ... }, ... ]      # at most 10 most recent
        }

    Privacy notes:
      - We do NOT return `is_problematic`, `comment`, or any ERP-only data.
      - We do NOT distinguish "no customer in this country" from "customer
        exists but is hidden": both yield {"found": false}. This avoids
        leaking which numbers are in the system from a public endpoint.
    """
    payload, err = _parse_json_body(request)
    if err:
        return err

    country, err = _get_country_from_payload(payload)
    if err:
        return err

    phone = str(payload.get("phone") or payload.get("customer_phone") or "").strip()
    if not phone:
        return api_error(
            "INVALID_JSON",
            "phone is required",
            status=400,
        )

    customer = (
        Customer.objects
        .filter(country=country, phone=phone)
        .order_by("-updated_at")
        .first()
    )

    if customer is None:
        return api_success({
            "found": False,
            "phone": phone,
        })

    addresses_qs = (
        CustomerAddress.objects
        .filter(customer=customer)
        .order_by("-is_default", "-created_at")[:10]
    )

    return api_success({
        "found": True,
        "phone": phone,
        "customer": {
            "id": customer.id,
            "name": customer.name or "",
            "phone": customer.phone or "",
            "is_regular": bool(customer.is_regular),
        },
        "addresses": [_serialize_customer_address(a) for a in addresses_qs],
    })
