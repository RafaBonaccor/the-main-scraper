import re

from botasaurus.browser import Driver, Wait, browser

from ..browser_runtime import normalize_browser_mode, resolve_browser_arguments, resolve_browser_profile
from ..browser_helpers import click_first_matching_text, navigate_with_retries
from ..date_filter import listing_has_time, parse_listing_date
from ..models import ScrapeOutcome
from ..runtime_controls import consume_skip_current_item_request, consume_stop_after_current_item_request
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
    include_details: bool = False,
    max_results: int = 25,
    slow_mode: bool = False,
    action_delay_seconds: float = 1.5,
    page_settle_seconds: float = 3.0,
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
        "include_details": bool(include_details),
        "max_results": max(int(max_results), 1),
        "slow_mode": bool(slow_mode),
        "action_delay_seconds": float(action_delay_seconds),
        "page_settle_seconds": float(page_settle_seconds),
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
    include_details = bool(config.get("include_details", False))
    slow_mode = bool(config.get("slow_mode", False))
    action_delay_seconds = _normalized_delay_seconds(config.get("action_delay_seconds", 1.5), default=1.5 if slow_mode else 0.0)
    page_settle_seconds = _normalized_delay_seconds(config.get("page_settle_seconds", 3.0), default=3.0 if slow_mode else 0.0)

    navigate_with_retries(driver, search_url, wait=Wait.LONG)
    _sleep_if_needed(driver, page_settle_seconds)
    cookie_banner_action = click_first_matching_text(driver, SUBITO_COOKIE_REJECT_TEXTS)
    _sleep_if_needed(driver, action_delay_seconds)
    driver.select_all("article", wait=Wait.VERY_LONG)
    _sleep_if_needed(driver, action_delay_seconds)

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
    details_loaded = 0
    detail_pages_visited = 0
    if include_details and rows:
        details_loaded, detail_pages_visited = _enrich_rows_with_detail_pages(
            driver,
            rows,
            action_delay_seconds=action_delay_seconds,
            page_settle_seconds=page_settle_seconds,
        )
    return {
        "rows": rows,
        "meta": {
            "cookie_banner_action": cookie_banner_action,
            "query": config["query"],
            "region": config.get("region", ""),
            "city": config.get("city", ""),
            "category": config.get("category", ""),
            "search_url": search_url,
            "include_details": include_details,
            "slow_mode": slow_mode,
            "action_delay_seconds": action_delay_seconds,
            "page_settle_seconds": page_settle_seconds,
            "details_loaded": details_loaded,
            "detail_pages_visited": detail_pages_visited,
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
        if _looks_like_published_at(cleaned):
            return cleaned
    return ""


def _looks_like_published_at(value: str) -> bool:
    cleaned = strip_leading_counter(normalize_whitespace(value))
    lowered = cleaned.lower()
    if not cleaned:
        return False
    if SUBITO_DATE_PATTERN.match(cleaned):
        return True
    if parse_listing_date(cleaned) is not None:
        return True
    if listing_has_time(cleaned) and any(token in lowered for token in ("oggi", "ieri", "fa")):
        return True
    return False


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


def _enrich_rows_with_detail_pages(
    driver: Driver,
    rows: list[dict],
    action_delay_seconds: float = 0.0,
    page_settle_seconds: float = 0.0,
) -> tuple[int, int]:
    details_loaded = 0
    detail_pages_visited = 0

    for row in rows:
        if consume_stop_after_current_item_request():
            break
        link = str(row.get("link", "") or "").strip()
        if not link:
            continue
        if consume_skip_current_item_request():
            continue

        detail_pages_visited += 1
        try:
            navigate_with_retries(driver, link, wait=Wait.LONG)
            _sleep_if_needed(driver, page_settle_seconds)
            if consume_skip_current_item_request():
                continue
            click_first_matching_text(driver, SUBITO_COOKIE_REJECT_TEXTS)
            _sleep_if_needed(driver, action_delay_seconds)
            driver.select("body", wait=Wait.LONG)
            _sleep_if_needed(driver, action_delay_seconds)
            if consume_skip_current_item_request():
                continue
            detail_payload = driver.run_js(
                r"""
const cleanText = (value) => (value || "").replace(/\\u00a0/g, " ").replace(/\\r/g, "").trim();
const textOf = (element) => cleanText(element?.innerText || element?.textContent || "");
const longestText = (values) => {
  const candidates = values
    .map((value) => cleanText(value))
    .filter((value) => value && !/^descrizione$/i.test(value) && value.length > 40);
  candidates.sort((a, b) => b.length - a.length);
  return candidates[0] || "";
};
const dateRegex = /(?:oggi|ieri|\d{1,2}\/\d{1,2}(?:\/\d{2,4})?|\d{1,2}\s+(?:gen(?:naio)?|feb(?:braio)?|mar(?:zo)?|apr(?:ile)?|mag(?:gio)?|giu(?:gno)?|lug(?:lio)?|ago(?:sto)?|set(?:t(?:embre)?)?|ott(?:obre)?|nov(?:embre)?|dic(?:embre)?))(?:\s+alle\s+\d{1,2}:\d{2})?|\b\d{1,2}:\d{2}\b|\b(?:\d+\s*ore?\s*fa|un[' ]?ora\s*fa|\d+\s*min(?:uti)?\s*fa|un\s*minuto\s*fa|pochi\s*minuti\s*fa)\b/i;
const uniqueTexts = (values) => {
  const results = [];
  const seen = new Set();
  for (const value of values) {
    const cleaned = cleanText(value);
    if (!cleaned || seen.has(cleaned)) {
      continue;
    }
    seen.add(cleaned);
    results.push(cleaned);
  }
  return results;
};

let description = longestText(
  [...document.querySelectorAll("[data-testid*='description'], [class*='description']")]
    .map((element) => textOf(element))
);

const heading = [...document.querySelectorAll("h2, h3, h4, h5, h6, [class*='headline']")]
  .find((element) => /^descrizione$/i.test(textOf(element)));

if (!description && heading) {
  const container = heading.closest("section, article, div");
  if (container) {
    description = longestText(
      [...container.querySelectorAll("p, li, div")]
        .map((element) => textOf(element))
    );
  }
}

if (!description && heading) {
  let sibling = heading.nextElementSibling;
  const siblingTexts = [];
  while (sibling && siblingTexts.length < 8) {
    const text = textOf(sibling);
    if (text && !/^descrizione$/i.test(text)) {
      siblingTexts.push(text);
    }
    sibling = sibling.nextElementSibling;
  }
  description = cleanText(siblingTexts.join("\\n"));
}

const dateCandidates = uniqueTexts(
  [...document.querySelectorAll("time, [datetime], [data-testid*='date'], [class*='date'], [class*='time'], [aria-label*='Pubblic'], [class*='insertion-date']")]
    .map((element) => textOf(element))
).filter((value) => value.length <= 120);

let publishedAt = dateCandidates.find((value) => dateRegex.test(value)) || "";

if (!publishedAt) {
  const bodyTexts = uniqueTexts(
    [...document.querySelectorAll("body *")]
      .map((element) => textOf(element))
  ).filter((value) => value.length > 0 && value.length <= 120);
  publishedAt = bodyTexts.find((value) => dateRegex.test(value)) || "";
}

return {
  description,
  published_at: publishedAt,
};
                """,
            ) or {}
        except Exception:
            continue

        description = normalize_whitespace(str(detail_payload.get("description", "") or ""))
        if description:
            row["description"] = description
            details_loaded += 1

        published_at = normalize_whitespace(str(detail_payload.get("published_at", "") or ""))
        if published_at and not str(row.get("published_at", "") or "").strip():
            row["published_at"] = published_at

        _sleep_if_needed(driver, action_delay_seconds)

    return details_loaded, detail_pages_visited


def _normalized_delay_seconds(value: float | int | str, default: float = 0.0) -> float:
    try:
        return max(float(value), 0.0)
    except (TypeError, ValueError):
        return max(default, 0.0)


def _sleep_if_needed(driver: Driver, seconds: float) -> None:
    if seconds > 0:
        driver.sleep(seconds)
