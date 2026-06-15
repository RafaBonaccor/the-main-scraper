import re
import time

from botasaurus.browser import Driver, Wait, browser

from ..browser_helpers import DEFAULT_COOKIE_REJECT_TEXTS, click_first_matching_text
from ..models import ScrapeOutcome
from ..utils import build_google_maps_search_url, extract_phone_numbers, normalize_whitespace


ACTION_LINES = {
    "Sito web",
    "Indicazioni",
    "Salvato",
    "Condividi",
    "Visita sito",
    "Sponsorizzato",
}

RATING_PATTERN = r"^\d(?:[.,]\d)?\(\d+\)$"


def run_google_maps_scraper(search: str, city: str = "", max_results: int = 25) -> ScrapeOutcome:
    config = {
        "search": search,
        "city": city,
        "max_results": max(int(max_results), 1),
    }
    payload = _scrape_google_maps_task(config)
    return ScrapeOutcome(source="google_maps", rows=payload["rows"], meta=payload["meta"])


@browser
def _scrape_google_maps_task(driver: Driver, config: dict) -> dict:
    search_url = build_google_maps_search_url(config["search"], config.get("city", ""))
    max_results = max(int(config.get("max_results", 25)), 1)

    driver.google_get(search_url, wait=Wait.LONG)
    cookie_banner_action = click_first_matching_text(driver, DEFAULT_COOKIE_REJECT_TEXTS)

    feed = driver.select('[role="feed"]', wait=Wait.VERY_LONG)
    if feed is None:
        driver.google_get(search_url, wait=Wait.LONG)
        feed = driver.select('[role="feed"]', wait=Wait.VERY_LONG)

    rows_by_key: dict[str, dict] = {}
    stalled_scrolls = 0
    last_count = 0

    while feed is not None:
        for article in driver.select_all('[role="article"]', wait=Wait.SHORT):
            row = _article_to_row(article, search_url, config.get("city", ""))
            key = row["link"] or row["phone"] or row["name"] or row["raw_text"]
            if not key:
                continue
            rows_by_key[key] = row

        if len(rows_by_key) >= max_results:
            break

        if not driver.can_scroll_further('[role="feed"]', wait=Wait.SHORT):
            break

        driver.scroll_to_bottom('[role="feed"]', wait=Wait.SHORT)
        time.sleep(1)

        current_count = len(rows_by_key)
        if current_count == last_count:
            stalled_scrolls += 1
            if stalled_scrolls >= 3:
                break
        else:
            stalled_scrolls = 0
            last_count = current_count

    rows = list(rows_by_key.values())[:max_results]
    return {
        "rows": rows,
        "meta": {
            "cookie_banner_action": cookie_banner_action,
            "search": config["search"],
            "city": config.get("city", ""),
            "search_url": search_url,
            "max_results": max_results,
            "row_count": len(rows),
        },
    }


def _article_to_row(article, search_url: str, city: str) -> dict:
    raw_text = article.text.strip()
    lines = _clean_lines(raw_text)
    phones = extract_phone_numbers(raw_text)
    link = _get_element_href(article, "a[href]")

    return {
        "source": "google_maps",
        "search_url": search_url,
        "city": city,
        "name": lines[0] if lines else "",
        "phone": " | ".join(phones),
        "address": _extract_address(lines, phones),
        "link": link,
        "raw_text": raw_text,
    }


def _extract_address(lines: list[str], phones: list[str]) -> str:
    for line in lines[1:]:
        if line in ACTION_LINES:
            continue
        if any(phone in line for phone in phones):
            continue
        if _looks_like_rating(line):
            continue
        if line.startswith(("Aperto", "Chiuso")):
            continue

        parts = [normalize_whitespace(part) for part in line.split("·") if normalize_whitespace(part)]
        if parts:
            candidate = parts[-1]
            if candidate not in ACTION_LINES and not _looks_like_rating(candidate) and len(candidate) > 6:
                return candidate

        if len(line) > 8 and line not in ACTION_LINES and not _looks_like_rating(line):
            return line

    return ""


def _clean_lines(raw_text: str) -> list[str]:
    cleaned_lines: list[str] = []

    for line in raw_text.splitlines():
        normalized = normalize_whitespace(line)
        if not normalized:
            continue
        if normalized in ACTION_LINES:
            continue
        if len(normalized) <= 2 and not any(char.isalnum() for char in normalized):
            continue
        cleaned_lines.append(normalized)

    return cleaned_lines


def _looks_like_rating(value: str) -> bool:
    return bool(re.match(RATING_PATTERN, value))


def _get_element_href(element, selector: str) -> str:
    try:
        child = element.select(selector, wait=Wait.SHORT)
    except Exception:
        return ""

    if child is None:
        return ""

    try:
        return child.href or ""
    except Exception:
        return ""
