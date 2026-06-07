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
import math
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Min, Q
from django.http import JsonResponse
from . import promotions_engine
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
    OrderItemAddon,
    OrderSource,
    Customer,
    CustomerAddress,
    DeliveryZone,
    # Part 5 — checkout support
    PromoCode,
    PaymentMethod,
    # Part 11 — homepage CMS
    HomepageBanner,
    HomepageProductBlock,
    HomepageProductBlockItem,
    # Part 1 — homepage compact upsell (separate from frequently-bought)
    HomepageCompactUpsellBlock,
    HomepageCompactUpsellItem,
    # Combo banner pair for the "Комбо и акции" section of the homepage.
    HomeComboBanner,
)

# Promo code usage-limit validation. Lives in promo_rules so admin
# screens can reuse the same logic without importing public_api.
from .promo_rules import check_promo_usage

# 💳 Click payment integration — URL builder (Part 1) + callback (Part 2).
# Sub-package so future providers (Payme, online_card, ...) can sit next to
# it without bloating public_api.
from .payments.click import (
    build_click_payment_url,
    ClickConfigError,
    verify_click_signature,
    amounts_match,
    ACTION_PREPARE,
    ACTION_COMPLETE,
    ERROR_OK,
    ERROR_SIGN_CHECK_FAILED,
    ERROR_INCORRECT_AMOUNT,
    ERROR_ACTION_NOT_FOUND,
    ERROR_ALREADY_PAID,
    ERROR_USER_DOES_NOT_EXIST,
    ERROR_BAD_REQUEST,
    ERROR_TRANSACTION_CANCELLED,
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
        # Exposed for the pickup map (Yandex markers). The pickup_points
        # queryset already filters is_active=True, so this is always True
        # there; included explicitly because the pickup-points contract lists
        # it per-point, and it's harmless for other consumers of this shape.
        "is_active": bool(location.is_active),
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
        "sort_order_2": int(getattr(category, "home_block_2_sort_order", 0) or 0),
        "in_block_1": bool(getattr(category, "in_home_block_1", True)),
        "in_block_2": bool(getattr(category, "in_home_block_2", False)),
        # Доп. поля для карточки «ресторана» (блок 2). Все опциональны.
        "logo_image": _resolve_image(
            request,
            getattr(category, "logo", None),
            getattr(category, "logo_url", "") or "",
        ),
        "subtitle": (getattr(category, "subtitle", "") or "").strip(),
        "rating": _to_float(category.rating) if getattr(category, "rating", None) is not None else None,
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


def _specs_for(request, country_id):
    """Активные акции страны, кэшируются на request (без N+1 в листинге)."""
    cache = getattr(request, "_promo_specs_cache", None) if request is not None else None
    if cache is None:
        cache = {}
        if request is not None:
            setattr(request, "_promo_specs_cache", cache)
    if country_id not in cache:
        cache[country_id] = promotions_engine.load_active_promotions(country_id)
    return cache[country_id]


def _promo_display(request, dish):
    """Поля акции для карточки товара (с учётом ручной старой цены
    dish.old_price). При любой ошибке — цена как есть."""
    manual_old = getattr(dish, "old_price", None)
    try:
        specs = _specs_for(request, dish.country_id)
        disp = promotions_engine.display_for_dish(
            specs, dish.id, dish.selling_price, compare_at=manual_old
        )
        return {
            "price": _to_float(disp["price"]),
            "old_price": (
                _to_float(disp["old_price"]) if disp["old_price"] is not None else None
            ),
            "savings_label": disp["savings_label"],
            "promo_hint": disp["promo_hint"],
            "badges": disp["badges"],
        }
    except Exception:
        show_old = manual_old is not None and manual_old > dish.selling_price
        savings = None
        if show_old:
            diff = int(manual_old - dish.selling_price)
            savings = f"Экономия {diff:,} сум".replace(",", "\u00a0")
        return {
            "price": _to_float(dish.selling_price),
            "old_price": _to_float(manual_old) if show_old else None,
            "savings_label": savings,
            "promo_hint": None,
            "badges": [],
        }


def serialize_product_card(request, dish, location=None):
    primary_cat = _primary_category_for_card(dish)
    _promo_disp = _promo_display(request, dish)
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
        "price": _promo_disp["price"],
        "old_price": _promo_disp["old_price"],
        "badge": dish.badge or "",
        "badges": _promo_disp["badges"],
        "savings_label": _promo_disp["savings_label"],
        "promo_hint": _promo_disp["promo_hint"],
        # Маркетинговые признаки — те же, что в detail (/products/{slug}),
        # чтобы листинг и карточка были согласованы. Раньше листинг отдавал
        # только badge, из-за чего фронт видел у всех is_vegetarian=false и
        # не получал spice_level.
        "is_new": bool(dish.is_new),
        "is_spicy": bool(dish.is_spicy),
        "is_vegetarian": bool(dish.is_vegetarian),
        "is_featured": bool(dish.is_featured),
        "show_in_combo_block": bool(getattr(dish, "show_in_combo_block", False)),
        "spice_level": dish.spice_level or "",
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
    Build "upsell_products" for a dish detail page. Two sources, in order:

      1. MANUAL — DishUpsellLink rows curated by the operator. If at least
         one ACTIVE link exists, we use that list (regardless of how many
         of those targets are actually visible/available — see filtering
         below). The operator's choice wins.

      2. AUTO — same-category dishes, exactly like before. Used only when
         the dish has zero curated links, so existing dishes that nobody
         touched keep showing recommendations.

    The shape of each item is the SAME for both sources — the frontend
    can't tell which mode is active and doesn't need to.

    Filtering rules (same in both modes):
      - excluded: the dish itself
      - excluded: dishes not visible on site / in stop-list / archived
      - excluded: dishes blocked by per-location DishAvailability
      - sorted by manual sort_order (manual mode) or site_sort_order (auto)
      - capped at `limit` items
    """
    if limit <= 0:
        return []

    # ---- 1. Manual links first ----
    # Defensive import to avoid circular reference at module load.
    from .models import DishUpsellLink

    manual_links = list(
        DishUpsellLink.objects
        .filter(from_dish=dish, is_active=True)
        .select_related("to_dish")
        .order_by("sort_order", "id")
    )

    if manual_links:
        # We HAVE manual curation — use it. Even if every target turns out
        # to be hidden/archived, we return an empty list rather than
        # falling back to auto — the operator's explicit choice is "show
        # these items" and an empty result is still that choice.
        target_ids = [link.to_dish_id for link in manual_links]
        visible_qs = (
            _visible_dishes_qs(dish.country, location=location)
            .filter(id__in=target_ids)
            .exclude(id=dish.id)
        )
        visible_by_id = {d.id: d for d in visible_qs}

        items = []
        for link in manual_links:
            target = visible_by_id.get(link.to_dish_id)
            if target is None:
                # Dish was archived / hidden / stop-listed after being
                # added to the upsell list — silently skip it. We DO NOT
                # raise; the link row stays in admin so the operator can
                # see what's broken and re-enable the dish if intended.
                continue
            items.append({
                "id": target.id,
                "name": _display_name(target),
                "slug": target.slug or "",
                "image": _resolve_image(
                    request,
                    target.photo,
                    getattr(target, "photo_url", "") or "",
                ),
                "price": _to_float(target.selling_price),
                # New field per the upsell-link spec: human-readable weight.
                # Same formatter used in compact-upsell, so the cart and
                # product-detail strips look consistent.
                "weight": _format_weight(target),
                "is_available": is_dish_available(target, location=location),
            })
            if len(items) >= limit:
                break

        return items

    # ---- 2. Auto fallback (legacy behavior — keep stable for dishes
    # without any manual curation) ----
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
            # Match the manual-mode payload shape exactly.
            "weight": _format_weight(candidate),
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
    _promo_disp = _promo_display(request, dish)

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
        "price": _promo_disp["price"],
        "old_price": _promo_disp["old_price"],
        "badge": dish.badge or "",
        "badges": _promo_disp["badges"],
        "savings_label": _promo_disp["savings_label"],
        "promo_hint": _promo_disp["promo_hint"],
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
    Excludes dishes blocked per-location via DishAvailability, and dishes
    that have been archived (is_archived=True — permanently removed from
    the menu while preserved in old-order history).
    """
    qs = Dish.objects.filter(
        country=country,
        is_visible_on_site=True,
        is_stop_list=False,
        is_archived=False,
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
            is_archived=False,
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
    "new":              "Принят",
    "accepted":         "Принят",
    "awaiting_payment": "Ожидает оплаты",
    "payment_failed":   "Оплата не прошла",
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
# Online-payment TTL — kill orders that sat unpaid past the window
# -----------------------------------------------------------------------------
# Online-payment orders that sit in awaiting_payment longer than this are
# considered abandoned. We mark them auto_expired and refuse late callbacks
# from the gateway — so a user who clicks "Pay" 25 hours after creating the
# order can't suddenly revive it. Payme's own timeout is 12h, but it counts
# from CreateTransaction, not from our Order.created_at, so the two timers
# are NOT synchronized — see PaymeTransaction model docstring.
#
# 24h is a conservative window: long enough for a real user to come back
# the next day, short enough to keep the awaiting_payment list manageable.
# Set ORDER_AWAITING_PAYMENT_TTL_HOURS env to override.
import os as _os
try:
    PAYMENT_AWAITING_TTL_HOURS = int(
        _os.environ.get("ORDER_AWAITING_PAYMENT_TTL_HOURS", "24")
    )
    if PAYMENT_AWAITING_TTL_HOURS <= 0:
        PAYMENT_AWAITING_TTL_HOURS = 24
except (TypeError, ValueError):
    PAYMENT_AWAITING_TTL_HOURS = 24


def _payment_ttl_delta():
    """Return the TTL as a timedelta — used by expire / tracking views."""
    from datetime import timedelta
    return timedelta(hours=PAYMENT_AWAITING_TTL_HOURS)


def _order_expires_at(order):
    """
    When does this order's awaiting_payment window close?
    Returns a tz-aware datetime, or None if the order is not on the online
    payment timer (cash, paid, cancelled, etc.).
    """
    if order is None or order.created_at is None:
        return None
    if order.payment_status == Order.PAYMENT_STATUS_PAID:
        return None
    if order.payment_status == Order.PAYMENT_STATUS_CASH:
        return None
    if order.status not in (Order.STATUS_AWAITING_PAYMENT,
                            Order.STATUS_PAYMENT_FAILED):
        return None
    return order.created_at + _payment_ttl_delta()


def _maybe_lazy_expire(order):
    """
    Lazy auto-expire: if an awaiting_payment order is older than TTL, mark
    it expired in a single atomic+select_for_update transaction. Idempotent
    — already-expired or non-eligible orders are returned unchanged.

    Returns the (possibly mutated) order. Safe to call from any read path;
    the row is only locked when we ACTUALLY need to write.
    """
    if order is None or order.auto_expired:
        return order
    if order.status != Order.STATUS_AWAITING_PAYMENT:
        return order
    if order.payment_status == Order.PAYMENT_STATUS_PAID:
        return order
    if order.created_at is None:
        return order
    if (timezone.now() - order.created_at) < _payment_ttl_delta():
        return order

    # Time to expire. Lock the row to serialize with any in-flight callback
    # that might be promoting it to paid.
    with transaction.atomic():
        try:
            locked = Order.objects.select_for_update().get(pk=order.pk)
        except Order.DoesNotExist:
            return order
        # Re-check under the lock — a callback might have just confirmed
        # payment while we were waiting for the row.
        if locked.auto_expired:
            return locked
        if locked.payment_status == Order.PAYMENT_STATUS_PAID:
            return locked
        if locked.status != Order.STATUS_AWAITING_PAYMENT:
            return locked
        if (timezone.now() - locked.created_at) < _payment_ttl_delta():
            return locked

        locked.auto_expired = True
        locked.payment_status = Order.PAYMENT_STATUS_EXPIRED
        locked.status = Order.STATUS_PAYMENT_FAILED
        locked.save(update_fields=[
            "auto_expired", "payment_status", "status",
        ])
        return locked


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
    collect_unavailable=False,
):
    """
    Validate raw cart items and compute prices server-side.

    Args:
        country:           Country instance.
        location:          Location instance or None.
        items_raw:         List of {dish_id, quantity, addon_ids[]} dicts.
                           Legacy key "addons" is accepted as a fallback for
                           addon_ids so older clients keep working.
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
    unavailable_items = []
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
            # Мягкий режим (collect_unavailable): не падаем, а собираем
            # недоступную позицию в список и пропускаем её из расчёта.
            # Строгий режим (по умолчанию): как раньше — ошибка.
            if collect_unavailable:
                unavailable_items.append({
                    "dish_id": dish.id,
                    "dish_name": _display_name(dish),
                })
                continue
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
        # Frontend contract: prefer "addon_ids" (current website field name);
        # fall back to legacy "addons" so old clients keep working unchanged.
        raw_addon_ids = (
            raw_item.get("addon_ids")
            if raw_item.get("addon_ids") is not None
            else raw_item.get("addons")
        ) or []
        if not isinstance(raw_addon_ids, list):
            return None, api_error(
                "INVALID_JSON",
                "addon_ids must be a list of dish IDs",
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

        line_objects.append({
            "dish": dish,
            "quantity": quantity,
            "base_price": base_price,
            "addons_price": addons_price,
            "per_unit": per_unit,
            "total_price": line_total,
            "addon_links": addon_links_for_line,
            "addons_payload": addons_payload,
        })

    # Мягкий режим: если после отсева недоступных не осталось ни одной
    # позиции — оформлять/считать нечего, отдаём ALL_UNAVAILABLE со списком.
    if collect_unavailable and not line_objects:
        return None, api_error(
            "ALL_UNAVAILABLE",
            "Все товары корзины недоступны в выбранной зоне",
            details={"unavailable_items": unavailable_items},
            status=409,
        )

    # ---- Автоматические акции: скидки %/суммой, N+M, подарки ----
    # Тот же расчёт идёт и в корзине, и в заказе (общая функция), поэтому
    # цены везде совпадают. Сбой движка не должен ломать оформление — при
    # ошибке работаем как раньше (без авто-акций).
    try:
        promo_specs = promotions_engine.load_active_promotions(country.id)
        promo_outcome = promotions_engine.apply_to_cart(promo_specs, line_objects)
    except Exception:
        promo_outcome = {"promotions": [], "gifts": [], "auto_discount": Decimal("0")}
        for lo in line_objects:
            lo.setdefault("per_unit_after", lo["per_unit"])
            lo.setdefault("line_total_after", lo["total_price"])
            lo.setdefault("free_quantity", 0)
            lo.setdefault("promo_id", None)
            lo.setdefault("promo_label", None)

    promotions_summary = promo_outcome["promotions"]
    gifts = promo_outcome["gifts"]
    auto_discount = promo_outcome["auto_discount"]

    # Итог акций -> строки (per_unit/total_price с учётом акций) + lines.
    for lo in line_objects:
        lo["per_unit"] = lo["per_unit_after"]
        lo["total_price"] = lo["line_total_after"]
        lines.append({
            "dish": {
                "id": lo["dish"].id,
                "name": _display_name(lo["dish"]),
                "slug": lo["dish"].slug or "",
            },
            "dish_id": lo["dish"].id,
            "quantity": lo["quantity"],
            "base_price": _to_float(lo["base_price"]),
            "addons": lo["addons_payload"],
            "addons_price": _to_float(lo["addons_price"]),
            "unit_price": _to_float(lo["per_unit_after"]),
            "line_total": _to_float(lo["line_total_after"]),
            "total_price": _to_float(lo["line_total_after"]),
            "free_quantity": lo["free_quantity"],
            "promo_id": lo["promo_id"],
            "promo_label": lo["promo_label"],
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
            # База скидки с учётом scope промокода:
            #   all     — весь заказ
            #   include — только eligible_dishes
            #   exclude — всё, КРОМЕ eligible_dishes
            #   auto    — старое поведение (пусто=всё, иначе=include)
            scope, eligible_ids = _promo_effective_scope(promo_code)
            if scope == "all":
                discount_base = sum(
                    (lo["total_price"] for lo in line_objects),
                    Decimal("0"),
                )
            elif scope == "exclude":
                discount_base = sum(
                    (lo["total_price"] for lo in line_objects
                     if lo["dish"].id not in eligible_ids),
                    Decimal("0"),
                )
            else:
                discount_base = sum(
                    (lo["total_price"] for lo in line_objects
                     if lo["dish"].id in eligible_ids),
                    Decimal("0"),
                )

            # 2 dp rounding so cashier-facing totals stay clean.
            discount_amount = (discount_base * discount_percent / Decimal("100")).quantize(
                Decimal("0.01")
            )
            if discount_amount > subtotal:
                discount_amount = subtotal

    # Объединяем скидку промокода с автоматическими акциями (клампим суммой).
    discount_amount = discount_amount + auto_discount
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
        "unavailable_items": unavailable_items,
        "promotions": promotions_summary,
        "gifts": gifts,
        "auto_discount": auto_discount,
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


# -----------------------------------------------------------------------------
# 📍 Delivery zone auto-matching (Part 8 — coordinates → zone → location)
# -----------------------------------------------------------------------------
#
# These helpers let the website skip "choose your branch" and instead let
# the ERP pick the serving branch automatically from the customer's address
# coordinates. Frontend sends lat/lng; we run a haversine match against all
# active circular zones in the country and pick the nearest one.
#
# Important:
#   - This is MVP-grade matching with circles, no polygons.
#   - Frontend coordinates are NEVER trusted to choose a branch — we always
#     re-validate on the server (cart_calculate and orders/create).


def _parse_coordinate(value):
    """Parse a single lat/lng-like value into float; None on failure / empty."""
    if value is None:
        return None
    if isinstance(value, bool):  # bool is an int subclass — guard against it
        return None
    if isinstance(value, (int, float)):
        try:
            f = float(value)
        except (TypeError, ValueError):
            return None
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    raw = str(value).strip().replace(",", ".")
    if not raw:
        return None
    try:
        f = float(raw)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def _coordinates_in_range(lat, lng):
    """Validate that lat/lng are within Earth ranges."""
    return (
        lat is not None
        and lng is not None
        and -90.0 <= lat <= 90.0
        and -180.0 <= lng <= 180.0
    )


def _parse_coordinates(payload):
    """
    Pull (lat, lng) from a request body.

    Accepted shapes:
        { "latitude": 41.31, "longitude": 69.27 }                  ← flat
        { "delivery": { "latitude": 41.31, "longitude": 69.27 } }  ← nested
        { "lat":      41.31, "lng":       69.27 }                  ← short

    Returns (lat, lng, error_response).
      - (lat, lng, None) when both present and valid.
      - (None, None, None) when missing entirely (caller decides if required).
      - (None, None, api_error) when present but malformed / out of range.
    """
    sources = [payload]
    if isinstance(payload.get("delivery"), dict):
        sources.append(payload["delivery"])

    raw_lat = raw_lng = None
    for src in sources:
        if raw_lat is None:
            raw_lat = src.get("latitude")
            if raw_lat in (None, ""):
                raw_lat = src.get("lat")
        if raw_lng is None:
            raw_lng = src.get("longitude")
            if raw_lng in (None, ""):
                raw_lng = src.get("lng")

    # Both missing → no coordinates were sent.
    lat_missing = raw_lat in (None, "")
    lng_missing = raw_lng in (None, "")
    if lat_missing and lng_missing:
        return None, None, None

    # One present, one missing — treat as malformed.
    if lat_missing or lng_missing:
        return None, None, api_error(
            "INVALID_COORDINATES",
            "Both latitude and longitude are required",
            details={"latitude": raw_lat, "longitude": raw_lng},
            status=400,
        )

    lat = _parse_coordinate(raw_lat)
    lng = _parse_coordinate(raw_lng)
    if lat is None or lng is None or not _coordinates_in_range(lat, lng):
        return None, None, api_error(
            "INVALID_COORDINATES",
            "Coordinates are out of range",
            details={"latitude": raw_lat, "longitude": raw_lng},
            status=400,
        )

    return lat, lng, None


def _haversine_km(lat1, lon1, lat2, lon2):
    """
    Great-circle distance between two lat/lng points, in kilometres.

    Uses the standard haversine formula. Mean Earth radius 6371 km is
    accurate enough for radius-based delivery zones (sub-percent error).
    """
    R_KM = 6371.0088
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2.0) ** 2
    )
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return R_KM * c


def _find_delivery_zone(country, lat, lng):
    """
    Find the best DeliveryZone for the given customer coordinates.

    A zone is eligible iff ALL of:
      - zone.is_active=True
      - zone.country=country
      - zone.radius_km > 0
      - zone.center_latitude / center_longitude are set
      - zone.location.is_active=True
      - zone.location.is_visible_on_site=True
      - zone.location.supports_delivery=True

    Eligible zones whose haversine distance ≤ radius_km are matched.
    Tie-break order (spec):
      1. nearest distance
      2. lower zone.site_sort_order
      3. lower location.site_sort_order
      4. location.name alphabetically

    Returns (zone_or_none, distance_km_or_none).
    """
    candidates = (
        DeliveryZone.objects
        .filter(
            country=country,
            is_active=True,
            location__is_active=True,
            location__is_visible_on_site=True,
            location__supports_delivery=True,
            center_latitude__isnull=False,
            center_longitude__isnull=False,
            radius_km__isnull=False,
        )
        .select_related("location")
    )

    matches = []
    for zone in candidates:
        # Defensive: skip non-positive radius even if the column allowed it.
        try:
            radius = float(zone.radius_km)
        except (TypeError, ValueError):
            continue
        if radius <= 0:
            continue

        try:
            z_lat = float(zone.center_latitude)
            z_lng = float(zone.center_longitude)
        except (TypeError, ValueError):
            continue

        distance = _haversine_km(lat, lng, z_lat, z_lng)
        if distance <= radius:
            matches.append((distance, zone))

    if not matches:
        return None, None

    # Sort by the tiebreaker chain.
    matches.sort(
        key=lambda pair: (
            pair[0],                                  # 1. distance
            int(pair[1].site_sort_order or 0),        # 2. zone sort
            int(pair[1].location.site_sort_order or 0),  # 3. location sort
            (pair[1].location.name or "").lower(),    # 4. location name
        )
    )
    distance, zone = matches[0]
    return zone, distance


def _require_location_from_payload(
    country,
    payload,
    required=True,
    fulfillment_method=None,
):
    """
    Resolve location from the JSON body.

    Args:
        country:              Country instance.
        payload:              parsed JSON body.
        required:             when True, missing id returns an error.
        fulfillment_method:   optional "delivery" / "pickup". When given,
                              extra filters are enforced per the website spec:
                                delivery → is_visible_on_site + supports_delivery
                                pickup   → is_visible_on_site + supports_pickup
                              When None, only is_active is checked.

    For pickup orders the website sends `pickup_point_id`. We accept both
    `pickup_point_id` and `location_id` (pickup_point_id takes priority for
    pickup orders).
    """
    # Pickup orders use pickup_point_id; fall through to location_id.
    raw = payload.get("pickup_point_id")
    if raw in (None, "", 0, "0"):
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

    filters = {
        "id": location_id,
        "country": country,
        "is_active": True,
    }
    if fulfillment_method == Order.FULFILLMENT_PICKUP:
        filters["is_visible_on_site"] = True
        filters["supports_pickup"] = True
    elif fulfillment_method == Order.FULFILLMENT_DELIVERY:
        filters["is_visible_on_site"] = True
        filters["supports_delivery"] = True

    location = Location.objects.filter(**filters).first()

    if location is None:
        return None, api_error(
            "LOCATION_NOT_FOUND",
            "Location does not exist or does not match the chosen fulfillment",
            details={
                "location_id": location_id,
                "fulfillment_method": fulfillment_method,
            },
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


def _promo_effective_scope(promo):
    """Нормализует scope промокода. auto -> all/include по eligible_dishes."""
    scope = (getattr(promo, "scope", "auto") or "auto")
    eligible_ids = set(promo.eligible_dishes.values_list("id", flat=True))
    if scope == "auto":
        scope = "include" if eligible_ids else "all"
    return scope, eligible_ids


def _resolve_promo_code(country, payload, *, required=False, customer_phone=None):
    """
    Resolve an optional PromoCode by string code, and validate its
    usage_limit against the requesting customer's phone.

    Args:
        country: Country instance.
        payload: request JSON dict.
        required: if True, missing/empty code returns PROMO_INVALID.
        customer_phone: phone string from the same payload (already
            stripped). When given AND the promo has a usage_limit set,
            we enforce the rule here so cart_calculate and order_create
            both reject ineligible redemptions at the same checkpoint.
            Pass None to skip the check (e.g. for cart_calculate calls
            that don't yet have a phone in the payload).

    Returns (promo_or_none, error_response).

    Lookup:
      - code is case-insensitive (we uppercase before searching).
      - filtered by country and is_active=True.

    Behavior:
      - empty / missing code → (None, None) when required=False;
        otherwise → (None, PROMO_INVALID error).
      - given code but not found / inactive → (None, PROMO_INVALID error).
      - found but usage_limit fails for this customer → (None, error).
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

    # Срок действия. Пустые границы = бессрочный. Вне срока — как недействующий.
    if not promo.is_valid_now():
        return None, api_error(
            "PROMO_INVALID",
            "Срок действия промокода истёк или ещё не начался",
            details={"promo_code": code},
            status=400,
        )

    # Usage-limit enforcement. We only run the check when the caller
    # provided a phone — for cart_calculate before the user has typed
    # their phone we let the code pass and discover the limit later at
    # order_create. The frontend re-runs cart_calculate after the phone
    # field is filled, so the error surfaces to the user immediately.
    if customer_phone:
        ok, err_code = check_promo_usage(
            promo, country=country, phone=customer_phone,
        )
        if not ok:
            if err_code == "PROMO_FIRST_ORDER_ONLY":
                return None, api_error(
                    "PROMO_FIRST_ORDER_ONLY",
                    "Промокод действует только для первого заказа",
                    details={"promo_code": code},
                    status=400,
                )
            # Unknown limit type — generic message, log details for ops.
            return None, api_error(
                "PROMO_INVALID",
                "Промокод не подходит для этого заказа",
                details={"promo_code": code, "reason": err_code},
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


def _get_or_create_app_source(country):
    """Get or create the "Приложение" OrderSource for the given country."""
    source, _ = OrderSource.objects.get_or_create(
        country=country,
        name="Приложение",
        defaults={"is_active": True},
    )
    return source


def _is_app_order(request, payload):
    """Заказ из мобильного приложения? Сигнал от клиента:
    payload {"client": "app"} или {"platform": "ios|android"}, либо заголовок
    X-Client-Type: app. Иначе считаем заказом с сайта."""
    body = payload or {}
    client = str(body.get("client") or "").strip().lower()
    platform = str(body.get("platform") or "").strip().lower()
    header = str(request.META.get("HTTP_X_CLIENT_TYPE", "") or "").strip().lower()
    return (
        client in {"app", "mobile", "application"}
        or platform in {"ios", "android"}
        or header in {"app", "mobile"}
    )


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
        # Подарок акции — позиция с нулевой ценой (бэк добавляет сам).
        "is_gift": _to_float(item.price_snapshot) == 0 and _to_float(item.total_price) == 0,
        # Снимок добавок позиции (пусто для заказов до внедрения добавок).
        "addons": [
            {
                "id": a.addon_dish_id,
                "name": a.name_snapshot,
                "price": _to_float(a.price_snapshot),
            }
            for a in item.addons.all()
        ],
    }


def _serialize_order_for_tracking(order):
    """Return the public tracking payload for one Order. No internal data."""
    items_payload = [
        _serialize_order_item_for_tracking(it)
        for it in order.items.select_related("dish").all()
    ]
    # payment_method is a FK to PaymentMethod; we surface it as the canonical
    # short string (cash / click / payme / online_card) so the frontend can
    # branch on a stable value. _resolve_payment_method names rows by the
    # human label (e.g. "Click"), so we map back by label.
    pm_obj = order.payment_method
    payment_method_key = ""
    if pm_obj is not None:
        # Reverse the PAYMENT_METHOD_LABELS dict — small, runs O(1) in
        # practice and never raises.
        label = pm_obj.name or ""
        for k, v in PAYMENT_METHOD_LABELS.items():
            if v == label:
                payment_method_key = k
                break
        # Fallback: if the label is one of the known keys directly.
        if not payment_method_key and label.lower() in PAYMENT_METHOD_KEYS:
            payment_method_key = label.lower()

    return {
        "order_id": order.id,
        "public_order_number": order.public_order_number or "",
        "order_number": order.public_order_number or "",
        "status": order.status,
        "status_label": _status_label(order.status),
        "created_at": order.created_at.isoformat() if order.created_at else None,
        "fulfillment_method": order.fulfillment_method or "",
        "customer_name": order.customer_name or "",
        "customer_phone": order.customer_phone or "",
        "delivery_address": order.delivery_address or "",
        "subtotal": _to_float(order.subtotal_amount),
        "subtotal_amount": _to_float(order.subtotal_amount),
        "discount_amount": _to_float(order.discount_amount),
        "delivery_price": _to_float(order.delivery_amount),
        "delivery_amount": _to_float(order.delivery_amount),
        "total": _to_float(order.total_amount),
        "payment_status": order.payment_status or "",
        "payment_method": payment_method_key,
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
    and recompute everything from authoritative DB prices.

    Body:
        {
          "country_slug":    "uzbekistan",
          "location_id":     1,            # optional — used only when no coords
          "fulfillment_method": "delivery" | "pickup",  # default "delivery"
          "delivery_zone_id":1,            # optional — only when no coords
          "promo_code":      "WELCOME10",  # optional
          "latitude":        41.3111,      # optional — auto-resolves zone
          "longitude":       69.2797,      # optional — auto-resolves zone
          "items": [
              { "dish_id": 40, "quantity": 1, "addon_ids": [] },
              ...
          ]
        }

    Coordinate resolution (delivery only):
      - When latitude+longitude are present and fulfillment is delivery,
        the server picks the serving branch and zone automatically. The
        frontend's location_id and delivery_zone_id are IGNORED to prevent
        spoofing.
      - When coords are absent, the older behavior is preserved:
        delivery_zone_id (if any) or the country-wide default fee.

    Pickup never uses coordinates and always has delivery_amount = 0.
    """
    payload, err = _parse_json_body(request)
    if err:
        return err

    country, err = _get_country_from_payload(payload)
    if err:
        return err

    fulfillment_method = _resolve_fulfillment_method(payload)

    # ---- Coordinates (delivery only) ----
    lat, lng, err = _parse_coordinates(payload)
    if err:
        return err

    matched_zone = None
    matched_distance_km = None
    location = None
    delivery_zone = None

    if fulfillment_method == Order.FULFILLMENT_DELIVERY and lat is not None:
        # Coordinates win: server picks branch + zone, frontend cannot override.
        matched_zone, matched_distance_km = _find_delivery_zone(country, lat, lng)
        if matched_zone is None:
            return api_error(
                "OUT_OF_DELIVERY_ZONE",
                "Пока не доставляем по этому адресу",
                details={"latitude": lat, "longitude": lng},
                status=400,
            )
        location = matched_zone.location
        delivery_zone = matched_zone
    else:
        # Legacy / no-coords path — location and zone resolved from payload.
        location, err = _require_location_from_payload(
            country, payload, required=False
        )
        if err:
            return err
        delivery_zone, err = _resolve_delivery_zone(country, location, payload)
        if err:
            return err
        if fulfillment_method == Order.FULFILLMENT_PICKUP:
            delivery_zone = None

    # If the website already has the customer's phone (user typed it
    # into the checkout form before the final POST), pass it through so
    # first_order promo limits surface immediately in cart preview.
    # Missing phone → no enforcement here; order_create will catch it.
    cart_phone = (payload.get("customer_phone") or "").strip()
    promo, err = _resolve_promo_code(
        country, payload, required=False,
        customer_phone=cart_phone or None,
    )
    if err:
        return err

    result, err = _validate_and_price_cart(
        country=country,
        location=location,
        items_raw=payload.get("items") or [],
        delivery_zone=delivery_zone,
        fulfillment_method=fulfillment_method,
        promo_code=promo,
        collect_unavailable=True,
    )
    if err:
        return err

    # Build response — existing fields kept, zone-related fields added when
    # the zone was matched so the frontend can show "Доставит: <branch>".
    response = {
        "items": result["lines"],
        "subtotal": _to_float(result["subtotal"]),
        "discount_amount": _to_float(result["discount_amount"]),
        "discount_percent": _to_float(result["discount_percent"]),
        "applied_promo_code": (
            result["applied_promo"].code if result["applied_promo"] else None
        ),
        "delivery_amount": _to_float(result["delivery_price"]),
        # delivery_price kept as a back-compat alias of delivery_amount.
        "delivery_price": _to_float(result["delivery_price"]),
        "free_delivery": bool(result["free_delivery"]),
        "fulfillment_method": result["fulfillment_method"],
        "total": _to_float(result["total"]),
        # Недоступные в выбранной зоне позиции — исключены из расчёта.
        # Фронт показывает «эти товары недоступны в вашей зоне».
        "unavailable_items": result.get("unavailable_items", []),
        # Лимиты заказа (настраиваются менеджером в ERP). 0 = без ограничения.
        # Фронт: сумма < min_order_amount → блокировать оформление;
        # сумма > cash_max_amount → убрать оплату наличными.
        "min_order_amount": _to_float(country.min_order_amount),
        "cash_max_amount": _to_float(country.cash_max_amount),
        # Акции: применённые/доступные акции и автоподарки (контракт фронта).
        "promotions": result.get("promotions", []),
        "gifts": result.get("gifts", []),
    }

    if matched_zone is not None:
        response["location_id"] = matched_zone.location_id
        response["delivery_zone_id"] = matched_zone.id
        response["delivery_zone_name"] = matched_zone.name or ""
        response["estimated_delivery_time"] = matched_zone.estimated_time or ""
        if matched_distance_km is not None:
            response["distance_km"] = round(matched_distance_km, 3)

    return api_success(response)


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
          "items": [
            { "dish_id": 40, "quantity": 1, "addon_ids": [] },
            ...
          ]
        }

    Each item:
      - dish_id   (required, int) — the product id from the catalog.
      - quantity  (required, int) — defaults to 1 if missing.
      - addon_ids (optional list[int]) — Dish ids of addons attached to the
                  product. Legacy "addons" key is also accepted for
                  back-compat with older clients.

    Response (frontend contract):
        {
          "success": true,
          "data": {
            "order": {
              "id":           42,
              "order_number": "RCN-2026-000042",
              "status":       "new",
              "total":        185000.0
            }
          }
        }

    For everything else (status_label, fulfillment, payment_method,
    payment_status, subtotal / discount / delivery breakdown, items snapshot)
    use GET /api/public/orders/<public_order_number>/.

    Coordinate auto-resolution (delivery only):
      - When latitude+longitude are present and fulfillment is delivery,
        the server picks the serving branch from the matching DeliveryZone
        and IGNORES location_id / delivery_zone_id from the payload.
      - If no zone matches → 400 OUT_OF_DELIVERY_ZONE.
      - When coordinates are absent, location_id is required and the
        previous behavior is preserved (back-compat with older clients).

    For pickup orders the resolver accepts both `pickup_point_id`
    (preferred) and `location_id`, and enforces the spec's filters:
    is_active + is_visible_on_site + supports_pickup.
    """
    payload, err = _parse_json_body(request)
    if err:
        return err

    country, err = _get_country_from_payload(payload)
    if err:
        return err

    fulfillment_method = _resolve_fulfillment_method(payload)

    customer_name = str(payload.get("customer_name") or "").strip()
    customer_phone = str(payload.get("customer_phone") or "").strip()
    delivery_address = str(payload.get("delivery_address") or "").strip()
    customer_comment = str(payload.get("comment") or "").strip()

    # ---- Привязка к вошедшему клиенту приложения (если есть токен) ----
    # Если запрос пришёл с Authorization: Bearer <token>, берём клиента из
    # токена. Тогда имя/телефон можно не передавать (подставим из аккаунта),
    # и заказ привяжется именно к этому клиенту (для истории в приложении).
    # Гостевой заказ (без токена) работает как раньше.
    from . import app_auth  # локальный импорт — избегаем кольцевого import
    token_customer = app_auth.customer_from_request(request)
    if token_customer is not None:
        if not customer_phone:
            customer_phone = (token_customer.phone or "").strip()
        if not customer_name:
            customer_name = (token_customer.name or "").strip()

    # ---- Courier-facing extras (Part 9 — delivery checkout fields) ----
    # The website sends courier_landmark / courier_comment / leave_at_door
    # at the top level. We also accept legacy alias keys so partial
    # rollouts (Tilda, mobile app, older client) don't lose data.
    #
    # Storage:
    #   courier_landmark → Order.delivery_landmark  (existing column with
    #                       the same semantic; renaming would require a
    #                       data migration and break ERP screens already
    #                       reading that field).
    #   courier_comment  → Order.courier_comment
    #   leave_at_door    → Order.leave_at_door
    courier_landmark_value = str(
        payload.get("courier_landmark")
        or payload.get("landmark")
        or payload.get("address_landmark")
        or ""
    ).strip()

    courier_comment_value = str(
        payload.get("courier_comment")
        or payload.get("comment_for_courier")
        or payload.get("delivery_comment")
        or ""
    ).strip()

    leave_at_door_value = bool(payload.get("leave_at_door"))

    if not customer_name or not customer_phone:
        return api_error(
            "INVALID_JSON",
            "customer_name and customer_phone are required",
            status=400,
        )

    if fulfillment_method == Order.FULFILLMENT_DELIVERY and not delivery_address:
        return api_error(
            "INVALID_JSON",
            "delivery_address is required for delivery orders",
            status=400,
        )

    # ---- Coordinates / location resolution ----
    lat, lng, err = _parse_coordinates(payload)
    if err:
        return err

    matched_zone = None
    location = None
    delivery_zone = None

    if fulfillment_method == Order.FULFILLMENT_DELIVERY and lat is not None:
        # Coordinates dictate the branch + zone. Frontend location_id and
        # delivery_zone_id are intentionally ignored.
        matched_zone, _distance = _find_delivery_zone(country, lat, lng)
        if matched_zone is None:
            return api_error(
                "OUT_OF_DELIVERY_ZONE",
                "Пока не доставляем по этому адресу",
                details={"latitude": lat, "longitude": lng},
                status=400,
            )
        location = matched_zone.location
        delivery_zone = matched_zone
    else:
        # No coordinates → require location_id (or pickup_point_id) and
        # enforce per-mode filters at the resolver.
        location, err = _require_location_from_payload(
            country,
            payload,
            required=True,
            fulfillment_method=fulfillment_method,
        )
        if err:
            return err

        delivery_zone, err = _resolve_delivery_zone(country, location, payload)
        if err:
            return err

    # Pickup orders never charge delivery, even if a zone leaked in.
    if fulfillment_method == Order.FULFILLMENT_PICKUP:
        delivery_zone = None
        # The Order.delivery_address column is NOT NULL TextField; keep an
        # explicit marker so cashier UI shows the right context.
        delivery_address = "Самовывоз"

    # Phone is already validated and present here (order_create requires
    # it). The promo-code usage_limit check uses it to reject first_order
    # promos for customers who already have prior qualifying orders.
    promo, err = _resolve_promo_code(
        country, payload, required=False,
        customer_phone=customer_phone or None,
    )
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
        collect_unavailable=True,
    )
    if err:
        return err

    if _is_app_order(request, payload):
        source = _get_or_create_app_source(country)
    else:
        source = _get_or_create_website_source(country)
    if token_customer is not None:
        customer = token_customer
        # Если у клиента в профиле пусто, а в заказе указали имя — сохраним.
        if customer_name and not (customer.name or "").strip():
            customer.name = customer_name
            customer.save(update_fields=["name"])
    else:
        customer = _get_or_create_website_customer(country, customer_name, customer_phone)

    # Decide initial payment_status from the chosen method:
    #   - cash               → CASH        (will be paid on hand-off)
    #   - online_card / click / payme → PENDING (gateway will finalize)
    #   - missing            → PENDING
    if payment_key == "cash":
        payment_status_value = Order.PAYMENT_STATUS_CASH
    else:
        payment_status_value = Order.PAYMENT_STATUS_PENDING

    # Decide initial order.status:
    #   - online gateway (click / payme / online_card) → AWAITING_PAYMENT
    #     until the callback confirms payment. Kitchen MUST NOT start
    #     cooking until the gateway confirms — that's the whole point of
    #     this status. The Part 2 callback transitions awaiting_payment →
    #     new (or cancelled, on failure).
    #   - everything else (cash, missing) → NEW as before.
    ONLINE_GATEWAY_KEYS = ("click", "payme", "online_card")

    # =========================================================================
    # MVP fallback: if the gateway env vars are empty, behave like cash.
    # =========================================================================
    # Production goal is hosted-checkout: order → status=awaiting_payment,
    # response includes payment_url, callback flips to paid. But during MVP
    # rollout the secret keys aren't provisioned yet, and the strict path
    # would 503 every Click/Payme order — blocking the launch.
    #
    # Compromise: detect whether the chosen gateway is actually configured.
    # If NOT configured, treat the order like cash-on-delivery:
    #   * status = new (cashier picks it up immediately)
    #   * payment_status = pending (operator confirms payment manually)
    #   * payment_method = whatever the customer chose (NOT rewritten to cash —
    #     operator must see "Click" / "Payme" so they know to verify the
    #     transfer in the provider cabinet)
    #   * response payment = null (no payment_url to redirect to)
    #
    # When the secrets are later added to env, this branch starts returning
    # False and the real hosted-checkout flow kicks in — zero code changes.
    from django.conf import settings as _settings_mvp

    def _gateway_configured(key):
        if key == "click":
            return bool(
                (getattr(_settings_mvp, "CLICK_SERVICE_ID", "") or "").strip()
                and (getattr(_settings_mvp, "CLICK_MERCHANT_ID", "") or "").strip()
                and (getattr(_settings_mvp, "CLICK_SECRET_KEY", "") or "").strip()
            )
        if key == "payme":
            return bool(
                (getattr(_settings_mvp, "PAYME_MERCHANT_ID", "") or "").strip()
                and (getattr(_settings_mvp, "PAYME_SECRET_KEY", "") or "").strip()
            )
        # online_card and unknown keys: treat as not configured.
        return False

    # `gateway_active` decides BOTH the initial status and whether we build
    # a payment_url below. Computed once so the two stay consistent.
    gateway_active = (
        payment_key in ONLINE_GATEWAY_KEYS and _gateway_configured(payment_key)
    )

    if gateway_active:
        initial_status = Order.STATUS_AWAITING_PAYMENT
    else:
        initial_status = Order.STATUS_NEW

    # Assemble the cashier-facing summary: addon details + payment + promo
    # + courier block. The new courier block ensures ERP staff can see the
    # courier info even if the order detail template doesn't render the
    # dedicated fields yet (Part 9).
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

    # Courier block — only rendered when at least one field is filled,
    # otherwise we'd accumulate empty headers in every website order.
    courier_block_lines = []
    if courier_landmark_value:
        courier_block_lines.append(f"- Ориентир: {courier_landmark_value}")
    if courier_comment_value:
        courier_block_lines.append(
            f"- Комментарий курьеру: {courier_comment_value}"
        )
    if leave_at_door_value:
        courier_block_lines.append("- Оставить у двери: Да")
    if courier_block_lines:
        courier_block = "Данные для курьера:\n" + "\n".join(courier_block_lines)
    else:
        courier_block = ""

    cashier_comment = "\n\n".join(
        part for part in (
            addon_summary,
            "\n".join(extra_lines),
            courier_block,
        ) if part
    )

    with transaction.atomic():
        # Meta CAPI deduplication — the frontend generates a UUID v4 in
        # CheckoutView, fires the Pixel `Purchase` event with it, and
        # passes the same value here. We persist it so a cron task can
        # later send the server-side CAPI Purchase with the same event_id
        # (15 minutes after payment, see send_pending_meta_purchases).
        # Missing/empty → "" (deduplication off for this order, which is
        # fine — the Pixel event still fires, just no server backup).
        meta_event_id_raw = payload.get("meta_event_id") or ""
        meta_event_id = str(meta_event_id_raw).strip()[:64]

        # Meta match-quality signals. The frontend knows _fbp / _fbc cookies
        # and may pass them in the payload; IP / User-Agent we also capture
        # from the request as a fallback (this endpoint is hit by the user's
        # browser, so the request carries real client headers). All re-sent
        # later in the server-side CAPI Purchase to raise match quality.
        def _client_ip(req):
            xff = req.META.get("HTTP_X_FORWARDED_FOR", "")
            if xff:
                return xff.split(",")[0].strip()
            return (req.META.get("HTTP_CF_CONNECTING_IP")
                    or req.META.get("REMOTE_ADDR") or "")

        meta_fbp = str(payload.get("meta_fbp") or payload.get("fbp") or "").strip()[:255]
        meta_fbc = str(payload.get("meta_fbc") or payload.get("fbc") or "").strip()[:255]
        meta_client_ip = str(
            payload.get("meta_client_ip") or _client_ip(request) or ""
        ).strip()[:64]
        meta_user_agent = str(
            payload.get("meta_user_agent")
            or request.META.get("HTTP_USER_AGENT", "")
            or ""
        ).strip()[:512]

        # Атрибуция: fbclid + UTM-метки (присылает фронт из URL перехода).
        fbclid = str(payload.get("fbclid") or "").strip()[:512]
        utm_source = str(payload.get("utm_source") or "").strip()[:255]
        utm_medium = str(payload.get("utm_medium") or "").strip()[:255]
        utm_campaign = str(payload.get("utm_campaign") or "").strip()[:255]
        utm_content = str(payload.get("utm_content") or "").strip()[:255]
        utm_term = str(payload.get("utm_term") or "").strip()[:255]

        # UTM промокода наследуются заказом ТОЛЬКО в пустые поля — метки,
        # пришедшие с фронта (реальный рекламный переход), приоритетнее.
        if promo is not None:
            utm_source = utm_source or str(getattr(promo, "utm_source", "") or "").strip()[:255]
            utm_medium = utm_medium or str(getattr(promo, "utm_medium", "") or "").strip()[:255]
            utm_campaign = utm_campaign or str(getattr(promo, "utm_campaign", "") or "").strip()[:255]
            utm_content = utm_content or str(getattr(promo, "utm_content", "") or "").strip()[:255]
            utm_term = utm_term or str(getattr(promo, "utm_term", "") or "").strip()[:255]

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
            status=initial_status,
            fulfillment_method=fulfillment_method,
            payment_status=payment_status_value,
            # Courier-facing fields stored on dedicated columns so ERP screens
            # and admin can show them as soon as templates are updated.
            delivery_landmark=courier_landmark_value,
            courier_comment=courier_comment_value,
            leave_at_door=leave_at_door_value,
            meta_event_id=meta_event_id,
            meta_fbp=meta_fbp,
            meta_fbc=meta_fbc,
            meta_client_ip=meta_client_ip,
            meta_user_agent=meta_user_agent,
            fbclid=fbclid,
            utm_source=utm_source,
            utm_medium=utm_medium,
            utm_campaign=utm_campaign,
            utm_content=utm_content,
            utm_term=utm_term,
        )

        # Now we have order.id → generate public_order_number deterministically.
        order.public_order_number = _generate_public_order_number(order)
        order.save(update_fields=["public_order_number"])

        for line in result["line_objects"]:
            try:
                dish_cost = Decimal(str(line["dish"].calculate_cost() or 0))
            except Exception:
                dish_cost = Decimal("0")

            oi = OrderItem.objects.create(
                order=order,
                dish=line["dish"],
                quantity=Decimal(line["quantity"]),
                price_snapshot=line["per_unit"],
                cost_snapshot=dish_cost,
                total_price=line["total_price"],
            )
            # Снимок выбранных добавок позиции (для деталей и «Повторить заказ»).
            for _addon in (line.get("addons_payload") or []):
                try:
                    OrderItemAddon.objects.create(
                        order_item=oi,
                        addon_dish_id=_addon.get("id"),
                        name_snapshot=(_addon.get("name") or "")[:255],
                        price_snapshot=Decimal(str(_addon.get("price") or 0)),
                    )
                except Exception:
                    pass

        # Подарочные позиции акций — бэк добавляет сам, цена 0. Любые
        # «подарки» от клиента игнорируются: источник истины — этот список.
        for gift in result.get("gifts", []):
            gift_dish = Dish.objects.filter(
                id=gift.get("dish_id"), country=country
            ).first()
            if gift_dish is None:
                continue
            try:
                gift_cost = Decimal(str(gift_dish.calculate_cost() or 0))
            except Exception:
                gift_cost = Decimal("0")
            OrderItem.objects.create(
                order=order,
                dish=gift_dish,
                quantity=Decimal(gift.get("quantity") or 1),
                price_snapshot=Decimal("0"),
                cost_snapshot=gift_cost,
                total_price=Decimal("0"),
            )

    # Уведомление о новом заказе в Telegram (в тред филиала). Сбой Telegram
    # не должен влиять на оформление — функция сама проглатывает ошибки.
    try:
        from .shift_views import send_new_order_to_telegram
        send_new_order_to_telegram(order)
    except Exception:
        pass

    # Авто-сохранение адреса доставки вошедшего клиента в книгу адресов
    # (без дублей по тексту адреса). Гостевые заказы — пропускаем. Сбой не
    # влияет на оформление заказа.
    if (token_customer is not None
            and fulfillment_method == Order.FULFILLMENT_DELIVERY
            and delivery_address):
        try:
            _norm = delivery_address.strip().casefold()
            _existing = list(token_customer.addresses.all())
            if not any((a.address or "").strip().casefold() == _norm for a in _existing):
                CustomerAddress.objects.create(
                    customer=token_customer,
                    address=delivery_address[:2000],
                    latitude=lat,
                    longitude=lng,
                    location=location,
                    landmark=(courier_landmark_value or "")[:255],
                    courier_comment=(courier_comment_value or "")[:255],
                    comment=(customer_comment or "")[:255],
                    is_default=(len(_existing) == 0),
                )
        except Exception:
            pass

    # Build the payment block. For cash (and any order without an online
    # Build the payment block. Three cases:
    #
    #   1. Cash / online_card / unknown method      → payment = null.
    #   2. Click/Payme but provider NOT configured  → payment = null,
    #      log a WARNING. The order is already saved with status=NEW and
    #      payment_status=PENDING (cash-like fallback) so the cashier
    #      sees the order normally; the canonical payment_method ("click"
    #      / "payme") is preserved so operators know to verify the
    #      transfer manually in the provider cabinet.
    #   3. Click/Payme AND provider configured      → build the real
    #      hosted-checkout URL, return payment = { provider, payment_url }.
    payment_block = None
    if gateway_active and payment_key == "click":
        try:
            payment_url = build_click_payment_url(order)
        except ClickConfigError as exc:
            # gateway_active was true, so config was present at the top of
            # the function. If we still trip ClickConfigError here, the
            # most likely cause is a zero/negative total — surface it
            # cleanly instead of returning a broken URL to the frontend.
            return api_error(
                "PAYMENT_PROVIDER_UNAVAILABLE",
                str(exc),
                status=503,
            )
        payment_block = {
            "provider": "click",
            "payment_url": payment_url,
        }
    elif gateway_active and payment_key == "payme":
        from .payments.payme import (
            build_payme_payment_url, PaymeConfigError,
        )
        try:
            payment_url = build_payme_payment_url(order)
        except PaymeConfigError as exc:
            return api_error(
                "PAYMENT_PROVIDER_UNAVAILABLE",
                str(exc),
                status=503,
            )
        payment_block = {
            "provider": "payme",
            "payment_url": payment_url,
        }
    elif payment_key in ONLINE_GATEWAY_KEYS:
        # Case 2: gateway requested but not configured. MVP fallback path.
        # Log so operations can grep how many such orders exist before the
        # secrets are provisioned. Stderr / Sentry / Render logs all pick
        # this up via Django's default logging config.
        import logging
        logging.getLogger(__name__).warning(
            "[mvp-payment] order %s created with payment_method=%s but "
            "provider not configured; manual processing required",
            order.public_order_number or order.id,
            payment_key,
        )

    # Уведомление о новом заказе в Telegram (тред филиала). Сбой Telegram
    # не должен ломать создание заказа — всё внутри try/except.
    try:
        from .shift_views import send_new_order_to_telegram
        send_new_order_to_telegram(order)
    except Exception:
        pass

    # Frontend contract: data.order = { id, order_number, status,
    # payment_status, payment_method, total } and data.payment is either
    # null (cash / no online gateway) or { provider, payment_url } (click).
    # Anything else the website needs after order creation is available via
    # GET /api/public/orders/<public_order_number>/.
    return api_success({
        "order": {
            "id": order.id,
            "order_number": order.public_order_number or "",
            "status": order.status,
            "payment_status": order.payment_status,
            # Echo the canonical key the website sent ("cash" / "click" /
            # "payme" / "online_card"), not the human-readable label, so
            # the frontend can branch on a stable value.
            "payment_method": payment_key or "",
            "total": _to_float(order.total_amount),
        },
        "payment": payment_block,
        # Позиции, исключённые из заказа как недоступные в зоне (мягкий режим).
        # Фронт показывает клиенту «эти товары не вошли в заказ».
        "unavailable_items": result.get("unavailable_items", []),
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
        .select_related("country", "location", "payment_method")
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

    scope, eligible_ids = _promo_effective_scope(promo)
    percent = _money(promo.percent)

    response = {
        "valid": True,
        "is_valid": True,
        "code": promo.code,
        "percent": _to_float(percent),
        "scope": scope,
        "eligible_dish_ids": sorted(eligible_ids),
        "message": None,
    }

    raw_items = payload.get("items")
    raw_subtotal = payload.get("subtotal")

    if isinstance(raw_items, list) and raw_items:
        # Точный расчёт по составу корзины с учётом scope.
        base = Decimal("0")
        applied_ids = []
        for it in raw_items:
            if not isinstance(it, dict):
                continue
            did = _coerce_int(it.get("dish_id"))
            qty = _coerce_int(it.get("quantity"), default=1) or 1
            if not did:
                continue
            dish = (
                Dish.objects.filter(id=did, country=country)
                .only("id", "selling_price")
                .first()
            )
            if dish is None:
                continue
            in_scope = (
                scope == "all"
                or (scope == "include" and did in eligible_ids)
                or (scope == "exclude" and did not in eligible_ids)
            )
            if in_scope:
                base += _money(dish.selling_price) * Decimal(qty)
                applied_ids.append(did)
        discount = (base * percent / Decimal("100")).quantize(Decimal("0.01"))
        response["discount_amount"] = _to_float(discount)
        response["applied_dish_ids"] = applied_ids
    elif raw_subtotal is not None and scope == "all":
        subtotal = _money(raw_subtotal)
        if subtotal < 0:
            subtotal = Decimal("0")
        discount = (subtotal * percent / Decimal("100")).quantize(Decimal("0.01"))
        if discount > subtotal:
            discount = subtotal
        response["subtotal"] = _to_float(subtotal)
        response["discount_amount"] = _to_float(discount)
        response["total_after_discount"] = _to_float(subtotal - discount)
    elif raw_subtotal is not None:
        # Код ограничен блюдами — по одной сумме точную скидку не посчитать.
        response["subtotal"] = _to_float(_money(raw_subtotal))
        response["message"] = (
            "Скидка действует только на часть блюд — точная сумма "
            "рассчитывается в корзине."
        )

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

# =============================================================================
# 🗺  ENDPOINT: POST /api/public/delivery/check
# =============================================================================

@csrf_exempt
@require_POST
def delivery_check(request):
    """
    Tell the website whether we can deliver to a set of coordinates, and
    which branch + price would serve them.

    Body:
        {
          "country_slug": "uzbekistan",
          "latitude":     41.3111,
          "longitude":    69.2797,
          "address":      "ул. Амир Темур, 101"  # echoed back, optional
        }

    Coordinates can also be sent nested under "delivery": { ... }.

    Success — inside a zone:
        {
          "success": true,
          "data": {
            "is_deliverable": true,
            "location":    { "id", "name", "public_name" },
            "delivery_zone": { "id", "name" },
            "delivery_price":           15000.0,
            "free_delivery_threshold":  150000.0,
            "estimated_delivery_time":  "35–45 мин",
            "distance_km":              2.4,
            "address":                  "ул. Амир Темур, 101"   # if sent
          }
        }

    Success — outside all zones:
        {
          "success": true,
          "data": {
            "is_deliverable": false,
            "reason":  "OUT_OF_DELIVERY_ZONE",
            "message": "Пока не доставляем по этому адресу"
          }
        }

    A missing / malformed coordinates payload returns 400 INVALID_COORDINATES.
    """
    payload, err = _parse_json_body(request)
    if err:
        return err

    country, err = _get_country_from_payload(payload)
    if err:
        return err

    lat, lng, err = _parse_coordinates(payload)
    if err:
        return err
    if lat is None or lng is None:
        return api_error(
            "INVALID_COORDINATES",
            "latitude and longitude are required",
            status=400,
        )

    zone, distance = _find_delivery_zone(country, lat, lng)
    address_echo = str(payload.get("address") or "").strip()

    if zone is None:
        response = {
            "is_deliverable": False,
            "reason": "OUT_OF_DELIVERY_ZONE",
            "message": "Пока не доставляем по этому адресу",
        }
        if address_echo:
            response["address"] = address_echo
        return api_success(response)

    location = zone.location
    response = {
        "is_deliverable": True,
        "location": {
            "id": location.id,
            "name": location.name,
            "public_name": _display_name(location),
        },
        "delivery_zone": {
            "id": zone.id,
            "name": zone.name or "",
        },
        "delivery_price": _to_float(zone.delivery_price),
        "free_delivery_threshold": _to_float(zone.free_delivery_threshold),
        "estimated_delivery_time": zone.estimated_time or "",
        "distance_km": round(distance, 3) if distance is not None else None,
    }
    if address_echo:
        response["address"] = address_echo
    return api_success(response)


# =============================================================================
# 🏠 PUBLIC HOMEPAGE API (Part 11 — banners / bestsellers / frequently bought)
# =============================================================================
#
# All three endpoints share the same shape:
#   - GET only, no auth, no side effects
#   - country_slug is required
#   - Empty result is a success with an empty list (never 404)
#
# A short Cache-Control header is set so a CDN / reverse proxy can cache
# the response for a minute, since the homepage is read by every visitor.
# No Redis / Django cache framework is involved — we just hint to clients.


HOMEPAGE_CACHE_CONTROL = "public, max-age=60"


def _apply_homepage_cache_headers(response):
    """Attach a short Cache-Control header so CDNs / browsers can cache."""
    response["Cache-Control"] = HOMEPAGE_CACHE_CONTROL
    return response


# -----------------------------------------------------------------------------
# 🖼  ENDPOINT: GET /api/public/home/banners
# -----------------------------------------------------------------------------

def _serialize_homepage_banner(banner):
    return {
        "id": banner.id,
        "title": banner.title or "",
        "subtitle": banner.subtitle or "",
        "show_text": bool(banner.show_text),
        "desktop_image": banner.desktop_image or "",
        "mobile_image": banner.mobile_image or "",
        "action_type": banner.action_type,
        "action_value": banner.action_value or "",
        "sort_order": int(banner.sort_order or 0),
    }


@csrf_exempt
@require_GET
def home_banners(request):
    """
    Active, currently-scheduled homepage banners for a country.

    A banner is returned only when:
      - country matches the resolved country
      - is_active = True
      - start_at is null OR start_at <= now
      - end_at   is null OR end_at   >= now

    Sorted by (sort_order, id).
    """
    country, err = get_public_country(request)
    if err:
        return err

    now = timezone.now()
    qs = HomepageBanner.objects.filter(
        country=country,
        is_active=True,
    ).filter(
        Q(start_at__isnull=True) | Q(start_at__lte=now),
    ).filter(
        Q(end_at__isnull=True) | Q(end_at__gte=now),
    ).order_by("sort_order", "id")

    response = api_success({
        "banners": [_serialize_homepage_banner(b) for b in qs],
    })
    return _apply_homepage_cache_headers(response)


# -----------------------------------------------------------------------------
# ⭐  ENDPOINT: GET /api/public/home/bestsellers
# -----------------------------------------------------------------------------

def _serialize_bestseller(request, dish):
    """
    Bestseller payload — we reuse the standard product card and overlay
    `is_featured: True` so the website can tag the card visually without
    a second lookup.
    """
    card = serialize_product_card(request, dish)
    card["is_featured"] = True
    return card


@csrf_exempt
@require_GET
def home_bestsellers(request):
    """
    Featured ("bestseller") dishes for the country homepage.

    Filters:
      - country matches
      - is_visible_on_site = True
      - is_stop_list       = False
      - is_featured        = True

    Sort: (site_sort_order, name).
    """
    country, err = get_public_country(request)
    if err:
        return err

    dishes = (
        Dish.objects
        .filter(
            country=country,
            is_visible_on_site=True,
            is_stop_list=False,
            is_featured=True,
        )
        .order_by("site_sort_order", "name")
    )

    response = api_success({
        "products": [_serialize_bestseller(request, d) for d in dishes],
    })
    return _apply_homepage_cache_headers(response)


# -----------------------------------------------------------------------------
# 🍱  ENDPOINT: GET /api/public/home/combo  («Комбо с фудкорта»; как bestsellers)
# -----------------------------------------------------------------------------

def _serialize_combo_pick(request, dish):
    """Карточка для блока «Комбо с фудкорта» — та же карточка каталога."""
    card = serialize_product_card(request, dish)
    card["show_in_combo_block"] = True
    return card


@csrf_exempt
@require_GET
def home_combo(request):
    """
    Блюда блока «Комбо с фудкорта» для главной (механика как у bestsellers).

    Фильтры: country, is_visible_on_site=True, is_stop_list=False,
             show_in_combo_block=True. Сортировка: (site_sort_order, name).
    Пусто -> products: [] (фронт прячет секцию).
    """
    country, err = get_public_country(request)
    if err:
        return err

    dishes = (
        Dish.objects
        .filter(
            country=country,
            is_visible_on_site=True,
            is_stop_list=False,
            show_in_combo_block=True,
        )
        .order_by("site_sort_order", "name")
    )

    response = api_success({
        "products": [_serialize_combo_pick(request, d) for d in dishes],
    })
    return _apply_homepage_cache_headers(response)


# -----------------------------------------------------------------------------
# 🧺  ENDPOINT: GET /api/public/home/frequently-bought
# -----------------------------------------------------------------------------

def _serialize_homepage_block(request, block, dishes_by_id, item_pairs):
    """
    Build one block payload.

    Args:
        block:          HomepageProductBlock instance.
        dishes_by_id:   {dish_id: Dish} prefetched in bulk.
        item_pairs:     ordered list of (item, dish_id) for THIS block,
                        in the desired display order.
    """
    products = []
    for _item, dish_id in item_pairs:
        dish = dishes_by_id.get(dish_id)
        if dish is None:
            # Dish became hidden / stopped / deleted between our query and now.
            continue
        products.append(serialize_product_card(request, dish))

    return {
        "id": block.id,
        "title": block.title or "",
        "products": products,
    }


@csrf_exempt
@require_GET
def home_frequently_bought(request):
    """
    Manually curated "frequently bought together" blocks.

    Filters:
      - block.country  = requested country
      - block.is_active = True
      - item.is_active  = True
      - item.dish.is_visible_on_site = True
      - item.dish.is_stop_list       = False

    Sorted by (block.sort_order, block.id, item.sort_order, item.id).

    To stay efficient on the homepage we run only THREE queries regardless
    of how many blocks/items there are:
      1) all eligible blocks for the country
      2) all eligible items for those blocks (ordered)
      3) all dishes for those items (one IN-query)
    """
    country, err = get_public_country(request)
    if err:
        return err

    blocks = list(
        HomepageProductBlock.objects
        .filter(country=country, is_active=True)
        .order_by("sort_order", "id")
    )

    if not blocks:
        response = api_success({"blocks": []})
        return _apply_homepage_cache_headers(response)

    block_ids = [b.id for b in blocks]

    # Pull every active item across all blocks in display order, so we can
    # bucket them per block in Python below.
    items = list(
        HomepageProductBlockItem.objects
        .filter(block_id__in=block_ids, is_active=True)
        .order_by("block_id", "sort_order", "id")
    )

    # Collect dish ids and pull dishes that are visible & not stopped.
    dish_ids = {it.dish_id for it in items}
    if dish_ids:
        visible_dishes = list(
            Dish.objects.filter(
                id__in=dish_ids,
                country=country,
                is_visible_on_site=True,
                is_stop_list=False,
            )
        )
    else:
        visible_dishes = []
    dishes_by_id = {d.id: d for d in visible_dishes}

    # Bucket items per block, keeping the ORDER BY from the query.
    items_by_block = {}
    for it in items:
        items_by_block.setdefault(it.block_id, []).append((it, it.dish_id))

    out = []
    for block in blocks:
        item_pairs = items_by_block.get(block.id, [])
        block_payload = _serialize_homepage_block(
            request, block, dishes_by_id, item_pairs
        )
        # A block with zero rendered products is still returned — operators
        # might intentionally show an empty "loading" block. If you'd rather
        # hide them, uncomment:
        # if not block_payload["products"]:
        #     continue
        out.append(block_payload)

    response = api_success({"blocks": out})
    return _apply_homepage_cache_headers(response)


# -----------------------------------------------------------------------------
# 🛒 ENDPOINT: GET /api/public/home/compact-upsell
# -----------------------------------------------------------------------------
# Compact horizontal upsell strip ("Часто заказывают вместе") for the website
# homepage. SEPARATE from /home/frequently-bought — different models, different
# (flat) response shape with quick-add product cards.
#
# Response shape:
#   {
#     "success": true,
#     "data": {
#       "enabled": <bool>,        # is there an active block at all
#       "title":   <str>,         # block title, or the default if no block
#       "products": [ {id, name, slug, image, price, weight, sort_order}, ... ]
#     }
#   }
#
# Product filtering (all must hold):
#   - item.is_active = True
#   - dish.country   = current country
#   - dish.is_visible_on_site = True
#   - dish.is_stop_list       = False
# Sorted by (item.sort_order, item.id).

COMPACT_UPSELL_DEFAULT_TITLE = "Часто заказывают вместе"


def _serialize_compact_upsell_product(request, dish, sort_order):
    """
    Build one compact-upsell product card.

    Reuses serialize_product_card() for the shared fields (id/name/slug/
    image/price/weight) and trims to the compact contract, then adds the
    per-item sort_order.

    NB: `id` is the ERP Dish id — the frontend checkout sends it back as
    dish_id. Slug is passed through untouched (Cyrillic-safe); never a
    synthesized "product-<id>" — empty stays empty.
    """
    card = serialize_product_card(request, dish)
    return {
        "id": card["id"],
        "name": card["name"],
        "slug": card["slug"],          # may be "" — we never fake it
        "image": card["image"],
        "price": card["price"],
        "weight": card["weight"],
        "sort_order": sort_order,
    }


@csrf_exempt
@require_GET
def home_compact_upsell(request):
    """
    Compact homepage upsell strip. Returns the FIRST active block for the
    country (by sort_order, id) plus its eligible products.

    Empty states:
      - no active block      -> enabled=false, default title, products=[]
      - active block, 0 items -> enabled=true,  block title,   products=[]
    """
    country, err = get_public_country(request)
    if err:
        return err

    block = (
        HomepageCompactUpsellBlock.objects
        .filter(
            country=country,
            is_active=True,
            placement=HomepageCompactUpsellBlock.PLACEMENT_HOME,
        )
        .order_by("sort_order", "id")
        .first()
    )

    # No active block at all → disabled, default title.
    if block is None:
        response = api_success({
            "enabled": False,
            "title": COMPACT_UPSELL_DEFAULT_TITLE,
            "products": [],
        })
        return _apply_homepage_cache_headers(response)

    title = block.title or COMPACT_UPSELL_DEFAULT_TITLE

    # Active items for this block, in display order.
    items = list(
        HomepageCompactUpsellItem.objects
        .filter(block=block, is_active=True)
        .order_by("sort_order", "id")
    )

    products = []
    if items:
        dish_ids = {it.dish_id for it in items}
        visible_dishes = Dish.objects.filter(
            id__in=dish_ids,
            country=country,
            is_visible_on_site=True,
            is_stop_list=False,
        )
        dishes_by_id = {d.id: d for d in visible_dishes}

        for it in items:
            dish = dishes_by_id.get(it.dish_id)
            if dish is None:
                # Hidden / stop-listed / wrong-country dish — filtered out.
                continue
            products.append(
                _serialize_compact_upsell_product(request, dish, it.sort_order)
            )

    response = api_success({
        "enabled": True,
        "title": title,
        "products": products,
    })
    return _apply_homepage_cache_headers(response)

# =============================================================================
# 💳 ENDPOINT: POST /api/payments/click/callback/   (Part 2)
# =============================================================================
# Receives Click's server-to-server callback. Click POSTs form-encoded data
# to ONE URL twice:
#
#   action=0 (Prepare)  → "can this order be paid?" → reply with merchant_
#                          prepare_id (we use order.id) + error=0.
#   action=1 (Complete) → final outcome. If error >= 0 → mark paid. If
#                          error < 0 (e.g. -9 = user cancelled) → mark
#                          failed/cancelled and put the order into
#                          payment_failed so the customer can retry.
#
# Security:
#   - Signature is checked FIRST. Wrong sign → error -1 immediately. We
#     don't even reveal whether the order exists.
#   - select_for_update inside an atomic block prevents races between
#     duplicate Click callbacks (Click retries on network errors).
#   - Idempotent: a second Complete with the same payment_transaction_id
#     returns error=0 without re-writing fields.
#   - No logging of secret_key, sign_string, or full payload — only the
#     two transaction ids and the error code.
#
# Replies are JSON (Click also accepts JSON callbacks per current docs).


def _click_reply(error, error_note, *, click_trans_id=None, merchant_trans_id=None,
                 merchant_prepare_id=None, merchant_confirm_id=None):
    """
    Build the JSON envelope Click expects in callback replies. Includes only
    fields relevant to the current stage — unused ones are omitted so we
    don't echo back nulls that could confuse Click's parser.
    """
    body = {
        "error": int(error),
        "error_note": str(error_note or ""),
    }
    if click_trans_id is not None:
        body["click_trans_id"] = click_trans_id
    if merchant_trans_id is not None:
        body["merchant_trans_id"] = merchant_trans_id
    if merchant_prepare_id is not None:
        body["merchant_prepare_id"] = merchant_prepare_id
    if merchant_confirm_id is not None:
        body["merchant_confirm_id"] = merchant_confirm_id
    return JsonResponse(body)


def _read_click_callback_params(request):
    """
    Click sends form-encoded data by default but the docs also mention JSON.
    We accept both. Returns a plain dict of strings (we never trust types
    from the wire — signature is computed on the raw string concatenation).
    """
    if request.POST:
        return {k: request.POST.get(k, "") for k in request.POST}
    # Fallback to JSON body.
    try:
        import json
        return json.loads(request.body.decode("utf-8") or "{}")
    except Exception:
        return {}


@csrf_exempt
@require_POST
def click_callback(request):
    """
    Click Prepare + Complete callback. ONE URL handles both actions.
    """
    params = _read_click_callback_params(request)

    # Fields Click documents for the callback.
    click_trans_id = str(params.get("click_trans_id") or "").strip()
    service_id = str(params.get("service_id") or "").strip()
    merchant_trans_id = str(params.get("merchant_trans_id") or "").strip()
    merchant_prepare_id = str(params.get("merchant_prepare_id") or "").strip()
    amount = str(params.get("amount") or "").strip()
    action_raw = str(params.get("action") or "").strip()
    sign_time = str(params.get("sign_time") or "").strip()
    sign_string = str(params.get("sign_string") or "").strip()
    # Click error code in the COMPLETE request — < 0 means the user cancelled
    # or the gateway itself failed. We don't trust this field for security
    # (signature is what matters), only for branching paid vs failed.
    click_error_raw = str(params.get("error") or "0").strip()

    # 1) Action must be parseable. Anything else → -3.
    try:
        action = int(action_raw)
    except (TypeError, ValueError):
        return _click_reply(
            ERROR_ACTION_NOT_FOUND, "Action not found",
            click_trans_id=click_trans_id,
            merchant_trans_id=merchant_trans_id,
        )

    if action not in (ACTION_PREPARE, ACTION_COMPLETE):
        return _click_reply(
            ERROR_ACTION_NOT_FOUND, "Action not found",
            click_trans_id=click_trans_id,
            merchant_trans_id=merchant_trans_id,
        )

    # 2) Signature check — BEFORE we hit the DB. Use the raw strings from
    # the wire; never re-format amount or sign_time. For PREPARE,
    # merchant_prepare_id is empty.
    sig_ok = verify_click_signature(
        sign_string=sign_string,
        click_trans_id=click_trans_id,
        service_id=service_id,
        merchant_trans_id=merchant_trans_id,
        merchant_prepare_id=merchant_prepare_id if action == ACTION_COMPLETE else "",
        amount=amount,
        action=action_raw,
        sign_time=sign_time,
    )
    if not sig_ok:
        return _click_reply(
            ERROR_SIGN_CHECK_FAILED, "Sign check failed",
            click_trans_id=click_trans_id,
            merchant_trans_id=merchant_trans_id,
        )

    if not merchant_trans_id:
        return _click_reply(
            ERROR_USER_DOES_NOT_EXIST, "Order not found",
            click_trans_id=click_trans_id,
            merchant_trans_id=merchant_trans_id,
        )

    # 3) Locate the order. Try by public_order_number first (what we put in
    # the URL as transaction_param); fall back to numeric pk for older
    # orders that may have used the raw id.
    order = Order.objects.filter(public_order_number=merchant_trans_id).first()
    if order is None and merchant_trans_id.isdigit():
        order = Order.objects.filter(pk=int(merchant_trans_id)).first()

    if order is None:
        return _click_reply(
            ERROR_USER_DOES_NOT_EXIST, "Order not found",
            click_trans_id=click_trans_id,
            merchant_trans_id=merchant_trans_id,
        )

    # 4) Amount must match the SERVER-CALCULATED total. Click can never make
    # us accept a different amount, even with a valid signature for one.
    if not amounts_match(order.total_amount, amount):
        return _click_reply(
            ERROR_INCORRECT_AMOUNT, "Incorrect amount",
            click_trans_id=click_trans_id,
            merchant_trans_id=merchant_trans_id,
        )

    # 5) Mutate inside an atomic block with row lock so duplicate callbacks
    # serialize safely. Re-fetch with select_for_update — the existence
    # check above was just to short-circuit not-found without locking.
    with transaction.atomic():
        try:
            locked = Order.objects.select_for_update().get(pk=order.pk)
        except Order.DoesNotExist:
            return _click_reply(
                ERROR_USER_DOES_NOT_EXIST, "Order not found",
                click_trans_id=click_trans_id,
                merchant_trans_id=merchant_trans_id,
            )

        if action == ACTION_PREPARE:
            # Prepare: don't change state yet (just confirm we can accept).
            # If already paid — Click should not be calling Prepare; reply
            # with -4.
            if locked.payment_status == Order.PAYMENT_STATUS_PAID:
                return _click_reply(
                    ERROR_ALREADY_PAID, "Already paid",
                    click_trans_id=click_trans_id,
                    merchant_trans_id=merchant_trans_id,
                )
            # Expired by our TTL — refuse. Click will mark the transaction
            # cancelled and won't actually debit the card.
            if locked.auto_expired:
                return _click_reply(
                    ERROR_TRANSACTION_CANCELLED, "Order expired",
                    click_trans_id=click_trans_id,
                    merchant_trans_id=merchant_trans_id,
                )
            # merchant_prepare_id = our order pk (stable, unique).
            return _click_reply(
                ERROR_OK, "Success",
                click_trans_id=click_trans_id,
                merchant_trans_id=merchant_trans_id,
                merchant_prepare_id=str(locked.pk),
            )

        # action == ACTION_COMPLETE
        # Idempotency: if we already recorded this click_trans_id, just say
        # OK without rewriting. Click retries on network errors.
        if (locked.payment_status == Order.PAYMENT_STATUS_PAID and
                locked.payment_transaction_id == click_trans_id):
            return _click_reply(
                ERROR_OK, "Success",
                click_trans_id=click_trans_id,
                merchant_trans_id=merchant_trans_id,
                merchant_prepare_id=str(locked.pk),
                merchant_confirm_id=str(locked.pk),
            )

        # Zombie-revival guard: a late callback after auto-expire MUST NOT
        # promote the order to paid. Reply -9 so Click rolls back its side.
        # Checked AFTER the idempotent already-paid branch so a legit
        # already-paid order can't be retroactively expired.
        if locked.auto_expired:
            return _click_reply(
                ERROR_TRANSACTION_CANCELLED, "Order expired",
                click_trans_id=click_trans_id,
                merchant_trans_id=merchant_trans_id,
            )

        # Branch by Click's error code in the Complete request:
        #   error >= 0 → payment succeeded.
        #   error  < 0 → user cancelled / gateway failed.
        try:
            click_error = int(click_error_raw)
        except (TypeError, ValueError):
            click_error = 0

        if click_error >= 0:
            locked.payment_status = Order.PAYMENT_STATUS_PAID
            locked.payment_transaction_id = click_trans_id
            locked.payment_paid_at = timezone.now()
            # Kitchen can start cooking now — flip the order status from
            # AWAITING_PAYMENT back to NEW so the existing cashier flow
            # picks it up like any other fresh order.
            if locked.status == Order.STATUS_AWAITING_PAYMENT:
                locked.status = Order.STATUS_NEW
            locked.save(update_fields=[
                "payment_status",
                "payment_transaction_id",
                "payment_paid_at",
                "status",
                "updated_at",
            ] if hasattr(locked, "updated_at") else [
                "payment_status",
                "payment_transaction_id",
                "payment_paid_at",
                "status",
            ])
            return _click_reply(
                ERROR_OK, "Success",
                click_trans_id=click_trans_id,
                merchant_trans_id=merchant_trans_id,
                merchant_prepare_id=str(locked.pk),
                merchant_confirm_id=str(locked.pk),
            )

        # Click reported failure / cancellation. -9 specifically means
        # transaction cancelled; everything else negative → generic failed.
        if click_error == ERROR_TRANSACTION_CANCELLED:
            locked.payment_status = Order.PAYMENT_STATUS_CANCELLED
        else:
            locked.payment_status = Order.PAYMENT_STATUS_FAILED
        # Move the order out of AWAITING_PAYMENT so the customer can retry
        # via /pay/. Already-paid orders are never reverted here (guarded
        # by the idempotency check above).
        locked.status = Order.STATUS_PAYMENT_FAILED
        locked.save(update_fields=["payment_status", "status"])

        return _click_reply(
            ERROR_OK, "Success",
            click_trans_id=click_trans_id,
            merchant_trans_id=merchant_trans_id,
            merchant_prepare_id=str(locked.pk),
        )


# =============================================================================
# 💳 ENDPOINT: POST /api/public/orders/<public_order_number>/pay/   (Part 2)
# =============================================================================
# Lets the frontend re-request a payment URL for an unpaid order. Use case:
# the customer's first attempt was cancelled or failed and they want to try
# again from the order-tracking page (or a "checkout?payment_failed=1"
# redirect from the gateway).
#
# Rules:
#   - Only orders in awaiting_payment OR payment_failed can retry.
#   - Already-paid orders → 409 (can't retry, no double-charging).
#   - Cash / cancelled orders → 409 (different flow).
#   - We do NOT create a new Order, do NOT change the public_order_number,
#     do NOT change total_amount.

@csrf_exempt
@require_POST
def order_pay_retry(request, public_order_number):
    number = (public_order_number or "").strip()
    if not number:
        return api_error(
            "INVALID_JSON",
            "public_order_number is required",
            status=400,
        )

    payload, err = _parse_json_body(request)
    if err:
        return err

    requested_method = str(payload.get("payment_method") or "").strip().lower()
    if requested_method not in ("click",):
        # Part 2 only ships Click retry; other providers (payme, online_card)
        # arrive when their callbacks land.
        return api_error(
            "PAYMENT_METHOD_INVALID",
            "Only 'click' retry is supported",
            details={"payment_method": requested_method, "allowed": ["click"]},
            status=400,
        )

    order = (
        Order.objects.filter(public_order_number=number)
        .select_related("country", "location", "payment_method")
        .first()
    )
    if order is None:
        return api_error(
            "DISH_NOT_FOUND",
            "Order not found",
            details={"public_order_number": number},
            status=404,
        )

    if order.payment_status == Order.PAYMENT_STATUS_PAID:
        return api_error(
            "ORDER_ALREADY_PAID",
            "Order is already paid",
            status=409,
        )

    # Only awaiting_payment and payment_failed are retryable. Cash, cancelled,
    # done, etc. are not. This also blocks retrying an order that an operator
    # cancelled in ERP.
    if order.status not in (Order.STATUS_AWAITING_PAYMENT,
                            Order.STATUS_PAYMENT_FAILED):
        return api_error(
            "ORDER_NOT_RETRYABLE",
            "Order is not in a retryable state",
            details={"status": order.status},
            status=409,
        )

    # Pre-flight config check — same as in order_create — so a misconfigured
    # deploy returns a clean 503 instead of a broken URL.
    from django.conf import settings as dj_settings
    if not (getattr(dj_settings, "CLICK_SERVICE_ID", "") and
            getattr(dj_settings, "CLICK_MERCHANT_ID", "")):
        return api_error(
            "PAYMENT_PROVIDER_UNAVAILABLE",
            "Click payment is not configured on the server",
            status=503,
        )

    # Reset to a clean awaiting-payment state for the retry. We don't touch
    # payment_transaction_id / payment_paid_at — those will be filled by
    # the next successful callback. If the previous attempt left
    # payment_status=failed/cancelled, flip it back to pending so the
    # frontend / ERP sees the order is in flight again.
    with transaction.atomic():
        locked = Order.objects.select_for_update().get(pk=order.pk)
        locked.payment_status = Order.PAYMENT_STATUS_PENDING
        locked.status = Order.STATUS_AWAITING_PAYMENT
        locked.save(update_fields=["payment_status", "status"])
        order = locked

    try:
        payment_url = build_click_payment_url(order)
    except ClickConfigError as exc:
        return api_error(
            "PAYMENT_PROVIDER_UNAVAILABLE",
            str(exc),
            status=503,
        )

    return api_success({
        "order": {
            "order_number": order.public_order_number or "",
            "payment_status": order.payment_status,
            "status": order.status,
        },
        "payment": {
            "provider": "click",
            "payment_url": payment_url,
        },
    })


# =============================================================================
# 🪧 ENDPOINT: GET /api/public/home/combo-banners
# =============================================================================
# Pair of CTA banners shown in the homepage "Комбо и акции" section.
# Distinct from /home/banners (hero, single, full-width) — these are two
# narrower side-by-side cards with promo-code / category / external-link
# actions.

COMBO_BANNER_LIMIT = 2


def _serialize_combo_banner(request, banner):
    """Public payload for one HomeComboBanner."""
    return {
        "id": banner.id,
        "title": banner.title or "",
        "subtitle": banner.subtitle or "",
        "background_image": _resolve_image(
            request, banner.background_image, banner.background_image_url,
        ),
        "background_color": banner.background_color or "#181818",
        "text_color": banner.text_color or "white",
        "cta_label": banner.cta_label or "",
        "cta_action_type": banner.cta_action_type or "none",
        "cta_action_value": banner.cta_action_value or "",
        "sort_order": int(banner.sort_order or 0),
        "is_active": bool(banner.is_active),
    }


@csrf_exempt
@require_GET
def home_combo_banners(request):
    """
    Two-banner CTA strip ("Комбо и акции"). Filters by country + is_active,
    orders by sort_order then id, caps at 2 results.

    Empty case: an empty banners list. The frontend hides the section in
    that case — there is no fallback to a hardcoded pair anymore.
    """
    country, err = get_public_country(request)
    if err:
        return err

    banners = (
        HomeComboBanner.objects
        .filter(country=country, is_active=True)
        .order_by("sort_order", "id")[:COMBO_BANNER_LIMIT]
    )

    response = api_success({
        "banners": [_serialize_combo_banner(request, b) for b in banners],
    })
    return _apply_homepage_cache_headers(response)


# =============================================================================
# 🛒 ENDPOINT: GET /api/public/cart/upsell
# =============================================================================
# "Добавить к заказу" — dish upsell strip on the /cart page. Reuses the
# HomepageCompactUpsellBlock model with placement="cart" so the same admin
# UI / serializer / item-management code services both placements.

CART_UPSELL_LIMIT = 10
CART_UPSELL_DEFAULT_TITLE = "Добавить к заказу"


@csrf_exempt
@require_GET
def cart_upsell(request):
    """
    Cart-page upsell strip. Reuses HomepageCompactUpsellBlock with
    placement="cart" — same admin/data, different surface on the website.
    """
    country, err = get_public_country(request)
    if err:
        return err

    block = (
        HomepageCompactUpsellBlock.objects
        .filter(
            country=country,
            is_active=True,
            placement=HomepageCompactUpsellBlock.PLACEMENT_CART,
        )
        .order_by("sort_order", "id")
        .first()
    )

    if block is None:
        response = api_success({
            "enabled": False,
            "title": CART_UPSELL_DEFAULT_TITLE,
            "products": [],
        })
        return _apply_homepage_cache_headers(response)

    title = block.title or CART_UPSELL_DEFAULT_TITLE

    items = list(
        HomepageCompactUpsellItem.objects
        .filter(block=block, is_active=True)
        .order_by("sort_order", "id")[:CART_UPSELL_LIMIT]
    )

    products = []
    if items:
        dish_ids = {it.dish_id for it in items}
        # Same visibility rules as the public catalog: visible on site,
        # not in stop-list, not archived. If a dish was archived after
        # being added to the upsell list, it silently disappears here.
        visible_dishes = Dish.objects.filter(
            id__in=dish_ids,
            country=country,
            is_visible_on_site=True,
            is_stop_list=False,
            is_archived=False,
        )
        dishes_by_id = {d.id: d for d in visible_dishes}

        for it in items:
            dish = dishes_by_id.get(it.dish_id)
            if dish is None:
                continue
            products.append(
                _serialize_compact_upsell_product(request, dish, it.sort_order)
            )

    response = api_success({
        "enabled": True,
        "title": title,
        "products": products,
    })
    return _apply_homepage_cache_headers(response)


# =============================================================================
# 💳 ENDPOINT: POST /api/payments/payme/callback/   (Payme JSON-RPC)
# =============================================================================
# Payme uses a JSON-RPC 2.0 envelope (no signature in the URL). Auth is HTTP
# Basic: Authorization: Basic base64("Paycom:" + SECRET_KEY).
#
# Six methods land on ONE endpoint, differentiated by params.method:
#   CheckPerformTransaction  — can we accept payment for this order?
#   CreateTransaction        — Payme reserves the order
#   PerformTransaction       — payment confirmed; mark order paid
#   CancelTransaction        — payment cancelled or refunded
#   CheckTransaction         — Payme asks "what's the status of tx X?"
#   GetStatement             — reconciliation (list transactions in a range)
#
# Critical invariants:
#   1. Repeated CreateTransaction with the SAME id must return the SAME
#      create_time and state (Sandbox enforces this).
#   2. Repeated PerformTransaction on an already-completed tx must return
#      the SAME perform_time and state.
#   3. CancelTransaction sets state=-1 if was CREATED, state=-2 if was
#      COMPLETED.
#   4. Amount in params.amount is TIYIN — compare against int(total * 100).
#   5. Reply HTTP status is ALWAYS 200 — error code goes in the JSON body.
#   6. Late callbacks for auto_expired orders are REFUSED with -31008.


def _payme_now_ms():
    """Current time as 13-digit ms-epoch (Payme's Timestamp type)."""
    import time
    return int(time.time() * 1000)


def _payme_rpc_response(request_id, *, result=None, error=None):
    """JSON-RPC 2.0 reply envelope. HTTP status is always 200."""
    body = {"jsonrpc": "2.0", "id": request_id}
    if error is not None:
        body["error"] = error
    else:
        body["result"] = result
    return JsonResponse(body, status=200)


def _payme_error_body(exc):
    """Convert a PaymeError exception into Payme's JSON-RPC error object."""
    body = {
        "code": exc.code,
        "message": exc.message,
    }
    if exc.data is not None:
        body["data"] = exc.data
    return body


def _payme_locate_order(account):
    """Resolve params.account → Order. Raises PaymeError(-31050) if not found."""
    from .payments.payme import PaymeError, ERROR_ACCOUNT_NOT_FOUND
    from django.conf import settings as dj_settings

    account_field = (
        getattr(dj_settings, "PAYME_ACCOUNT_FIELD", "") or "order_id"
    ).strip()
    if not isinstance(account, dict):
        raise PaymeError(ERROR_ACCOUNT_NOT_FOUND, data=account_field)

    raw_value = account.get(account_field)
    if raw_value is None or str(raw_value).strip() == "":
        raise PaymeError(ERROR_ACCOUNT_NOT_FOUND, data=account_field)

    raw_value = str(raw_value).strip()
    order = (
        Order.objects.filter(public_order_number=raw_value)
        .select_related("country", "location", "payment_method")
        .first()
    )
    if order is None and raw_value.isdigit():
        order = (
            Order.objects.filter(pk=int(raw_value))
            .select_related("country", "location", "payment_method")
            .first()
        )

    if order is None:
        raise PaymeError(ERROR_ACCOUNT_NOT_FOUND, data=account_field)

    if order.status == Order.STATUS_CANCELLED:
        raise PaymeError(ERROR_ACCOUNT_NOT_FOUND, data=account_field)

    return order


def _payme_handle_check_perform(params):
    """CheckPerformTransaction — "can this order be paid?" """
    from .payments.payme import (
        PaymeError, amount_matches_order,
        ERROR_INVALID_AMOUNT, ERROR_INVALID_STATE,
    )
    order = _payme_locate_order(params.get("account"))

    # Auto-expired guard — first line of zombie defense.
    order = _maybe_lazy_expire(order)
    if order.auto_expired:
        raise PaymeError(ERROR_INVALID_STATE)

    amount = params.get("amount")
    if not isinstance(amount, int) or not amount_matches_order(order, amount):
        raise PaymeError(ERROR_INVALID_AMOUNT)

    if order.payment_status == Order.PAYMENT_STATUS_PAID:
        raise PaymeError(ERROR_INVALID_STATE)

    return {"allow": True}


def _payme_handle_create_transaction(params):
    """CreateTransaction with idempotency + zombie guard."""
    from .payments.payme import (
        PaymeError, amount_matches_order,
        ERROR_INVALID_AMOUNT, ERROR_INVALID_STATE, ERROR_ACCOUNT_NOT_FOUND,
        STATE_CREATED,
    )
    from .models import PaymeTransaction
    from django.conf import settings as dj_settings

    payme_tx_id = str(params.get("id") or "").strip()
    if not payme_tx_id:
        raise PaymeError(ERROR_INVALID_STATE)

    order = _payme_locate_order(params.get("account"))

    amount = params.get("amount")
    if not isinstance(amount, int) or not amount_matches_order(order, amount):
        raise PaymeError(ERROR_INVALID_AMOUNT)

    payme_time_ms = params.get("time")
    if not isinstance(payme_time_ms, int):
        raise PaymeError(ERROR_INVALID_STATE)

    with transaction.atomic():
        try:
            locked_order = Order.objects.select_for_update().get(pk=order.pk)
        except Order.DoesNotExist:
            raise PaymeError(
                ERROR_ACCOUNT_NOT_FOUND,
                data=(getattr(dj_settings, "PAYME_ACCOUNT_FIELD", "") or "order_id"),
            )

        # IDEMPOTENCY first.
        existing = PaymeTransaction.objects.filter(
            payme_transaction_id=payme_tx_id
        ).first()
        if existing is not None:
            if existing.order_id != locked_order.id:
                raise PaymeError(ERROR_INVALID_STATE)
            return {
                "create_time": existing.create_time_ms,
                "transaction": str(existing.id),
                "state": existing.state,
            }

        # ZOMBIE GUARD: auto-expired refuses fresh transactions.
        if locked_order.auto_expired:
            raise PaymeError(ERROR_INVALID_STATE)

        # At most one active reservation per order.
        active = PaymeTransaction.objects.filter(
            order=locked_order, state=STATE_CREATED,
        ).first()
        if active is not None:
            raise PaymeError(ERROR_INVALID_STATE)

        if locked_order.payment_status == Order.PAYMENT_STATUS_PAID:
            raise PaymeError(ERROR_INVALID_STATE)

        if locked_order.status == Order.STATUS_CANCELLED:
            raise PaymeError(ERROR_INVALID_STATE)

        now_ms = _payme_now_ms()
        ptx = PaymeTransaction.objects.create(
            payme_transaction_id=payme_tx_id,
            order=locked_order,
            amount_tiyin=amount,
            state=STATE_CREATED,
            payme_time_ms=payme_time_ms,
            create_time_ms=now_ms,
            raw_last_params=params,
        )
        if locked_order.status != Order.STATUS_AWAITING_PAYMENT:
            locked_order.status = Order.STATUS_AWAITING_PAYMENT
        if locked_order.payment_status != Order.PAYMENT_STATUS_PENDING:
            locked_order.payment_status = Order.PAYMENT_STATUS_PENDING
        locked_order.payment_transaction_id = payme_tx_id
        locked_order.save(update_fields=[
            "status", "payment_status", "payment_transaction_id",
        ])

    return {
        "create_time": ptx.create_time_ms,
        "transaction": str(ptx.id),
        "state": ptx.state,
    }


def _payme_handle_perform_transaction(params):
    """PerformTransaction — final confirmation. Zombie guard inside lock."""
    from .payments.payme import (
        PaymeError,
        ERROR_TRANSACTION_NOT_FOUND, ERROR_INVALID_STATE,
        STATE_CREATED, STATE_COMPLETED,
    )
    from .models import PaymeTransaction

    payme_tx_id = str(params.get("id") or "").strip()
    if not payme_tx_id:
        raise PaymeError(ERROR_TRANSACTION_NOT_FOUND)

    with transaction.atomic():
        try:
            ptx = (
                PaymeTransaction.objects
                .select_for_update()
                .select_related("order")
                .get(payme_transaction_id=payme_tx_id)
            )
        except PaymeTransaction.DoesNotExist:
            raise PaymeError(ERROR_TRANSACTION_NOT_FOUND)

        # IDEMPOTENCY: repeated Perform — same response.
        if ptx.state == STATE_COMPLETED:
            return {
                "transaction": str(ptx.id),
                "perform_time": ptx.perform_time_ms,
                "state": ptx.state,
            }

        if ptx.state != STATE_CREATED:
            raise PaymeError(ERROR_INVALID_STATE)

        order = Order.objects.select_for_update().get(pk=ptx.order_id)

        # ZOMBIE GUARD inside lock.
        if order.auto_expired:
            raise PaymeError(ERROR_INVALID_STATE)

        now_ms = _payme_now_ms()
        ptx.state = STATE_COMPLETED
        ptx.perform_time_ms = now_ms
        ptx.raw_last_params = params
        ptx.save(update_fields=[
            "state", "perform_time_ms", "raw_last_params", "updated_at",
        ])

        order.payment_status = Order.PAYMENT_STATUS_PAID
        order.payment_transaction_id = payme_tx_id
        order.payment_paid_at = timezone.now()
        if order.status == Order.STATUS_AWAITING_PAYMENT:
            order.status = Order.STATUS_NEW
        order.save(update_fields=[
            "payment_status", "payment_transaction_id",
            "payment_paid_at", "status",
        ])

    return {
        "transaction": str(ptx.id),
        "perform_time": ptx.perform_time_ms,
        "state": ptx.state,
    }


def _payme_handle_cancel_transaction(params):
    """
    CancelTransaction — STATE_CREATED → -1, STATE_COMPLETED → -2 (refund).
    Refund sets payment_status=REFUNDED (distinct from CANCELLED).
    """
    from .payments.payme import (
        PaymeError,
        ERROR_TRANSACTION_NOT_FOUND, ERROR_INVALID_STATE,
        STATE_CREATED, STATE_COMPLETED,
        STATE_CANCELLED, STATE_CANCELLED_AFTER,
    )
    from .models import PaymeTransaction

    payme_tx_id = str(params.get("id") or "").strip()
    if not payme_tx_id:
        raise PaymeError(ERROR_TRANSACTION_NOT_FOUND)

    reason = params.get("reason")
    if reason is not None and not isinstance(reason, int):
        reason = None

    with transaction.atomic():
        try:
            ptx = (
                PaymeTransaction.objects
                .select_for_update()
                .select_related("order")
                .get(payme_transaction_id=payme_tx_id)
            )
        except PaymeTransaction.DoesNotExist:
            raise PaymeError(ERROR_TRANSACTION_NOT_FOUND)

        # IDEMPOTENCY: already cancelled — same response.
        if ptx.state in (STATE_CANCELLED, STATE_CANCELLED_AFTER):
            return {
                "transaction": str(ptx.id),
                "cancel_time": ptx.cancel_time_ms,
                "state": ptx.state,
            }

        order = Order.objects.select_for_update().get(pk=ptx.order_id)
        now_ms = _payme_now_ms()

        if ptx.state == STATE_CREATED:
            new_state = STATE_CANCELLED
        elif ptx.state == STATE_COMPLETED:
            new_state = STATE_CANCELLED_AFTER
        else:
            raise PaymeError(ERROR_INVALID_STATE)

        ptx.state = new_state
        ptx.cancel_time_ms = now_ms
        ptx.reason = reason
        ptx.raw_last_params = params
        ptx.save(update_fields=[
            "state", "cancel_time_ms", "reason",
            "raw_last_params", "updated_at",
        ])

        if new_state == STATE_CANCELLED:
            # Before-pay cancel. Preserve EXPIRED if it's stronger.
            if order.payment_status != Order.PAYMENT_STATUS_EXPIRED:
                order.payment_status = Order.PAYMENT_STATUS_FAILED
            if order.status == Order.STATUS_AWAITING_PAYMENT:
                order.status = Order.STATUS_PAYMENT_FAILED
            order.save(update_fields=["payment_status", "status"])
        else:  # STATE_CANCELLED_AFTER → refund
            order.payment_status = Order.PAYMENT_STATUS_REFUNDED
            order.save(update_fields=["payment_status"])

    return {
        "transaction": str(ptx.id),
        "cancel_time": ptx.cancel_time_ms,
        "state": ptx.state,
    }


def _payme_handle_check_transaction(params):
    """CheckTransaction — read-only."""
    from .payments.payme import (
        PaymeError, ERROR_TRANSACTION_NOT_FOUND,
    )
    from .models import PaymeTransaction

    payme_tx_id = str(params.get("id") or "").strip()
    if not payme_tx_id:
        raise PaymeError(ERROR_TRANSACTION_NOT_FOUND)

    ptx = PaymeTransaction.objects.filter(
        payme_transaction_id=payme_tx_id
    ).first()
    if ptx is None:
        raise PaymeError(ERROR_TRANSACTION_NOT_FOUND)

    return {
        "create_time": ptx.create_time_ms,
        "perform_time": ptx.perform_time_ms,
        "cancel_time": ptx.cancel_time_ms,
        "transaction": str(ptx.id),
        "state": ptx.state,
        "reason": ptx.reason,
    }


def _payme_handle_get_statement(params):
    """GetStatement — Payme polls a time range for reconciliation."""
    from .models import PaymeTransaction
    from django.conf import settings as dj_settings

    frm = params.get("from")
    to = params.get("to")
    if not isinstance(frm, int) or not isinstance(to, int):
        return {"transactions": []}

    qs = PaymeTransaction.objects.filter(
        payme_time_ms__gte=frm, payme_time_ms__lte=to,
    ).select_related("order").order_by("payme_time_ms")

    account_field = (
        getattr(dj_settings, "PAYME_ACCOUNT_FIELD", "") or "order_id"
    ).strip()

    out = []
    for ptx in qs[:10000]:
        order = ptx.order
        out.append({
            "id": ptx.payme_transaction_id,
            "time": ptx.payme_time_ms,
            "amount": ptx.amount_tiyin,
            "account": {
                account_field: order.public_order_number or str(order.id),
            },
            "create_time": ptx.create_time_ms,
            "perform_time": ptx.perform_time_ms,
            "cancel_time": ptx.cancel_time_ms,
            "transaction": str(ptx.id),
            "state": ptx.state,
            "reason": ptx.reason,
        })
    return {"transactions": out}


_PAYME_HANDLERS = {
    "CheckPerformTransaction": _payme_handle_check_perform,
    "CreateTransaction":       _payme_handle_create_transaction,
    "PerformTransaction":      _payme_handle_perform_transaction,
    "CancelTransaction":       _payme_handle_cancel_transaction,
    "CheckTransaction":        _payme_handle_check_transaction,
    "GetStatement":            _payme_handle_get_statement,
}


@csrf_exempt
def payme_callback(request):
    """Single JSON-RPC entry point for all 6 Merchant API methods."""
    from .payments.payme import (
        PaymeError, verify_payme_basic_auth,
        ERROR_METHOD_NOT_POST, ERROR_PARSE, ERROR_INVALID_REQUEST,
        ERROR_METHOD_NOT_FOUND, ERROR_INSUFFICIENT_PRIVILEGE,
        ERROR_SYSTEM,
    )

    request_id = None

    if request.method != "POST":
        return _payme_rpc_response(
            request_id,
            error=_payme_error_body(PaymeError(ERROR_METHOD_NOT_POST)),
        )

    # 1) Auth FIRST. Wrong creds → -32504, no body parsing.
    if not verify_payme_basic_auth(request):
        return _payme_rpc_response(
            request_id,
            error=_payme_error_body(PaymeError(ERROR_INSUFFICIENT_PRIVILEGE)),
        )

    # 2) Parse JSON.
    try:
        body = request.body.decode("utf-8") if request.body else ""
        payload = json.loads(body) if body else {}
    except (ValueError, UnicodeDecodeError):
        return _payme_rpc_response(
            request_id,
            error=_payme_error_body(PaymeError(ERROR_PARSE)),
        )

    if not isinstance(payload, dict):
        return _payme_rpc_response(
            request_id,
            error=_payme_error_body(PaymeError(ERROR_INVALID_REQUEST)),
        )

    request_id = payload.get("id")
    method = payload.get("method")
    params = payload.get("params") or {}

    if not isinstance(method, str) or not method:
        return _payme_rpc_response(
            request_id,
            error=_payme_error_body(PaymeError(ERROR_INVALID_REQUEST)),
        )
    if not isinstance(params, dict):
        return _payme_rpc_response(
            request_id,
            error=_payme_error_body(PaymeError(ERROR_INVALID_REQUEST)),
        )

    handler = _PAYME_HANDLERS.get(method)
    if handler is None:
        return _payme_rpc_response(
            request_id,
            error=_payme_error_body(
                PaymeError(ERROR_METHOD_NOT_FOUND, data=method)
            ),
        )

    # 3) Dispatch with error normalization.
    try:
        result = handler(params)
    except PaymeError as exc:
        return _payme_rpc_response(request_id, error=_payme_error_body(exc))
    except Exception:
        # Don't leak internal error messages to Payme.
        return _payme_rpc_response(
            request_id,
            error=_payme_error_body(PaymeError(ERROR_SYSTEM)),
        )

    return _payme_rpc_response(request_id, result=result)
