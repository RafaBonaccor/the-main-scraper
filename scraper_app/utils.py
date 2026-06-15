import re
from pathlib import Path
from urllib.parse import quote


PHONE_PATTERN = re.compile(r"\+?\d[\d\s().-]{7,}\d")


def normalize_whitespace(value: str) -> str:
    return " ".join((value or "").split())


def extract_phone_numbers(text: str) -> list[str]:
    found_numbers: list[str] = []
    seen_numbers: set[str] = set()

    for line in (text or "").splitlines():
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


def slugify_filename(value: str) -> str:
    normalized = re.sub(r"[^\w\-]+", "_", (value or "").strip(), flags=re.ASCII)
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    return normalized or "scrape"


def parse_text_list(raw_value: str) -> list[str]:
    return [item.strip() for item in (raw_value or "").split(",") if item.strip()]


def build_google_maps_search_url(search_value: str, city: str = "") -> str:
    candidate = (search_value or "").strip()
    if candidate.startswith(("http://", "https://")):
        return candidate

    city_value = (city or "").strip()
    if city_value:
        candidate = f"{candidate} {city_value}"

    return f"https://www.google.com/maps/search/{quote(candidate)}"


def first_nonempty_line(text: str) -> str:
    for line in (text or "").splitlines():
        cleaned = normalize_whitespace(line)
        if cleaned:
            return cleaned
    return ""


def as_uri_if_path(value: str) -> str:
    candidate = Path(value)
    if candidate.exists():
        return candidate.resolve().as_uri()
    return value
