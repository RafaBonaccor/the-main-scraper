from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from .models import ScrapeOutcome


ITALIAN_MONTHS = {
    "gen": 1,
    "gennaio": 1,
    "feb": 2,
    "febbraio": 2,
    "mar": 3,
    "marzo": 3,
    "apr": 4,
    "aprile": 4,
    "mag": 5,
    "maggio": 5,
    "giu": 6,
    "giugno": 6,
    "lug": 7,
    "luglio": 7,
    "ago": 8,
    "agosto": 8,
    "set": 9,
    "sett": 9,
    "settembre": 9,
    "ott": 10,
    "ottobre": 10,
    "nov": 11,
    "novembre": 11,
    "dic": 12,
    "dicembre": 12,
}
SLASH_DATE_PATTERN = re.compile(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b")
TIME_PATTERN = re.compile(r"\b(?:alle\s*)?(\d{1,2}):(\d{2})\b", re.IGNORECASE)
RELATIVE_HOURS_PATTERN = re.compile(r"\b(?:(\d+)\s*ore?\s*fa|un[' ]?ora\s*fa)\b", re.IGNORECASE)
RELATIVE_MINUTES_PATTERN = re.compile(
    r"\b(?:(\d+)\s*min(?:uti)?\s*fa|un\s*minuto\s*fa|pochi\s*minuti\s*fa)\b",
    re.IGNORECASE,
)
MONTH_NAME_PATTERN = re.compile(
    r"\b(\d{1,2})\s+"
    r"(gen(?:naio)?|feb(?:braio)?|mar(?:zo)?|apr(?:ile)?|mag(?:gio)?|giu(?:gno)?|lug(?:lio)?|ago(?:sto)?|set(?:t(?:embre)?)?|ott(?:obre)?|nov(?:embre)?|dic(?:embre)?)"
    r"(?:\s+(\d{2,4}))?\b",
    re.IGNORECASE,
)


def apply_age_filter_to_outcome(
    outcome: ScrapeOutcome,
    *,
    max_age_hours: int = 0,
    max_age_days: int = 0,
    exact_age_days: int | None = None,
    keep_unknown_dates: bool = True,
    now: datetime | None = None,
) -> ScrapeOutcome:
    current_moment = now or datetime.now()
    current_day = current_moment.date()
    normalized_max_age_hours = max(int(max_age_hours or 0), 0)
    normalized_max_age = max(int(max_age_days or 0), 0)
    normalized_exact_age = None if exact_age_days is None else max(int(exact_age_days), 0)
    filter_mode = (
        "max_age_hours"
        if normalized_max_age_hours > 0
        else "exact_day"
        if normalized_exact_age is not None
        else "max_age"
        if normalized_max_age > 0
        else "disabled"
    )
    filtered_rows: list[dict] = []
    counts = {"fresh": 0, "stale": 0, "unknown": 0}
    removed_count = 0

    for row in outcome.rows:
        annotated = dict(row)
        raw_published_at = str(row.get("published_at", "") or "")
        parsed_date = parse_listing_date(raw_published_at, today=current_day)
        parsed_datetime = parse_listing_datetime(raw_published_at, now=current_moment)
        has_time = listing_has_time(raw_published_at)
        age_days = None
        age_hours = None
        decision = "unknown"
        reason = "Data annuncio non riconosciuta."

        if parsed_date is not None:
            age_days = max((current_day - parsed_date).days, 0)
            if normalized_max_age_hours > 0:
                if parsed_datetime is None or not has_time:
                    decision = "unknown"
                    reason = "Orario annuncio non disponibile per il filtro in ore."
                else:
                    age_hours = max((current_moment - parsed_datetime).total_seconds() / 3600, 0.0)
                    if age_hours > normalized_max_age_hours:
                        decision = "stale"
                        reason = f"Annuncio troppo vecchio: {age_hours:.1f} ore."
                    else:
                        decision = "fresh"
                        reason = f"Annuncio entro {normalized_max_age_hours} ore."
            elif normalized_exact_age is not None:
                if age_days == normalized_exact_age:
                    decision = "fresh"
                    reason = f"Annuncio del giorno selezionato: {describe_age_days(normalized_exact_age)}."
                else:
                    decision = "stale"
                    reason = (
                        f"Annuncio di {describe_age_days(age_days)}, "
                        f"non coincide con {describe_age_days(normalized_exact_age)}."
                    )
            elif normalized_max_age > 0 and age_days > normalized_max_age:
                decision = "stale"
                reason = f"Annuncio troppo vecchio: {age_days} giorni."
            else:
                decision = "fresh"
                if normalized_max_age > 0:
                    reason = f"Annuncio entro {normalized_max_age} giorni."
                else:
                    reason = "Data annuncio valida."

        annotated.update(
            {
                "published_date_iso": parsed_date.isoformat() if parsed_date else "",
                "published_datetime_iso": parsed_datetime.isoformat(timespec="minutes") if parsed_datetime else "",
                "age_days": age_days,
                "age_hours": round(age_hours, 2) if age_hours is not None else None,
                "age_filter_decision": decision,
                "age_filter_reason": reason,
            }
        )
        counts[decision] += 1

        if decision == "stale" and (normalized_max_age_hours > 0 or normalized_max_age > 0 or normalized_exact_age is not None):
            removed_count += 1
            continue
        if decision == "unknown" and not keep_unknown_dates:
            removed_count += 1
            continue
        filtered_rows.append(annotated)

    meta = dict(outcome.meta)
    meta.update(
        {
            "age_filter_enabled": filter_mode != "disabled",
            "age_filter_mode": filter_mode,
            "age_filter_max_hours": normalized_max_age_hours,
            "age_filter_max_days": normalized_max_age,
            "age_filter_exact_days": normalized_exact_age,
            "age_filter_exact_label": describe_age_days(normalized_exact_age) if normalized_exact_age is not None else "",
            "age_filter_keep_unknown_dates": keep_unknown_dates,
            "age_filter_removed_count": removed_count,
            "age_filter_counts": counts,
            "row_count": len(filtered_rows),
        }
    )
    return ScrapeOutcome(source=outcome.source, rows=filtered_rows, meta=meta)


def parse_listing_date(value: str, today: date | None = None) -> date | None:
    current_day = today or date.today()
    raw_value = str(value or "").strip().lower()
    if not raw_value:
        return None

    normalized = raw_value.replace("\u00a0", " ").replace(",", " ")
    normalized = normalized.split("alle", 1)[0].strip()

    if _parse_relative_hours(raw_value) is not None or _parse_relative_minutes(raw_value) is not None:
        return current_day
    if "oggi" in normalized:
        return current_day
    if "ieri" in normalized:
        return current_day - timedelta(days=1)

    time_match = TIME_PATTERN.search(raw_value)
    if time_match and not SLASH_DATE_PATTERN.search(normalized) and not MONTH_NAME_PATTERN.search(normalized):
        return current_day

    slash_match = SLASH_DATE_PATTERN.search(normalized)
    if slash_match:
        return _build_date_from_parts(
            day=int(slash_match.group(1)),
            month=int(slash_match.group(2)),
            year_text=slash_match.group(3),
            today=current_day,
        )

    month_match = MONTH_NAME_PATTERN.search(normalized)
    if month_match:
        month_value = ITALIAN_MONTHS.get(month_match.group(2).lower())
        if month_value is None:
            return None
        return _build_date_from_parts(
            day=int(month_match.group(1)),
            month=month_value,
            year_text=month_match.group(3),
            today=current_day,
        )

    return None


def _build_date_from_parts(day: int, month: int, year_text: str | None, today: date) -> date | None:
    if year_text:
        year_value = int(year_text)
        if year_value < 100:
            year_value += 2000
    else:
        year_value = today.year

    try:
        parsed = date(year_value, month, day)
    except ValueError:
        return None

    if not year_text and parsed > today + timedelta(days=1):
        try:
            parsed = date(today.year - 1, month, day)
        except ValueError:
            return None

    return parsed


def parse_listing_datetime(value: str, now: datetime | None = None) -> datetime | None:
    current_moment = now or datetime.now()
    relative_hours = _parse_relative_hours(value)
    if relative_hours is not None:
        return current_moment - timedelta(hours=relative_hours)

    relative_minutes = _parse_relative_minutes(value)
    if relative_minutes is not None:
        return current_moment - timedelta(minutes=relative_minutes)

    parsed_date = parse_listing_date(value, today=current_moment.date())
    if parsed_date is None:
        return None

    time_match = TIME_PATTERN.search(str(value or "").strip().lower())
    if not time_match:
        return datetime.combine(parsed_date, datetime.min.time())

    try:
        hour = int(time_match.group(1))
        minute = int(time_match.group(2))
        parsed_datetime = datetime(parsed_date.year, parsed_date.month, parsed_date.day, hour, minute)
    except ValueError:
        return None

    if parsed_datetime > current_moment + timedelta(minutes=5):
        parsed_datetime = parsed_datetime - timedelta(days=1)
    return parsed_datetime


def to_datetime_for_sorting(value: str) -> datetime | None:
    return parse_listing_datetime(value)


def listing_has_time(value: str) -> bool:
    raw_value = str(value or "").strip().lower()
    return bool(
        TIME_PATTERN.search(raw_value)
        or RELATIVE_HOURS_PATTERN.search(raw_value)
        or RELATIVE_MINUTES_PATTERN.search(raw_value)
    )


def _parse_relative_hours(value: str) -> float | None:
    raw_value = str(value or "").strip().lower()
    match = RELATIVE_HOURS_PATTERN.search(raw_value)
    if not match:
        return None
    if match.group(1):
        return max(float(int(match.group(1))), 0.0)
    return 1.0


def _parse_relative_minutes(value: str) -> float | None:
    raw_value = str(value or "").strip().lower()
    match = RELATIVE_MINUTES_PATTERN.search(raw_value)
    if not match:
        return None
    if match.group(1):
        return max(float(int(match.group(1))), 0.0)
    if "pochi" in raw_value:
        return 5.0
    return 1.0


def describe_age_days(value: int | None) -> str:
    if value is None:
        return ""
    normalized_value = max(int(value), 0)
    if normalized_value == 0:
        return "oggi"
    if normalized_value == 1:
        return "ieri"
    return f"{normalized_value} giorni fa"
