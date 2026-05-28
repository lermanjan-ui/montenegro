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

from .models import (
    UserProfile,
    HomepageBanner,
    Dish,
    HomepageProductBlock,
    HomepageProductBlockItem,
    HomepageCompactUpsellBlock,
    HomepageCompactUpsellItem,
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
                return redirect(f"/c/{country.slug}/settings/homepage/")

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
                return redirect(f"/c/{country.slug}/settings/homepage/")

        if action == "delete_homepage_banner":
            banner = get_object_or_404(
                HomepageBanner,
                id=request.POST.get("banner_id"),
                country=country,
            )
            banner.delete()
            return redirect(f"/c/{country.slug}/settings/homepage/")

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
            return redirect(f"/c/{country.slug}/settings/homepage/")

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
            return redirect(f"/c/{country.slug}/settings/homepage/")

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
            return redirect(f"/c/{country.slug}/settings/homepage/")

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
                return redirect(f"/c/{country.slug}/settings/homepage/")

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
                return redirect(f"/c/{country.slug}/settings/homepage/")

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
            return redirect(f"/c/{country.slug}/settings/homepage/")

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
            return redirect(f"/c/{country.slug}/settings/homepage/")

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
            return redirect(f"/c/{country.slug}/settings/homepage/")

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
            return redirect(f"/c/{country.slug}/settings/homepage/")

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
                HomepageCompactUpsellBlock.objects.create(
                    country=country,
                    title=title,
                    sort_order=_parse_int_or_zero(
                        request.POST.get("sort_order")
                    ),
                    is_active=bool(request.POST.get("is_active")),
                )
                return redirect(f"/c/{country.slug}/settings/homepage/")

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
                return redirect(f"/c/{country.slug}/settings/homepage/")

        if action == "delete_compact_upsell_block":
            block = get_object_or_404(
                HomepageCompactUpsellBlock,
                id=request.POST.get("block_id"),
                country=country,
            )
            # Cascade-deletes HomepageCompactUpsellItem rows automatically.
            # Dishes are NOT affected — the item→dish FK doesn't propagate.
            block.delete()
            return redirect(f"/c/{country.slug}/settings/homepage/")

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

    # ---- Compact upsell blocks (Part 2.1 — Компактный блок допродаж) ----
    # Current country only, ordered by (sort_order, id). Items are prefetched
    # with their dish so the template can show a per-block product count
    # (and, in Part 2.2, the item list) without N+1 queries.
    compact_upsell_blocks = (
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

    return render(request, "foodcost/homepage_settings.html", {
        "country": country,
        "banners": banners,
        "action_choices": HomepageBanner.ACTION_TYPE_CHOICES,
        "ACTION_NONE": HomepageBanner.ACTION_NONE,
        "now": now,
        "error": error,
        # Bestsellers (Part 12)
        "featured_dishes": featured_dishes,
        "available_dishes": available_dishes,
        # Frequently bought blocks (Part 12)
        "homepage_blocks": homepage_blocks,
        "available_block_dishes": available_block_dishes,
        # Compact upsell blocks (Part 2.1)
        "compact_upsell_blocks": compact_upsell_blocks,
    })
