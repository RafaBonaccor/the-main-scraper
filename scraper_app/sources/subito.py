import re

from botasaurus.browser import Driver, Wait, browser

from ..browser_runtime import normalize_browser_mode, resolve_browser_arguments, resolve_browser_profile
from ..browser_helpers import click_first_matching_text, navigate_with_retries
from ..models import ScrapeOutcome
from ..utils import (
    build_subito_search_url,
    extract_first_price,
    normalize_whitespace,
    split_nonempty_lines,
    strip_leading_counter,
)


SUBITO_COOKIE_REJECT_TEXTS = (
    "Continua senza accettare",
)

SUBITO_PROMO_LABELS = {
    "vetrina",
    "top",
    "urgente",
}

SUBITO_DATE_PATTERN = re.compile(r"^(oggi|ieri|\d{1,2}/\d{1,2}(?:/\d{2,4})?)(?:\s+alle\s+.+)?$", re.IGNORECASE)
SUBITO_LOCATION_PATTERN = re.compile(r".+\([A-Z]{2}\)$")
SUBITO_SCHEDULE_LABELS = {
    "full time",
    "part time",
    "turni",
    "stage",
    "contratto a tempo determinato",
    "contratto a tempo indeterminato",
}
SUBITO_META_MARKERS = {
    "mostra numero",
    "azienda",
    "privato",
}
SUBITO_PERSISTENT_PROFILE_NAME = "Subito"


def run_subito_scraper(
    query: str = "",
    region: str = "lazio",
    city: str = "roma",
    category: str = "offerte-lavoro",
    max_results: int = 25,
    browser_mode: str = "isolated",
    browser_user_data_dir: str = "",
    browser_profile_directory: str = "Default",
) -> ScrapeOutcome:
    browser_profile_directory = _normalize_subito_profile_directory(browser_mode, browser_profile_directory)
    config = {
        "query": query,
        "region": region,
        "city": city,
        "category": category,
        "max_results": max(int(max_results), 1),
        "browser_mode": browser_mode,
        "browser_user_data_dir": browser_user_data_dir,
        "browser_profile_directory": browser_profile_directory,
    }
    payload = _scrape_subito_task(config)
    return ScrapeOutcome(source="subito", rows=payload["rows"], meta=payload["meta"])


def _normalize_subito_profile_directory(browser_mode: str, browser_profile_directory: str) -> str:
    normalized_mode = normalize_browser_mode(browser_mode)
    clean_value = str(browser_profile_directory or "").strip()
    if normalized_mode == "sessione_persistente" and clean_value in {"", "Default"}:
        return SUBITO_PERSISTENT_PROFILE_NAME
    return clean_value or "Default"


@browser(profile=resolve_browser_profile, add_arguments=resolve_browser_arguments)
def _scrape_subito_task(driver: Driver, config: dict) -> dict:
    search_url = build_subito_search_url(
        query_value=config.get("query", ""),
        region=config.get("region", ""),
        category=config.get("category", ""),
        city=config.get("city", ""),
    )
    max_results = max(int(config.get("max_results", 25)), 1)

    navigate_with_retries(driver, search_url, wait=Wait.LONG)
    cookie_banner_action = click_first_matching_text(driver, SUBITO_COOKIE_REJECT_TEXTS)
    driver.select_all("article", wait=Wait.VERY_LONG)

    extracted_rows = driver.run_js(
        """
const cleanText = (value) => (value || "").replace(/\\u00a0/g, " ").replace(/\\r/g, "").trim();
const getText = (root, selectors) => {
  for (const selector of selectors) {
    const element = root.querySelector(selector);
    const text = cleanText(element?.innerText || element?.textContent || "");
    if (text) {
      return text;
    }
  }
  return "";
};
const getHref = (root) => {
  const link = root.querySelector("a[href]");
  return link ? (link.href || link.getAttribute("href") || "") : "";
};

return [...document.querySelectorAll("article")].map((article) => {
  const rawText = cleanText(article.innerText || article.textContent || "");
  if (!rawText) {
    return null;
  }

  return {
    raw_text: rawText,
    title: getText(article, ["h2", "h3", "a[aria-label]"]),
    price_text: getText(article, ["[data-testid*='price']", "[class*='price']"]),
    location_text: getText(article, ["[data-testid*='location']", "[data-testid*='town']", "[class*='town']", "[class*='city']"]),
    badge_text: getText(article, ["[class*='badge']", "[class*='tag']", "[data-testid*='feature']"]),
    link: getHref(article),
  };
}).filter(Boolean);
        """,
    )

    rows_by_key: dict[str, dict] = {}
    for raw_row in extracted_rows or []:
        row = _to_subito_row(
            raw_row,
            search_url=search_url,
            query=config.get("query", ""),
            region=config.get("region", ""),
            city=config.get("city", ""),
            category=config.get("category", ""),
        )
        key = row["link"] or row["title"] or row["raw_text"]
        if not key:
            continue
        rows_by_key[key] = row
        if len(rows_by_key) >= max_results:
            break

    rows = list(rows_by_key.values())[:max_results]
    return {
        "rows": rows,
        "meta": {
            "cookie_banner_action": cookie_banner_action,
            "query": config["query"],
            "region": config.get("region", ""),
            "city": config.get("city", ""),
            "category": config.get("category", ""),
            "search_url": search_url,
            "max_results": max_results,
            "row_count": len(rows),
        },
    }


def _to_subito_row(
    raw_row: dict,
    search_url: str,
    query: str,
    region: str,
    city: str,
    category: str,
) -> dict:
    raw_text = (raw_row.get("raw_text") or "").strip()
    lines = split_nonempty_lines(raw_text)

    listing_type = _extract_listing_type(lines, raw_row.get("badge_text", ""))
    selected_title = _clean_title(raw_row.get("title", ""))
    title = selected_title or _infer_title(lines)
    price = _extract_price(raw_row.get("price_text", ""), raw_text)
    location = _extract_location(raw_row.get("location_text", ""), lines, title, price)
    published_at = _extract_published_at(lines)
    details = _extract_job_details(lines, title, location, price)

    return {
        "source": "subito",
        "search_url": search_url,
        "query": query,
        "region": region,
        "city": city,
        "category": category,
        "listing_type": listing_type,
        "title": title,
        "price": price,
        "location": location,
        "published_at": published_at,
        "sector": details["sector"],
        "role_type": details["role_type"],
        "schedule": details["schedule"],
        "company": details["company"],
        "link": raw_row.get("link") or "",
        "raw_text": raw_text,
    }


def _clean_title(value: str) -> str:
    title = strip_leading_counter(normalize_whitespace(value))
    lowered = title.lower()
    for label in SUBITO_PROMO_LABELS:
        prefix = f"{label} "
        if lowered.startswith(prefix):
            return title[len(prefix) :].strip()
    return title


def _extract_listing_type(lines: list[str], badge_text: str) -> str:
    candidate = normalize_whitespace(badge_text).lower()
    if candidate in SUBITO_PROMO_LABELS:
        return candidate

    for line in lines[:2]:
        cleaned = strip_leading_counter(normalize_whitespace(line)).lower()
        if cleaned in SUBITO_PROMO_LABELS:
            return cleaned

    return ""


def _infer_title(lines: list[str]) -> str:
    for line in lines:
        cleaned = _clean_title(line)
        lowered = cleaned.lower()
        if not cleaned:
            continue
        if lowered in SUBITO_PROMO_LABELS:
            continue
        if SUBITO_DATE_PATTERN.match(cleaned):
            continue
        return cleaned
    return ""


def _extract_price(selected_price: str, raw_text: str) -> str:
    selected = normalize_whitespace(selected_price)
    selected_extracted = extract_first_price(selected)
    if selected_extracted:
        return selected_extracted
    return extract_first_price(raw_text)


def _extract_location(selected_location: str, lines: list[str], title: str, price: str) -> str:
    selected = strip_leading_counter(normalize_whitespace(selected_location))
    if _looks_like_location(selected):
        return selected

    for line in reversed(lines):
        cleaned = strip_leading_counter(normalize_whitespace(line))
        lowered = cleaned.lower()
        if not cleaned or cleaned == title or cleaned == price:
            continue
        if lowered in SUBITO_PROMO_LABELS:
            continue
        if lowered in SUBITO_META_MARKERS:
            continue
        if SUBITO_DATE_PATTERN.match(cleaned):
            continue
        if _looks_like_location(cleaned):
            return cleaned

    return ""


def _extract_published_at(lines: list[str]) -> str:
    for line in lines:
        cleaned = strip_leading_counter(normalize_whitespace(line))
        if SUBITO_DATE_PATTERN.match(cleaned):
            return cleaned
    return ""


def _extract_job_details(lines: list[str], title: str, location: str, price: str) -> dict[str, str]:
    sector = ""
    role_type = ""
    schedule = ""
    company = ""

    cleaned_lines = [strip_leading_counter(normalize_whitespace(line)) for line in lines]

    if "Azienda" in cleaned_lines:
        index = cleaned_lines.index("Azienda")
        if index + 1 < len(cleaned_lines):
            candidate = cleaned_lines[index + 1]
            if candidate.lower() not in SUBITO_META_MARKERS:
                company = candidate

    filtered = []
    for line in cleaned_lines:
        lowered = line.lower()
        if not line or line == title or line == location or line == price:
            continue
        if lowered in SUBITO_PROMO_LABELS or lowered in SUBITO_META_MARKERS:
            continue
        if SUBITO_DATE_PATTERN.match(line):
            continue
        if _looks_like_location(line) or _looks_like_price(line):
            continue
        if line == company:
            continue
        filtered.append(line)

    for line in filtered:
        lowered = line.lower()
        if not sector:
            sector = line
            continue
        if not schedule and lowered in SUBITO_SCHEDULE_LABELS:
            schedule = line
            continue
        if not role_type:
            role_type = line

    return {
        "sector": sector,
        "role_type": role_type,
        "schedule": schedule,
        "company": company,
    }


def _looks_like_location(value: str) -> bool:
    return bool(SUBITO_LOCATION_PATTERN.match(value))


def _looks_like_price(value: str) -> bool:
    return "€" in value or value.lower() == "gratis"
