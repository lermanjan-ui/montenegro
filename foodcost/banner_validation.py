"""
Validation for banner action_value fields.

`action_value` lives on HomepageBanner and HomeComboBanner. It's user-typed
free-form text that says "where the banner click should go" depending on
`action_type`. Without validation managers occasionally paste:

    http://localhost:3000/category/pasta

…copied from their local dev environment, and the resulting prod banner
404s on click. We block that here at the ERP form level — the public API
never sees the bad value because it can't be saved in the first place.

Design notes
============
* Validators return (ok: bool, error_message: str|None) tuples instead of
  raising — they're called from form-handler views in views_homepage.py
  which already use a single-error-string convention (`error = ...`,
  redirect to the tab). Raising would require a try/except around each
  call site; the tuple shape fits the existing code.

* We accept *relative paths only* (`/category/pasta`) and reject any
  absolute URL — including https://raccoon.uz/category/pasta. Reasons:
    - the frontend (Next.js) treats relative paths as internal navigation,
      keeping the user in the SPA without a hard reload
    - absolute URLs to the same host bypass that and cause a full page
      load — slower and breaks ISR caching
    - it also catches the common copy-paste-from-localhost bug

* The `external_url` action_type is the one exception — it expects a
  full URL (those banners are explicitly meant to take the user off-site).
  We require https:// for those so we don't ship http:// links from a
  https:// site.

* `promo_code` action_value is a plain promo code string ("APP30"). No
  URL rules apply — only basic length / character checks.
"""

import re
from urllib.parse import urlparse


# Localhost/loopback variants the validator must reject as action_value.
# Order matters: longer prefixes first so a partial match doesn't
# accidentally pass through (not strictly necessary with startswith()
# but keeps intent obvious).
_LOCALHOST_PATTERNS = (
    "http://localhost",
    "https://localhost",
    "http://127.0.0.1",
    "https://127.0.0.1",
    "localhost:",
    "127.0.0.1:",
)


# slug-ish path segment after /category/ or /product/. Allows cyrillic
# (raccoon.uz has Russian-character slugs like "комбо"), latin, digits,
# hyphen, underscore, and percent-encoded chars from copy-paste. We
# don't try to fully URL-encode-validate here — the goal is to catch
# obviously-broken values like "" (empty after slash) or a full URL,
# not to enforce slug perfection.
_PATH_SEGMENT_RE = re.compile(r"^/[A-Za-z0-9_\-%\u0400-\u04FF]+(?:/.*)?$")


def validate_action_value(action_type, action_value):
    """Validate an `action_value` against its declared `action_type`.

    Args:
        action_type: one of "category", "product", "external_url",
            "promo_code", "none". Unknown types are treated as "none"
            (permissive — the calling view already coerces unknown
            action_types to ACTION_NONE before reaching this validator).
        action_value: raw user input, already stripped.

    Returns:
        (True, None) when valid.
        (False, "human-readable Russian error") otherwise.
    """
    # Normalize: empty value is always OK for "none". For other types
    # the caller has its own "value required" check before us.
    value = (action_value or "").strip()

    # "none" — value is ignored entirely. Don't validate anything; managers
    # often leave a stale value when switching the type to "none" and
    # forcing them to clear it would be annoying.
    if action_type == "none":
        return True, None

    if not value:
        # The caller normally catches this earlier with "value required";
        # double-check here so a bypass doesn't silently save an empty
        # action_value with a non-none action_type.
        return False, "Для выбранного типа действия нужно указать значение."

    # promo_code — bare code string, NOT a URL. Reject anything that
    # looks like a URL to catch the same copy-paste mistake.
    if action_type == "promo_code":
        if "/" in value or value.startswith("http"):
            return False, (
                "Для типа «Промокод» укажите сам код (например, APP30), "
                "а не ссылку."
            )
        if len(value) > 64:
            return False, "Промокод не длиннее 64 символов."
        return True, None

    # external_url — full https:// URL, off-site by design.
    if action_type == "external_url":
        # Reject localhost even here — there's no scenario where a real
        # external link points at localhost.
        low = value.lower()
        for pat in _LOCALHOST_PATTERNS:
            if pat in low:
                return False, (
                    "Внешняя ссылка не должна указывать на localhost — "
                    "проверьте, что вы не скопировали URL из локальной "
                    "разработки."
                )
        if not (value.startswith("https://") or value.startswith("http://")):
            return False, (
                "Внешняя ссылка должна начинаться с https:// "
                "(или http:// для тестовых сервисов)."
            )
        try:
            parsed = urlparse(value)
        except ValueError:
            return False, "Не удалось разобрать ссылку. Проверьте формат."
        if not parsed.netloc:
            return False, "Внешняя ссылка должна содержать домен."
        return True, None

    # category / product — relative path only, must start with the right
    # prefix. This is the main rule that catches localhost copy-paste.
    if action_type in ("category", "product"):
        low = value.lower()

        # First — block any absolute URL or localhost-y input.
        if value.startswith("http://") or value.startswith("https://"):
            return False, (
                "Используйте относительный путь (например, /category/pasta), "
                "а не полную ссылку. Это нужно, чтобы баннер работал и на "
                "проде, и на тесте."
            )
        for pat in _LOCALHOST_PATTERNS:
            if pat in low:
                return False, (
                    "Уберите localhost из ссылки. Используйте относительный "
                    "путь — например, /category/pasta."
                )

        expected_prefix = "/category/" if action_type == "category" else "/product/"
        if not value.startswith(expected_prefix):
            human = (
                "/category/<категория>" if action_type == "category"
                else "/product/<товар>"
            )
            return False, (
                f"Для типа «{ 'Категория' if action_type == 'category' else 'Товар' }» "
                f"путь должен начинаться с {expected_prefix} "
                f"(например, {human})."
            )

        # Sanity: there must be SOMETHING after the prefix
        if value == expected_prefix or value == expected_prefix.rstrip("/"):
            return False, (
                f"После {expected_prefix} укажите slug — "
                f"например, {expected_prefix}pasta."
            )

        return True, None

    # Unknown action_type — be permissive. The view already coerces
    # unknown types to ACTION_NONE before saving; reaching this branch
    # means we have a new action_type the validator doesn't know about
    # yet. Better to allow than to break the page.
    return True, None


# -----------------------------------------------------------------------------
# Auto-fix helpers used by the `fix_banner_urls` management command.
# Kept here next to the validator so the "what's valid" and "how to repair"
# rules live in one file.
# -----------------------------------------------------------------------------

def autofix_action_value(action_type, action_value):
    """Try to mechanically repair a broken action_value.

    Returns (fixed_value, was_changed: bool). When the function can't
    confidently repair the value (e.g. an external_url is malformed),
    it returns the input unchanged and lets the manager fix it by hand.

    Repair rules:
      - absolute URL with localhost/127.0.0.1 → keep only the URL path
        ("http://localhost:3000/category/pasta" → "/category/pasta")
      - absolute URL with raccoon.uz domain → keep only the URL path
        (same idea: "https://raccoon.uz/product/x" → "/product/x")
      - other absolute URLs on category/product → strip to path
      - external_url with localhost → no auto-fix (we don't know the
        real production URL); manager must edit
      - empty / "/" / "/category/" → no auto-fix
    """
    value = (action_value or "").strip()
    if not value:
        return value, False

    if action_type in ("category", "product"):
        if value.startswith("http://") or value.startswith("https://"):
            try:
                parsed = urlparse(value)
            except ValueError:
                return value, False
            path = parsed.path or "/"
            if path != value:
                return path, True
        # Already a relative path — nothing to fix automatically (a wrong
        # prefix like "/c/pasta" instead of "/category/pasta" is a
        # judgement call we leave to the manager).
        return value, False

    if action_type == "external_url":
        low = value.lower()
        for pat in _LOCALHOST_PATTERNS:
            if pat in low:
                # We don't know what production URL this should be —
                # can't auto-fix.
                return value, False
        return value, False

    if action_type == "promo_code":
        # If somebody saved a URL into a promo_code field, take just the
        # last path segment — best-effort guess, manager should verify.
        if value.startswith("http://") or value.startswith("https://"):
            try:
                parsed = urlparse(value)
            except ValueError:
                return value, False
            tail = (parsed.path or "").rstrip("/").rsplit("/", 1)[-1]
            if tail:
                return tail, True
        return value, False

    return value, False
