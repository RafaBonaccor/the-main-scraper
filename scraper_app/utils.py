import re
import unicodedata
from pathlib import Path
from urllib.parse import quote, urlencode


PHONE_PATTERN = re.compile(r"\+?\d[\d\s().-]{7,}\d")
PRICE_PATTERN = re.compile(r"(?:\d[\d.\s,]*(?:\s?(?:€|â‚¬))|(?:€|â‚¬)\s?\d[\d.\s,]*|Gratis)", re.IGNORECASE)


def normalize_whitespace(value: str) -> str:
    return " ".join(_repair_mojibake(value or "").split())


def _repair_mojibake(value: str) -> str:
    text = value or ""
    if not any(marker in text for marker in ("Ã", "Â", "â‚¬", "â€™", "â€œ", "â€")):
        return text

    try:
        text = text.encode("latin1").decode("utf-8")
    except UnicodeError:
        pass

    return (
        text.replace("â‚¬", "€")
        .replace("Â·", "·")
        .replace("Â", "")
        .replace("â€™", "'")
        .replace("â€œ", '"')
        .replace("â€\x9d", '"')
    )


def split_nonempty_lines(text: str) -> list[str]:
    lines: list[str] = []
    for raw_line in (text or "").splitlines():
        cleaned = normalize_whitespace(raw_line)
        if cleaned:
            lines.append(cleaned)
    return lines


def extract_phone_numbers(text: str) -> list[str]:
    found_numbers: list[str] = []
    seen_numbers: set[str] = set()

    for line in split_nonempty_lines(text):
        parts = re.split(r"[·|]", line)

        for part in parts:
            candidate = normalize_whitespace(part)
            for match in PHONE_PATTERN.finditer(candidate):
                phone_number = match.group().strip()
                digits_only = re.sub(r"\D", "", phone_number)

                if len(digits_only) < 10 or len(digits_only) > 15:
                    continue
                if phone_number in seen_numbers:
                    continue

                seen_numbers.add(phone_number)
                found_numbers.append(phone_number)

    return found_numbers


def extract_first_price(text: str) -> str:
    for line in split_nonempty_lines(text):
        match = PRICE_PATTERN.search(line)
        if match:
            return normalize_whitespace(match.group())
    return ""


def strip_leading_counter(value: str) -> str:
    return re.sub(r"^\d+\s+", "", (value or "").strip())


def slugify_filename(value: str) -> str:
    normalized = re.sub(r"[^\w\-]+", "_", (value or "").strip(), flags=re.ASCII)
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    return normalized or "scrape"


def slugify_path_segment(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", (value or "").strip())
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii").lower()
    ascii_value = re.sub(r"[^a-z0-9]+", "-", ascii_value).strip("-")
    return ascii_value


def parse_text_list(raw_value: str) -> list[str]:
    return [item.strip() for item in (raw_value or "").split(",") if item.strip()]


def build_search_phrase(*parts: str) -> str:
    return " ".join(part.strip() for part in parts if part and part.strip())


def build_google_maps_search_url(search_value: str, city: str = "", province: str = "", country: str = "") -> str:
    candidate = (search_value or "").strip()
    if candidate.startswith(("http://", "https://")):
        return candidate

    candidate = build_search_phrase(candidate, city, province, country)
    return f"https://www.google.com/maps/search/{quote(candidate)}"


def build_subito_search_url(
    query_value: str = "",
    region: str = "italia",
    category: str = "offerte-lavoro",
    city: str = "",
) -> str:
    candidate = (query_value or "").strip()
    if candidate.startswith(("http://", "https://")):
        return candidate

    region_slug = slugify_path_segment(region) or "italia"
    category_slug = slugify_path_segment(category) or "offerte-lavoro"
    city_slug = slugify_path_segment(city)

    base_url = f"https://www.subito.it/annunci-{region_slug}/vendita/{category_slug}/"
    if city_slug:
        base_url = f"{base_url}{city_slug}/"

    if not candidate:
        return base_url

    return f"{base_url}?{urlencode({'q': candidate})}"


def first_nonempty_line(text: str) -> str:
    lines = split_nonempty_lines(text)
    return lines[0] if lines else ""


def as_uri_if_path(value: str) -> str:
    candidate = Path(value)
    if candidate.exists():
        return candidate.resolve().as_uri()
    return value
