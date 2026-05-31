"""
Tilda CSV → ERP migration utilities.

Parses Tilda's leads CSV export into a normalized structure ready for
insertion. Pure functions only — no DB access here; that lives in the
management commands that orchestrate the actual import.

CSV format reference (verified against the May 2026 export):
    Email;Название;Телефон;Дата;Промокод;Товары в заказе;utm_campaign;
    Адрес_доставки;Комментарий;tranid;formid;formname;Сумма заказа;Stage

Notable quirks discovered during analysis:
  * `Сумма заказа` is the operator's final total — it does NOT equal the
    sum of line prices (deliveries, manual adjustments, promo discounts).
    We trust the column verbatim as Order.total_amount.
  * Items column packs multiple lines into one cell, separated by '\\n'.
    Per-line format: "<name> x <qty> ≡ <unit_price>"
  * Names sometimes carry add-on annotations in parentheses:
      "Альфредо ( Добавить: Дополнительный соус для пиццы, ...)"
    We treat the WHOLE string as the dish name — case-insensitive exact
    match against ERP catalog. If the ERP has a Dish named exactly
    "Альфредо" (without the parenthetical), it WON'T match — the strict
    matching strategy chosen for this import. Unmatched lines persist
    via dish_name_snapshot with dish=NULL.
  * Phone column is mostly "+998 NN-NNN-NNNN" but a few rows have
    duplicated prefixes ("+998 +998901234567") or odd spacing. We keep
    the value as-typed for customer_phone (operators recognize it),
    but normalize a "matching key" version (digits only) for dedupe
    against existing Customer records.
  * Stage is "Входящие" for everything in the export — Tilda doesn't
    distinguish completed vs cancelled, so we mark all imported orders
    as status=done at insert time (operator's call: these were real
    historical deliveries).
"""

import csv
import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Optional


# Tilda's column names — Russian, exactly as they appear in the export
# header. Keep this list in sync with the CSV; if Tilda changes the
# header in a future export, the validator at the bottom catches it.
TILDA_COLUMNS = [
    "Email",
    "Название",
    "Телефон",
    "Дата",
    "Промокод",
    "Товары в заказе",
    "utm_campaign",
    "Адрес_доставки",
    "Комментарий",
    "tranid",
    "formid",
    "formname",
    "Сумма заказа",
    "Stage",
]


# Line format inside "Товары в заказе". Backed by analysis of 1581 rows
# in the May-2026 export: every non-empty line matched this regex.
#
# Example: "Спайс суши гункан Краб x 1 ≡ 14000"
#   group "name" = "Спайс суши гункан Краб"
#   group "qty"  = "1"
#   group "price" = "14000"
#
# Quantity allows decimals (just in case of weight-based items in
# future exports). Price is integer in the current export but we
# allow decimal to be safe.
_ITEM_LINE_RE = re.compile(
    r"^\s*(?P<name>.+?)\s+x\s+(?P<qty>\d+(?:[.,]\d+)?)"
    # Price is optional — in rare cases (e.g. comp items, bug in Tilda)
    # the "≡ <price>" tail is missing. Treat as zero.
    r"(?:\s*≡\s*(?P<price>\d+(?:[.,]\d+)?))?\s*$",
    re.UNICODE,
)


@dataclass
class TildaItem:
    """One parsed line from `Товары в заказе`."""
    name: str             # raw name, exactly as typed in Tilda
    quantity: Decimal
    unit_price: Decimal
    matched_dish_id: Optional[int] = None  # filled in later via DB lookup


@dataclass
class TildaRow:
    """One parsed CSV row. Pure data; DB linking happens elsewhere."""
    line_number: int      # 1-based source line for error messages
    tranid: str           # idempotency key (already-imported check)
    customer_name: str
    customer_phone: str   # as-typed in source
    phone_key: str        # digits-only, for matching existing Customer
    order_date: datetime
    total: Decimal
    delivery_address: str
    customer_comment: str
    promo_code: str
    items: list = field(default_factory=list)
    # Validation diagnostics — non-fatal issues we surface in the
    # analyze report but don't block import for. Fatal issues raise
    # during parse.
    warnings: list = field(default_factory=list)


# -- Field-level parsers ----------------------------------------------------

def normalize_phone_key(phone):
    """Return digits-only version of a phone for dedupe.

    Examples:
        "+998 90-301-0709"      → "998903010709"
        "+998 +998901234567"    → "998998901234567"  (broken — see warning)
        "+998 (90) 123-45-67"   → "998901234567"

    The 'key' is used to find an existing Customer; the original
    string is kept as-typed for display in customer_phone.
    """
    if not phone:
        return ""
    return "".join(ch for ch in phone if ch.isdigit())


def parse_amount(raw):
    """Parse a money string into Decimal. Empty / unparseable → 0."""
    if raw is None:
        return Decimal("0")
    s = str(raw).strip().replace(",", ".").replace(" ", "")
    if not s:
        return Decimal("0")
    try:
        return Decimal(s)
    except (InvalidOperation, ValueError):
        return Decimal("0")


def parse_date(raw):
    """Parse Tilda's date format ("YYYY-MM-DD HH:MM:SS") into a naive
    datetime. Caller can localize. Returns None on unparseable input.
    """
    if not raw:
        return None
    raw = raw.strip()
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def parse_items(items_cell):
    """Split the multi-line items cell into TildaItem records.

    Items separator is '\\n'. Each line: '<name> x <qty> ≡ <unit_price>'.
    Lines that don't match the regex are surfaced as warnings, not
    silently dropped — so the analyze report flags suspicious data.

    Returns (items, warnings).
    """
    items = []
    warnings = []
    if not items_cell:
        return items, warnings

    for raw_line in items_cell.split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        m = _ITEM_LINE_RE.match(line)
        if not m:
            warnings.append(f"unparseable item line: {line!r}")
            continue
        items.append(TildaItem(
            name=m.group("name").strip(),
            quantity=parse_amount(m.group("qty")),
            unit_price=parse_amount(m.group("price")),  # parse_amount handles None → 0
        ))
    return items, warnings


# Names sometimes carry inline add-on annotations:
#   "Альфредо ( Добавить: Дополнительный соус для пиццы, ...)"
# This helper extracts the bare dish name (everything before " (" if
# present), useful for diagnostics but NOT for the matching strategy
# the user chose (strict case-insensitive exact match).
def strip_addon_annotation(name):
    """Return the part before " (" if present, else the whole name."""
    if not name:
        return name
    idx = name.find(" (")
    if idx == -1:
        return name
    return name[:idx].strip()


# -- Row-level parser -------------------------------------------------------

def parse_tilda_row(row_dict, line_number):
    """Convert one DictReader row into a TildaRow. Raises ValueError on
    fatal problems (missing required column, unparseable date).
    """
    # Required fields — if any of these is missing the row is unusable.
    tranid = (row_dict.get("tranid") or "").strip()
    phone = (row_dict.get("Телефон") or "").strip()
    total_raw = (row_dict.get("Сумма заказа") or "").strip()
    date_raw = (row_dict.get("Дата") or "").strip()

    if not tranid:
        raise ValueError(f"line {line_number}: tranid is empty")
    if not phone:
        raise ValueError(f"line {line_number}: phone is empty")

    date_parsed = parse_date(date_raw)
    if date_parsed is None:
        raise ValueError(f"line {line_number}: cannot parse date {date_raw!r}")

    items, item_warnings = parse_items(row_dict.get("Товары в заказе") or "")

    parsed = TildaRow(
        line_number=line_number,
        tranid=tranid,
        customer_name=(row_dict.get("Название") or "").strip() or "Клиент",
        customer_phone=phone,
        phone_key=normalize_phone_key(phone),
        order_date=date_parsed,
        total=parse_amount(total_raw),
        delivery_address=(row_dict.get("Адрес_доставки") or "").strip(),
        customer_comment=(row_dict.get("Комментарий") or "").strip(),
        promo_code=(row_dict.get("Промокод") or "").strip(),
        items=items,
    )
    parsed.warnings.extend(item_warnings)

    # Soft warnings — flag but don't block.
    if parsed.total <= 0:
        parsed.warnings.append("total is zero or negative")
    if not parsed.items:
        parsed.warnings.append("no parseable items")
    if not parsed.delivery_address:
        parsed.warnings.append("delivery address is empty")
    if len(parsed.phone_key) < 9:
        parsed.warnings.append(f"phone digits look too short: {parsed.phone_key!r}")

    return parsed


# -- File-level reader ------------------------------------------------------

def read_tilda_csv(path):
    """Read a Tilda CSV file into a list of TildaRow + a list of fatal
    parse errors. Caller decides what to do with errors.

    Returns (rows, errors).
    """
    rows = []
    errors = []

    with open(path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f, delimiter=";")
        # Sanity-check the header — protects against a Tilda export change
        # that silently shifts columns. Missing optional columns are OK;
        # missing required ones cause a clear early failure.
        header = reader.fieldnames or []
        missing = [c for c in TILDA_COLUMNS if c not in header]
        if missing:
            errors.append(
                f"CSV header is missing expected columns: {missing}. "
                f"Got: {header}"
            )
            return rows, errors

        # csv module's line counter is finicky — we keep our own (1-based,
        # counting from the first data row after the header).
        for i, row_dict in enumerate(reader, start=2):
            try:
                parsed = parse_tilda_row(row_dict, line_number=i)
            except ValueError as e:
                errors.append(str(e))
                continue
            rows.append(parsed)

    return rows, errors


# -- Reporting helpers ------------------------------------------------------

def dish_name_index(rows):
    """Build {lowercased_name → set(original_names)} index for diagnostic
    reports. Helps the analyze command list "what unique dish names
    appear in the CSV" before any DB matching happens.
    """
    index = {}
    for r in rows:
        for item in r.items:
            key = item.name.strip().lower()
            index.setdefault(key, set()).add(item.name.strip())
    return index


def unique_phones(rows):
    """Return a set of phone_keys observed in the rows."""
    return {r.phone_key for r in rows if r.phone_key}


def order_count_by_phone(rows):
    """{phone_key → number of orders}. Used to highlight power-users
    when reporting."""
    counts = {}
    for r in rows:
        counts[r.phone_key] = counts.get(r.phone_key, 0) + 1
    return counts
