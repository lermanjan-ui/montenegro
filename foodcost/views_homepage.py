"""
ERP-side settings page for managing homepage content (Part 12).

This page exposes THREE sections:
  - "Баннеры главной"            — CRUD on HomepageBanner.
  - "Хиты продаж"                — toggle Dish.is_featured + tune
                                   Dish.site_sort_order.
  - "Часто заказывают вместе"    — CRUD on HomepageProductBlock and its
                                   HomepageProductBlockItem children.

Permissions:
    - login_required
    - country must be accessible to the user (delegated to get_country)
    - SECTION_SETTINGS access required (delegated to require_section_access)

We deliberately reuse the SAME permission gate as the main /settings/ page
because spec says "do not create a new permission system".
"""

from django.contrib.auth.decorators import login_required
from django.db.models import Prefetch
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_datetime

import json

from .models import (
    UserProfile,
    HomepageBanner,
    Dish,
    HomepageProductBlock,
    HomepageProductBlockItem,
    HomepageCompactUpsellBlock,
    HomepageCompactUpsellItem,
    HomeComboBanner,
)
from .views import get_country, require_section_access


# -----------------------------------------------------------------------------
# Form-value parsing helpers
# -----------------------------------------------------------------------------

def _parse_optional_datetime(value):
    """
    Parse an HTML <input type="datetime-local"> string into a timezone-aware
    datetime. Empty / invalid input yields None so the model field stays
    NULL.

    HTML datetime-local sends a value like "2026-06-01T18:30" without
    timezone. We attach the Django default timezone so comparisons against
    timezone.now() are consistent.
    """
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    dt = parse_datetime(raw)
    if dt is None:
        return None
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
    return dt


def _parse_int_or_zero(value):
    """Parse a positive integer from a form value; return 0 on failure."""
    try:
        n = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return n if n > 0 else 0


# -----------------------------------------------------------------------------
# Combo-banner form helpers
# -----------------------------------------------------------------------------
# Pulled out so create / update share validation. We catch:
#   - missing required title / cta_label
#   - cta_action_value required when type != "none"
#   - bad HEX in background_color
#   - unknown choices (defensive — the form sends a <select>)
#
# Length caps are enforced by the CharField max_length on the model; this
# function only checks soft business rules. Errors are returned as a string
# (matching the existing single-`error` slot on the page); if you want per-
# field rendering, swap to a dict.

import re as _re

_HEX_COLOR_RE = _re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")


def _clean_hex(value, *, default="#181818"):
    """Normalize a HEX string; fall back to the default on bad input."""
    raw = (value or "").strip()
    if not raw:
        return default
    return raw if _HEX_COLOR_RE.match(raw) else default


def _clean_combo_text_color(value):
    raw = (value or "").strip().lower()
    if raw in {"white", "dark"}:
        return raw
    return "white"


def _clean_combo_action_type(value):
    raw = (value or "").strip().lower()
    allowed = {"category", "product", "promo_code", "external_url", "none"}
    return raw if raw in allowed else "none"


def _validate_combo_banner_form(post):
    """
    Return an error string (Russian) if the form is invalid, else None.
    Single-string contract because the existing template surfaces one
    error banner at the top of the page.
    """
    title = (post.get("title") or "").strip()
    cta_label = (post.get("cta_label") or "").strip()
    if not title:
        return "Заголовок обязателен"
    if len(title) > 30:
        return "Заголовок не длиннее 30 символов"
    if len((post.get("subtitle") or "").strip()) > 100:
        return "Подзаголовок не длиннее 100 символов"
    if not cta_label:
        return "Текст кнопки обязателен"
    if len(cta_label) > 20:
        return "Текст кнопки не длиннее 20 символов"

    raw_hex = (post.get("background_color") or "").strip()
    if raw_hex and not _HEX_COLOR_RE.match(raw_hex):
        return "Цвет фона должен быть HEX (например, #181818)"

    action_type = (post.get("cta_action_type") or "").strip().lower()
    action_value = (post.get("cta_action_value") or "").strip()
    if action_type != "none" and not action_value:
        return "Для выбранного действия нужно указать значение"

    return None


# -----------------------------------------------------------------------------
# Status helper (used by template for badge rendering)
# -----------------------------------------------------------------------------

def _banner_status(banner, now):
    """
    Compute a single status code for a banner. The template renders this as
    a coloured badge. Order matters: an inactive banner is always "Отключён"
    regardless of dates; otherwise schedule wins.

    Returns one of: "off", "scheduled", "expired", "active".
    """
    if not banner.is_active:
        return "off"
    if banner.start_at and banner.start_at > now:
        return "scheduled"
    if banner.end_at and banner.end_at < now:
        return "expired"
    return "active"


# -----------------------------------------------------------------------------
# Main view
# -----------------------------------------------------------------------------

@login_required(login_url="/login/")
def homepage_settings_page(request, country_slug):
    country = get_country(country_slug, request.user)

    access_error = require_section_access(
        request.user,
        UserProfile.SECTION_SETTINGS,
    )
    if access_error:
        return access_error

    error = None

    # Helper: build the canonical settings URL preserving the active tab.
    # Forms submit `_tab=<name>` in a hidden field so the redirect after
    # save lands the operator back where they were. Without this every
    # save (add bestseller, update banner, etc.) silently throws the user
    # back to the "banners" default tab.
    _VALID_TABS = {
        "banners", "combo", "compact_upsell",
        "product_blocks", "bestsellers",
    }
    def _redirect_to_tab():
        # Priority: explicit form field > query string > default
        tab = (
            request.POST.get("_tab")
            or request.GET.get("tab")
            or ""
        ).strip()
        # Same for placement (used by the compact-upsell tab's segmented
        # switch). Has no effect on other tabs.
        placement = (
            request.POST.get("_placement")
            or request.GET.get("placement")
            or ""
        ).strip()
        if placement not in ("home", "cart"):
            placement = ""

        base = f"/c/{country.slug}/settings/homepage/"
        qs = []
        if tab and tab in _VALID_TABS and tab != "banners":
            qs.append(f"tab={tab}")
        if placement and tab == "compact_upsell" and placement != "home":
            qs.append(f"placement={placement}")
        if qs:
            return redirect(f"{base}?{'&'.join(qs)}")
        return redirect(base)

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "create_homepage_banner":
            title = (request.POST.get("title") or "").strip()
            if not title:
                error = "Укажи заголовок баннера"
            else:
                action_type = (
                    request.POST.get("action_type")
                    or HomepageBanner.ACTION_NONE
                ).strip()
                valid_actions = {key for key, _ in HomepageBanner.ACTION_TYPE_CHOICES}
                if action_type not in valid_actions:
                    action_type = HomepageBanner.ACTION_NONE

                HomepageBanner.objects.create(
                    country=country,
                    title=title,
                    subtitle=(request.POST.get("subtitle") or "").strip(),
                    show_text=bool(request.POST.get("show_text")),
                    desktop_image=(request.POST.get("desktop_image") or "").strip(),
                    mobile_image=(request.POST.get("mobile_image") or "").strip(),
                    action_type=action_type,
                    action_value=(request.POST.get("action_value") or "").strip(),
                    sort_order=_parse_int_or_zero(request.POST.get("sort_order")),
                    is_active=bool(request.POST.get("is_active")),
                    start_at=_parse_optional_datetime(
                        request.POST.get("start_at")
                    ),
                    end_at=_parse_optional_datetime(
                        request.POST.get("end_at")
                    ),
                )
                return _redirect_to_tab()

        if action == "update_homepage_banner":
            banner = get_object_or_404(
                HomepageBanner,
                id=request.POST.get("banner_id"),
                country=country,
            )

            new_title = (request.POST.get("title") or "").strip()
            if not new_title:
                error = "Заголовок баннера не может быть пустым"
            else:
                action_type = (
                    request.POST.get("action_type")
                    or HomepageBanner.ACTION_NONE
                ).strip()
                valid_actions = {key for key, _ in HomepageBanner.ACTION_TYPE_CHOICES}
                if action_type not in valid_actions:
                    action_type = HomepageBanner.ACTION_NONE

                banner.title = new_title
                banner.subtitle = (request.POST.get("subtitle") or "").strip()
                banner.show_text = bool(request.POST.get("show_text"))
                banner.desktop_image = (
                    request.POST.get("desktop_image") or ""
                ).strip()
                banner.mobile_image = (
                    request.POST.get("mobile_image") or ""
                ).strip()
                banner.action_type = action_type
                banner.action_value = (
                    request.POST.get("action_value") or ""
                ).strip()
                banner.sort_order = _parse_int_or_zero(
                    request.POST.get("sort_order")
                )
                banner.is_active = bool(request.POST.get("is_active"))
                banner.start_at = _parse_optional_datetime(
                    request.POST.get("start_at")
                )
                banner.end_at = _parse_optional_datetime(
                    request.POST.get("end_at")
                )
                banner.save()
                return _redirect_to_tab()

        if action == "delete_homepage_banner":
            banner = get_object_or_404(
                HomepageBanner,
                id=request.POST.get("banner_id"),
                country=country,
            )
            banner.delete()
            return _redirect_to_tab()

        # ---- Bestsellers (Part 12 — Хиты продаж) ----
        # Bestsellers reuse the existing Dish.is_featured flag. There is
        # no separate Bestseller model. The actions below toggle the flag
        # and tweak site_sort_order; the public API at
        # /api/public/home/bestsellers already reads is_featured directly.
        #
        # Every action confirms country ownership via get_object_or_404(
        # Dish, id=..., country=country) so an Uzbekistan admin can't
        # mark a Montenegrin dish as featured by sending its id.

        if action == "add_bestseller":
            dish = get_object_or_404(
                Dish,
                id=request.POST.get("dish_id"),
                country=country,
            )
            if not dish.is_featured:
                dish.is_featured = True
                dish.save(update_fields=["is_featured"])
            return _redirect_to_tab()

        if action == "update_bestseller":
            dish = get_object_or_404(
                Dish,
                id=request.POST.get("dish_id"),
                country=country,
            )
            # Keep is_featured=True even if it somehow drifted — this action
            # is only callable from the featured-dishes list, so the dish
            # should stay in that list after save.
            dish.is_featured = True
            dish.site_sort_order = _parse_int_or_zero(
                request.POST.get("site_sort_order")
            )
            dish.save(update_fields=["is_featured", "site_sort_order"])
            return _redirect_to_tab()

        if action == "remove_bestseller":
            dish = get_object_or_404(
                Dish,
                id=request.POST.get("dish_id"),
                country=country,
            )
            if dish.is_featured:
                dish.is_featured = False
                dish.save(update_fields=["is_featured"])
            # NB: we never delete the Dish itself — only flip the flag.
            return _redirect_to_tab()

        # ---- Frequently bought blocks (Part 12 — Часто заказывают вместе) ----
        # Two related models:
        #   - HomepageProductBlock      (the block itself)
        #   - HomepageProductBlockItem  (one dish inside one block)
        #
        # Country ownership is verified for every action via
        # get_object_or_404(..., country=country) on the block. Items are
        # validated via their block to keep the cross-country guarantee.
        #
        # The (block, dish) pair has a DB-level UniqueConstraint, so we use
        # get_or_create when adding an item — duplicate submissions are
        # silently ignored instead of crashing the page.

        if action == "create_homepage_product_block":
            title = (request.POST.get("title") or "").strip()
            if not title:
                error = "Укажи название блока"
            else:
                HomepageProductBlock.objects.create(
                    country=country,
                    title=title,
                    sort_order=_parse_int_or_zero(
                        request.POST.get("sort_order")
                    ),
                    is_active=bool(request.POST.get("is_active")),
                )
                return _redirect_to_tab()

        if action == "update_homepage_product_block":
            block = get_object_or_404(
                HomepageProductBlock,
                id=request.POST.get("block_id"),
                country=country,
            )
            new_title = (request.POST.get("title") or "").strip()
            if not new_title:
                error = "Название блока не может быть пустым"
            else:
                block.title = new_title
                block.sort_order = _parse_int_or_zero(
                    request.POST.get("sort_order")
                )
                block.is_active = bool(request.POST.get("is_active"))
                block.save()
                return _redirect_to_tab()

        if action == "delete_homepage_product_block":
            block = get_object_or_404(
                HomepageProductBlock,
                id=request.POST.get("block_id"),
                country=country,
            )
            # Cascade-deletes HomepageProductBlockItem rows automatically
            # (on_delete=CASCADE in the model). Dishes themselves are NOT
            # affected — the FK from item to dish doesn't propagate.
            block.delete()
            return _redirect_to_tab()

        if action == "add_homepage_product_block_item":
            block = get_object_or_404(
                HomepageProductBlock,
                id=request.POST.get("block_id"),
                country=country,
            )
            dish = get_object_or_404(
                Dish,
                id=request.POST.get("dish_id"),
                country=country,
            )
            # get_or_create gracefully handles the unique (block, dish)
            # constraint. If a row already exists we don't touch it — the
            # operator can edit it from its row directly.
            HomepageProductBlockItem.objects.get_or_create(
                block=block,
                dish=dish,
                defaults={
                    "sort_order": _parse_int_or_zero(
                        request.POST.get("sort_order")
                    ),
                    "is_active": bool(request.POST.get("is_active")),
                },
            )
            return _redirect_to_tab()

        if action == "update_homepage_product_block_item":
            # Look up the item via the block to enforce country ownership
            # without trusting the bare item_id.
            block = get_object_or_404(
                HomepageProductBlock,
                id=request.POST.get("block_id"),
                country=country,
            )
            item = get_object_or_404(
                HomepageProductBlockItem,
                id=request.POST.get("item_id"),
                block=block,
            )
            item.sort_order = _parse_int_or_zero(
                request.POST.get("sort_order")
            )
            item.is_active = bool(request.POST.get("is_active"))
            item.save(update_fields=["sort_order", "is_active"])
            return _redirect_to_tab()

        if action == "delete_homepage_product_block_item":
            block = get_object_or_404(
                HomepageProductBlock,
                id=request.POST.get("block_id"),
                country=country,
            )
            item = get_object_or_404(
                HomepageProductBlockItem,
                id=request.POST.get("item_id"),
                block=block,
            )
            # Only the item row is removed — Dish stays intact.
            item.delete()
            return _redirect_to_tab()

        # ---- Compact upsell blocks (Part 2.1 — Компактный блок допродаж) ----
        # SEPARATE feature from "Часто заказывают вместе" above. These actions
        # manage HomepageCompactUpsellBlock only (block-level CRUD). Product /
        # item management lives in Part 2.2.
        #
        # Country ownership is enforced on every action via
        # get_object_or_404(..., country=country). Deleting a block cascades
        # to its HomepageCompactUpsellItem rows (on_delete=CASCADE) but never
        # touches the Dish records themselves.

        if action == "create_compact_upsell_block":
            title = (request.POST.get("title") or "").strip()
            if not title:
                error = "Укажи название компактного блока"
            else:
                # placement: "home" (default — backwards compat) or "cart".
                # The form posts it as a hidden field on each section's
                # create form, so accidentally creating a "home" block from
                # the cart section UI is impossible.
                placement = (request.POST.get("placement") or "").strip()
                if placement not in (
                    HomepageCompactUpsellBlock.PLACEMENT_HOME,
                    HomepageCompactUpsellBlock.PLACEMENT_CART,
                ):
                    placement = HomepageCompactUpsellBlock.PLACEMENT_HOME
                HomepageCompactUpsellBlock.objects.create(
                    country=country,
                    title=title,
                    placement=placement,
                    sort_order=_parse_int_or_zero(
                        request.POST.get("sort_order")
                    ),
                    is_active=bool(request.POST.get("is_active")),
                )
                return _redirect_to_tab()

        if action == "update_compact_upsell_block":
            block = get_object_or_404(
                HomepageCompactUpsellBlock,
                id=request.POST.get("block_id"),
                country=country,
            )
            new_title = (request.POST.get("title") or "").strip()
            if not new_title:
                error = "Название компактного блока не может быть пустым"
            else:
                block.title = new_title
                block.sort_order = _parse_int_or_zero(
                    request.POST.get("sort_order")
                )
                block.is_active = bool(request.POST.get("is_active"))
                block.save()
                return _redirect_to_tab()

        if action == "delete_compact_upsell_block":
            block = get_object_or_404(
                HomepageCompactUpsellBlock,
                id=request.POST.get("block_id"),
                country=country,
            )
            # Cascade-deletes HomepageCompactUpsellItem rows automatically.
            # Dishes are NOT affected — the item→dish FK doesn't propagate.
            block.delete()
            return _redirect_to_tab()

        # ---- Compact upsell items (Part 2.2 — товары внутри блока) ----
        # Manage HomepageCompactUpsellItem rows inside a compact block.
        #
        # Country ownership: the block is fetched with country=country, and
        # items are always looked up via that block, so a forged item_id from
        # another country can't be touched. The dish is also fetched with
        # country=country when adding, so cross-country dishes are rejected.
        #
        # The (block, dish) pair has a DB-level UniqueConstraint, so adding
        # uses get_or_create — duplicate submissions are ignored safely
        # instead of raising IntegrityError. No action here ever modifies the
        # Dish itself; only the item rows change.

        if action == "add_compact_upsell_item":
            block = get_object_or_404(
                HomepageCompactUpsellBlock,
                id=request.POST.get("block_id"),
                country=country,
            )
            dish = get_object_or_404(
                Dish,
                id=request.POST.get("dish_id"),
                country=country,
            )
            # get_or_create gracefully handles the unique (block, dish)
            # constraint. If the dish is already in the block we leave the
            # existing row untouched — the operator edits it from its row.
            HomepageCompactUpsellItem.objects.get_or_create(
                block=block,
                dish=dish,
                defaults={
                    "sort_order": _parse_int_or_zero(
                        request.POST.get("sort_order")
                    ),
                    "is_active": bool(request.POST.get("is_active")),
                },
            )
            return _redirect_to_tab()

        if action == "update_compact_upsell_item":
            # Look up the item via its block to enforce country ownership
            # without trusting the bare item_id.
            block = get_object_or_404(
                HomepageCompactUpsellBlock,
                id=request.POST.get("block_id"),
                country=country,
            )
            item = get_object_or_404(
                HomepageCompactUpsellItem,
                id=request.POST.get("item_id"),
                block=block,
            )
            item.sort_order = _parse_int_or_zero(
                request.POST.get("sort_order")
            )
            item.is_active = bool(request.POST.get("is_active"))
            # Only item fields change — never the Dish.
            item.save(update_fields=["sort_order", "is_active"])
            return _redirect_to_tab()

        if action == "delete_compact_upsell_item":
            block = get_object_or_404(
                HomepageCompactUpsellBlock,
                id=request.POST.get("block_id"),
                country=country,
            )
            item = get_object_or_404(
                HomepageCompactUpsellItem,
                id=request.POST.get("item_id"),
                block=block,
            )
            # Only the item row is removed — Dish stays in the catalog.
            item.delete()
            return _redirect_to_tab()

        # ----- Combo banners ("Комбо и акции" section) -----
        # Validation lives here rather than in the model because we want
        # the operator to see "Неверный HEX" on the form, not a 500 from
        # full_clean() bubbling up. Length limits are enforced by the
        # CharField max_length and silently truncated on the input side
        # (browsers already enforce maxlength) — anything else we catch
        # with explicit checks below.

        if action == "create_combo_banner":
            errs = _validate_combo_banner_form(request.POST)
            if errs:
                error = errs
            else:
                banner = HomeComboBanner(
                    country=country,
                    title=(request.POST.get("title") or "").strip(),
                    subtitle=(request.POST.get("subtitle") or "").strip(),
                    cta_label=(request.POST.get("cta_label") or "").strip(),
                    background_color=_clean_hex(
                        request.POST.get("background_color"), default="#181818",
                    ),
                    text_color=_clean_combo_text_color(
                        request.POST.get("text_color"),
                    ),
                    cta_action_type=_clean_combo_action_type(
                        request.POST.get("cta_action_type"),
                    ),
                    cta_action_value=(
                        request.POST.get("cta_action_value") or ""
                    ).strip(),
                    background_image_url=(
                        request.POST.get("background_image_url") or ""
                    ).strip(),
                    sort_order=_parse_int_or_zero(
                        request.POST.get("sort_order")
                    ),
                    is_active=bool(request.POST.get("is_active")),
                )
                uploaded = request.FILES.get("background_image")
                if uploaded:
                    banner.background_image = uploaded
                banner.save()
                return _redirect_to_tab()

        if action == "update_combo_banner":
            banner = get_object_or_404(
                HomeComboBanner,
                id=request.POST.get("banner_id"),
                country=country,
            )
            errs = _validate_combo_banner_form(request.POST)
            if errs:
                error = errs
            else:
                banner.title = (request.POST.get("title") or "").strip()
                banner.subtitle = (request.POST.get("subtitle") or "").strip()
                banner.cta_label = (request.POST.get("cta_label") or "").strip()
                banner.background_color = _clean_hex(
                    request.POST.get("background_color"), default="#181818",
                )
                banner.text_color = _clean_combo_text_color(
                    request.POST.get("text_color"),
                )
                banner.cta_action_type = _clean_combo_action_type(
                    request.POST.get("cta_action_type"),
                )
                banner.cta_action_value = (
                    request.POST.get("cta_action_value") or ""
                ).strip()
                banner.background_image_url = (
                    request.POST.get("background_image_url") or ""
                ).strip()
                banner.sort_order = _parse_int_or_zero(
                    request.POST.get("sort_order")
                )
                banner.is_active = bool(request.POST.get("is_active"))

                # Image handling — same precedence rules used elsewhere:
                # new upload wins, then "clear" detaches, else preserve.
                uploaded = request.FILES.get("background_image")
                if uploaded:
                    if banner.background_image:
                        banner.background_image.delete(save=False)
                    banner.background_image = uploaded
                elif request.POST.get("background_image_clear"):
                    if banner.background_image:
                        banner.background_image.delete(save=False)
                    banner.background_image = None

                banner.save()
                return _redirect_to_tab()

        if action == "delete_combo_banner":
            banner = get_object_or_404(
                HomeComboBanner,
                id=request.POST.get("banner_id"),
                country=country,
            )
            if banner.background_image:
                banner.background_image.delete(save=False)
            banner.delete()
            return _redirect_to_tab()

        # ---------------------------------------------------------------
        # Reorder action — fires when an operator drags a row in any of
        # the page's tabs. Body is JSON-encoded form data, payload:
        #
        #   action       = "reorder"
        #   entity       = "homepage_banner" | "combo_banner"
        #                 | "compact_upsell_block" | "compact_upsell_item"
        #                 | "homepage_product_block" | "product_block_item"
        #                 | "bestseller"
        #   ids          = JSON array of ids in the new order
        #   parent_id    = optional, used for nested items (e.g. items
        #                  inside a compact_upsell_block need the block id
        #                  so we don't mutate sort_order on items from
        #                  another block).
        #
        # We answer with HTTP 204 on success — front-end keeps the order
        # the user already sees, no full reload needed. On error we return
        # 400 with a short reason; the front-end shows a toast.
        if action == "reorder":
            from django.http import JsonResponse, HttpResponseBadRequest

            entity = (request.POST.get("entity") or "").strip()
            try:
                ids = json.loads(request.POST.get("ids") or "[]")
            except (ValueError, TypeError):
                return HttpResponseBadRequest("bad ids")
            if not isinstance(ids, list):
                return HttpResponseBadRequest("ids must be a list")
            try:
                ids = [int(i) for i in ids]
            except (TypeError, ValueError):
                return HttpResponseBadRequest("ids must be integers")

            parent_id = request.POST.get("parent_id")
            try:
                parent_id = int(parent_id) if parent_id else None
            except (TypeError, ValueError):
                parent_id = None

            # Whitelist of entity → (Model, parent FK kwarg or None).
            # The parent kwarg scopes the update to one container so a
            # malicious payload can't touch sibling collections.
            ENTITY_MAP = {
                "homepage_banner": (HomepageBanner, "country"),
                "combo_banner": (HomeComboBanner, "country"),
                "compact_upsell_block": (HomepageCompactUpsellBlock, "country"),
                "compact_upsell_item": (HomepageCompactUpsellItem, "block_id"),
                "homepage_product_block": (HomepageProductBlock, "country"),
                "product_block_item": (HomepageProductBlockItem, "block_id"),
            }
            mapping = ENTITY_MAP.get(entity)
            if mapping is None:
                return HttpResponseBadRequest("unknown entity")
            Model, parent_kw = mapping

            if parent_kw == "country":
                qs = Model.objects.filter(country=country, id__in=ids)
            elif parent_kw == "block_id":
                if parent_id is None:
                    return HttpResponseBadRequest("parent_id required")
                # Verify the parent block belongs to this country before
                # touching items.
                if entity == "compact_upsell_item":
                    parent = HomepageCompactUpsellBlock.objects.filter(
                        id=parent_id, country=country,
                    ).first()
                else:  # product_block_item
                    parent = HomepageProductBlock.objects.filter(
                        id=parent_id, country=country,
                    ).first()
                if parent is None:
                    return HttpResponseBadRequest("parent not found")
                qs = Model.objects.filter(block_id=parent_id, id__in=ids)
            else:
                qs = Model.objects.none()

            by_id = {obj.id: obj for obj in qs}
            for position, obj_id in enumerate(ids):
                obj = by_id.get(obj_id)
                if obj is None:
                    continue
                if obj.sort_order != position:
                    obj.sort_order = position
                    obj.save(update_fields=["sort_order"])

            return JsonResponse({"ok": True})

        # ---------------------------------------------------------------
        # Bestseller reorder — Dish.site_sort_order, not a dedicated table.
        # Kept separate from the generic reorder above because the field
        # lives on Dish itself and we filter by is_featured=True instead.
        if action == "reorder_bestsellers":
            from django.http import JsonResponse, HttpResponseBadRequest

            try:
                ids = json.loads(request.POST.get("ids") or "[]")
                ids = [int(i) for i in ids]
            except (ValueError, TypeError):
                return HttpResponseBadRequest("bad ids")

            qs = Dish.objects.filter(
                country=country, is_featured=True, id__in=ids,
            )
            by_id = {d.id: d for d in qs}
            for position, dish_id in enumerate(ids):
                d = by_id.get(dish_id)
                if d is None:
                    continue
                if d.site_sort_order != position:
                    d.site_sort_order = position
                    d.save(update_fields=["site_sort_order"])

            return JsonResponse({"ok": True})

    # ---- GET render ----
    now = timezone.now()

    banners_qs = (
        HomepageBanner.objects
        .filter(country=country)
        .order_by("sort_order", "id")
    )

    # Decorate with status + a pre-formatted local string for the
    # datetime-local input so the template stays simple. We do this in
    # Python rather than a template tag to avoid adding a custom tag for
    # one screen.
    banners = []
    current_tz = timezone.get_current_timezone()
    for b in banners_qs:
        b.status = _banner_status(b, now)
        b.start_at_input = (
            timezone.localtime(b.start_at, current_tz).strftime("%Y-%m-%dT%H:%M")
            if b.start_at else ""
        )
        b.end_at_input = (
            timezone.localtime(b.end_at, current_tz).strftime("%Y-%m-%dT%H:%M")
            if b.end_at else ""
        )
        banners.append(b)

    # ---- Bestsellers querysets (Part 12 — Хиты продаж) ----
    featured_dishes = (
        Dish.objects
        .filter(country=country, is_featured=True)
        .order_by("site_sort_order", "name")
    )
    available_dishes = (
        Dish.objects
        .filter(country=country, is_featured=False)
        .order_by("name")
    )

    # ---- Frequently bought blocks (Part 12 — Часто заказывают вместе) ----
    # Prefetch items + the dish each item points at, so the template can
    # render one block with all its products in a single SQL roundtrip per
    # block instead of one query per item.
    homepage_blocks = (
        HomepageProductBlock.objects
        .filter(country=country)
        .order_by("sort_order", "id")
        .prefetch_related(
            Prefetch(
                "items",
                queryset=(
                    HomepageProductBlockItem.objects
                    .select_related("dish")
                    .order_by("sort_order", "id")
                ),
            ),
        )
    )

    # Dropdown source — every dish in the current country, by name.
    # Per-block deduplication happens in the template via JS-free
    # rendering: if the dish is already in the block, the operator simply
    # sees no effect from the add action (get_or_create), no error spam.
    available_block_dishes = (
        Dish.objects
        .filter(country=country)
        .order_by("name")
    )

    # ---- Compact upsell blocks ----
    # Split by placement so the template can render two separate sections:
    # one for the homepage, one for the cart page. They share the model
    # and all CRUD code paths; only the placement filter differs.
    compact_upsell_qs = (
        HomepageCompactUpsellBlock.objects
        .filter(country=country)
        .order_by("sort_order", "id")
        .prefetch_related(
            Prefetch(
                "items",
                queryset=(
                    HomepageCompactUpsellItem.objects
                    .select_related("dish")
                    .order_by("sort_order", "id")
                ),
            ),
        )
    )
    compact_upsell_blocks = compact_upsell_qs.filter(
        placement=HomepageCompactUpsellBlock.PLACEMENT_HOME,
    )
    cart_upsell_blocks = compact_upsell_qs.filter(
        placement=HomepageCompactUpsellBlock.PLACEMENT_CART,
    )

    # Dropdown source for adding a dish to a compact block — every dish in the
    # current country, by name. Per-block dedup is handled by get_or_create on
    # the add action, so showing the full list here is safe.
    available_compact_upsell_dishes = (
        Dish.objects
        .filter(country=country)
        .order_by("name")
    )

    # ---- Combo banners (paired CTA cards in "Комбо и акции" section) ----
    combo_banners = (
        HomeComboBanner.objects
        .filter(country=country)
        .order_by("sort_order", "id")
    )

    # Active tab — picked from ?tab= query string, defaults to "banners".
    # The template uses this to highlight the right pill and to swap which
    # section is rendered. Keeping it URL-driven means the operator can
    # reload / share / back-button between tabs.
    VALID_TABS = {
        "banners", "combo", "compact_upsell",
        "product_blocks", "bestsellers",
    }
    active_tab = request.GET.get("tab") or "banners"
    if active_tab not in VALID_TABS:
        active_tab = "banners"

    # Sub-tab for the compact upsell tab — picks home or cart placement.
    # Same URL-driven pattern as the main tabs. Ignored on other tabs.
    active_placement = (request.GET.get("placement") or "home").strip()
    if active_placement not in ("home", "cart"):
        active_placement = "home"

    # Pick which list + labels to expose to the template under the
    # `current_*` names. We do this here (not via {% with %} in the
    # template) because Django parses templates synchronously — it
    # can't tolerate a {% with %} opened inside one {% if %} branch
    # and closed in another. Doing it in Python is cleaner and the
    # template just uses `current_blocks` etc.
    if active_placement == "cart":
        current_blocks = cart_upsell_blocks
        current_placement_label = "корзине"
        current_placement_default_title = "Добавить к заказу"
        current_placement_surface_hint = (
            "На странице корзины под прогрессом доставки."
        )
    else:
        current_blocks = compact_upsell_blocks
        current_placement_label = "главной"
        current_placement_default_title = "Часто заказывают вместе"
        current_placement_surface_hint = (
            "На главной странице сайта в виде горизонтальной ленты."
        )

    return render(request, "foodcost/homepage_settings.html", {
        "country": country,
        "banners": banners,
        "action_choices": HomepageBanner.ACTION_TYPE_CHOICES,
        "ACTION_NONE": HomepageBanner.ACTION_NONE,
        "now": now,
        "error": error,
        "active_tab": active_tab,
        "active_placement": active_placement,
        # Bestsellers (Part 12)
        "featured_dishes": featured_dishes,
        "available_dishes": available_dishes,
        # Frequently bought blocks (Part 12)
        "homepage_blocks": homepage_blocks,
        "available_block_dishes": available_block_dishes,
        # Compact upsell — homepage placement
        "compact_upsell_blocks": compact_upsell_blocks,
        # Compact upsell — cart placement (same model, different placement)
        "cart_upsell_blocks": cart_upsell_blocks,
        # Compact upsell dish dropdown (shared between home + cart placements)
        "available_compact_upsell_dishes": available_compact_upsell_dishes,
        # Compact upsell — selected placement view (aliased above so the
        # template doesn't have to choose between *_blocks lists itself)
        "current_blocks": current_blocks,
        "current_placement_label": current_placement_label,
        "current_placement_default_title": current_placement_default_title,
        "current_placement_surface_hint": current_placement_surface_hint,
        # Combo banners — "Комбо и акции" pair on the homepage
        "combo_banners": combo_banners,
        "combo_action_choices": HomeComboBanner.ACTION_TYPE_CHOICES,
        "combo_text_color_choices": HomeComboBanner.TEXT_COLOR_CHOICES,
        "COMBO_ACTION_NONE": HomeComboBanner.ACTION_NONE,
        # Placement constants for hidden form fields
        "PLACEMENT_HOME": HomepageCompactUpsellBlock.PLACEMENT_HOME,
        "PLACEMENT_CART": HomepageCompactUpsellBlock.PLACEMENT_CART,
    })
