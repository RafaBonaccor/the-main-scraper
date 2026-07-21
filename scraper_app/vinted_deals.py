import re

from .utils import normalize_whitespace


VINTED_DEAL_HUNTER_DEFAULT_TERMS = (
    "charm",
    "bracciali",
    "pandora",
    "collane",
    "gioielli",
    "ciondoli",
    "orecchini",
    "anelli",
)
VINTED_DEAL_HUNTER_DEFAULT_MIN_FAVORITES = 70
VINTED_DEAL_HUNTER_DEFAULT_MAX_AGE_HOURS = 24.0
VINTED_DEAL_HUNTER_DEFAULT_LOOP_SECONDS = 5
VINTED_DEAL_HUNTER_DEFAULT_MAX_RESULTS_PER_SEARCH = 250
VINTED_DEAL_HUNTER_DEFAULT_CATEGORY_LABEL = "Gioielli donna"
VINTED_DEAL_HUNTER_MAX_SHIPPING_PRICE = 2.99

_FAVORITE_COUNT_PATTERN = re.compile(r"\d+")


def normalize_vinted_deal_hunter_terms(raw_terms: object, *, use_default_when_empty: bool = True) -> list[str]:
    if isinstance(raw_terms, (list, tuple, set)):
        parts = [str(item or "").strip() for item in raw_terms]
    else:
        normalized = str(raw_terms or "").replace("\r", "\n").replace(";", ",")
        parts = [part.strip() for chunk in normalized.split("\n") for part in chunk.split(",")]

    terms: list[str] = []
    seen: set[str] = set()
    for part in parts:
        clean = normalize_whitespace(part)
        if not clean:
            continue
        dedupe_key = clean.lower()
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        terms.append(clean)
    if terms:
        return terms
    return list(VINTED_DEAL_HUNTER_DEFAULT_TERMS) if use_default_when_empty else []


def normalize_vinted_deal_hunter_min_favorites(value: object, default: int = 0) -> int:
    try:
        parsed = int(float(value))
    except (TypeError, ValueError):
        return max(int(default), 0)
    return max(parsed, 0)


def normalize_vinted_deal_hunter_max_age_hours(value: object, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return max(float(default), 0.0)
    return max(parsed, 0.0)


def normalize_vinted_deal_hunter_max_price(value: object, default: float | None = None) -> float | None:
    if value in (None, ""):
        return default
    text = normalize_whitespace(str(value or "")).replace("€", "").replace(" ", "")
    if not text:
        return default
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    try:
        parsed = float(text)
    except (TypeError, ValueError):
        return default
    return max(parsed, 0.0)


def vinted_deal_hunter_enabled(min_favorites: object, max_age_hours: object) -> bool:
    return normalize_vinted_deal_hunter_min_favorites(min_favorites) > 0 and normalize_vinted_deal_hunter_max_age_hours(max_age_hours) > 0


def coerce_vinted_favorite_count(value: object) -> int | None:
    if isinstance(value, int):
        return value if value >= 0 else None
    text = normalize_whitespace(str(value or ""))
    if not text:
        return None
    match = _FAVORITE_COUNT_PATTERN.search(text.replace(".", "").replace(" ", ""))
    if not match:
        return None
    try:
        parsed = int(match.group(0))
    except ValueError:
        return None
    return parsed if parsed >= 0 else None


def parse_vinted_relative_age_hours(published_at: object) -> float | None:
    text = normalize_whitespace(str(published_at or "")).lower()
    if not text:
        return None
    if text in {"ora", "adesso"}:
        return 0.0
    if text == "ieri":
        return 24.0
    if text == "oggi":
        return 0.0
    if text in {"un giorno fa", "una giornata fa"}:
        return 24.0
    match = re.match(r"^(\d+)\s+([^\s]+)\s+fa$", text)
    if not match:
        return None
    amount = int(match.group(1))
    unit = match.group(2).strip().lower()
    if unit.startswith(("second", "sec")):
        return amount / 3600.0
    if unit.startswith(("minut", "min")):
        return amount / 60.0
    if unit.startswith(("or", "hour")):
        return float(amount)
    if unit.startswith(("giorn", "day")):
        return float(amount * 24)
    if unit.startswith(("settiman", "week")):
        return float(amount * 24 * 7)
    if unit.startswith(("mes", "month")):
        return float(amount * 24 * 30)
    if unit.startswith(("ann", "year")):
        return float(amount * 24 * 365)
    return None


def is_vinted_deal_hunter_candidate(
    favorite_count: object,
    min_favorites: object = VINTED_DEAL_HUNTER_DEFAULT_MIN_FAVORITES,
) -> bool:
    normalized_min_favorites = normalize_vinted_deal_hunter_min_favorites(
        min_favorites,
        default=VINTED_DEAL_HUNTER_DEFAULT_MIN_FAVORITES,
    )
    if normalized_min_favorites <= 0:
        return False
    parsed_favorite_count = coerce_vinted_favorite_count(favorite_count)
    if parsed_favorite_count is None:
        return False
    return parsed_favorite_count >= normalized_min_favorites


def is_vinted_deal_hunter_match(
    favorite_count: object,
    published_at: object,
    min_favorites: object = VINTED_DEAL_HUNTER_DEFAULT_MIN_FAVORITES,
    max_age_hours: object = VINTED_DEAL_HUNTER_DEFAULT_MAX_AGE_HOURS,
    price_value: object = None,
    max_price: object = None,
    shipping_price_value: object = None,
) -> bool:
    if not is_vinted_deal_hunter_candidate(favorite_count, min_favorites=min_favorites):
        return False
    normalized_max_age_hours = normalize_vinted_deal_hunter_max_age_hours(
        max_age_hours,
        default=VINTED_DEAL_HUNTER_DEFAULT_MAX_AGE_HOURS,
    )
    if normalized_max_age_hours <= 0:
        return False
    age_hours = parse_vinted_relative_age_hours(published_at)
    if age_hours is None:
        return False
    if age_hours > normalized_max_age_hours:
        return False
    normalized_max_price = normalize_vinted_deal_hunter_max_price(max_price)
    numeric_price = _coerce_nonnegative_float(price_value)
    if normalized_max_price is not None:
        if numeric_price is None or numeric_price > normalized_max_price:
            return False
    shipping_value = _coerce_nonnegative_float(shipping_price_value)
    if shipping_value is None:
        return False
    return shipping_value <= VINTED_DEAL_HUNTER_MAX_SHIPPING_PRICE


def annotate_vinted_deal_hunter_row(
    row: dict,
    min_favorites: object = VINTED_DEAL_HUNTER_DEFAULT_MIN_FAVORITES,
    max_age_hours: object = VINTED_DEAL_HUNTER_DEFAULT_MAX_AGE_HOURS,
    max_price: object = None,
) -> dict:
    annotated = dict(row)
    normalized_min_favorites = normalize_vinted_deal_hunter_min_favorites(
        min_favorites,
        default=VINTED_DEAL_HUNTER_DEFAULT_MIN_FAVORITES,
    )
    normalized_max_age_hours = normalize_vinted_deal_hunter_max_age_hours(
        max_age_hours,
        default=VINTED_DEAL_HUNTER_DEFAULT_MAX_AGE_HOURS,
    )
    normalized_max_price = normalize_vinted_deal_hunter_max_price(max_price)
    favorite_count = coerce_vinted_favorite_count(annotated.get("favorite_count"))
    published_at = str(annotated.get("published_at", "") or "").strip()
    age_hours = parse_vinted_relative_age_hours(published_at)
    price_value = _coerce_nonnegative_float(annotated.get("price_value"))
    shipping_price_value = _coerce_nonnegative_float(annotated.get("shipping_price_value"))
    candidate = is_vinted_deal_hunter_candidate(favorite_count, min_favorites=normalized_min_favorites)
    match = is_vinted_deal_hunter_match(
        favorite_count,
        published_at,
        min_favorites=normalized_min_favorites,
        max_age_hours=normalized_max_age_hours,
        price_value=price_value,
        max_price=normalized_max_price,
        shipping_price_value=shipping_price_value,
    )

    if match:
        reason = (
            f"{favorite_count} like, {age_hours:.1f}h, sped {shipping_price_value:.2f}€"
            if favorite_count is not None and age_hours is not None
            else f"{normalized_min_favorites}+ like entro {normalized_max_age_hours:g}h"
        )
        label = "affare 24h/70+"
    elif candidate:
        if age_hours is None:
            reason = f"{favorite_count} like, data non ancora confermata" if favorite_count is not None else ""
        elif shipping_price_value is None:
            reason = (
                f"{favorite_count} like, {age_hours:.1f}h, spedizione non confermata"
                if favorite_count is not None
                else "spedizione non confermata"
            )
        elif shipping_price_value > VINTED_DEAL_HUNTER_MAX_SHIPPING_PRICE:
            reason = (
                f"{favorite_count} like ma spedizione {shipping_price_value:.2f}€"
                if favorite_count is not None
                else f"spedizione {shipping_price_value:.2f}€"
            )
        elif normalized_max_price is not None and (price_value is None or price_value > normalized_max_price):
            if favorite_count is not None and price_value is not None:
                reason = f"{favorite_count} like ma prezzo {price_value:.2f}€"
            elif price_value is not None:
                reason = f"prezzo {price_value:.2f}€"
            else:
                reason = "prezzo non confermato"
        else:
            reason = f"{favorite_count} like ma {age_hours:.1f}h fa" if favorite_count is not None else f"{age_hours:.1f}h fa"
        label = "candidato 70+"
    else:
        reason = ""
        label = ""

    annotated["deal_hunter_candidate"] = candidate
    annotated["deal_hunter_match"] = match
    annotated["deal_hunter_label"] = label
    annotated["deal_hunter_reason"] = reason
    annotated["deal_hunter_age_hours"] = age_hours
    annotated["deal_hunter_min_favorites"] = normalized_min_favorites
    annotated["deal_hunter_max_age_hours"] = normalized_max_age_hours
    annotated["deal_hunter_max_price"] = normalized_max_price
    return annotated


def annotate_vinted_deal_hunter_rows(
    rows: list[dict],
    min_favorites: object = VINTED_DEAL_HUNTER_DEFAULT_MIN_FAVORITES,
    max_age_hours: object = VINTED_DEAL_HUNTER_DEFAULT_MAX_AGE_HOURS,
    max_price: object = None,
) -> list[dict]:
    return [
        annotate_vinted_deal_hunter_row(
            row,
            min_favorites=min_favorites,
            max_age_hours=max_age_hours,
            max_price=max_price,
        )
        for row in rows
    ]


def _coerce_nonnegative_float(value: object) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None
