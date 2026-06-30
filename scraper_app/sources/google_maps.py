import re
import time
import unicodedata
from datetime import datetime

from botasaurus.browser import Driver, Wait, browser

from ..browser_helpers import (
    DEFAULT_COOKIE_REJECT_TEXTS,
    click_first_matching_text,
    current_page_url,
    navigate_with_retries,
)
from ..browser_runtime import resolve_browser_arguments, resolve_browser_profile
from ..models import ScrapeOutcome
from ..runtime_controls import consume_skip_current_item_request, consume_stop_after_current_item_request
from ..utils import build_google_maps_search_url, extract_phone_numbers, normalize_whitespace
from ..website_audit import annotate_lead_opportunity, audit_business_website


ACTION_LINES = {
    "Sito web",
    "Indicazioni",
    "Salvato",
    "Condividi",
    "Visita sito",
    "Sponsorizzato",
}

RATING_PATTERN = re.compile(r"^(\d(?:[.,]\d)?)(?:\s*\(([\d.,]+)\))?$")


def run_google_maps_scraper(
    search: str,
    city: str = "",
    province: str = "",
    country: str = "",
    max_results: int = 25,
    exclude_sponsored: bool = True,
    include_details: bool = True,
    audit_websites: bool = True,
    website_timeout_seconds: float = 10.0,
    slow_mode: bool = False,
    action_delay_seconds: float = 1.5,
    page_settle_seconds: float = 3.0,
    browser_mode: str = "isolated",
    browser_user_data_dir: str = "",
    browser_profile_directory: str = "Default",
) -> ScrapeOutcome:
    config = {
        "search": search,
        "city": city,
        "province": province,
        "country": country,
        "max_results": max(int(max_results), 1),
        "exclude_sponsored": bool(exclude_sponsored),
        "include_details": bool(include_details),
        "slow_mode": bool(slow_mode),
        "action_delay_seconds": max(float(action_delay_seconds), 0.0),
        "page_settle_seconds": max(float(page_settle_seconds), 0.0),
        "browser_mode": browser_mode,
        "browser_user_data_dir": browser_user_data_dir,
        "browser_profile_directory": browser_profile_directory,
    }
    payload = _scrape_google_maps_task(config)
    rows = payload["rows"]

    audit_cache: dict[str, dict] = {}
    audit_errors: list[dict[str, str]] = []
    for index, row in enumerate(rows, start=1):
        if consume_skip_current_item_request():
            row["audit_skipped"] = True
            rows[index - 1] = annotate_lead_opportunity(row)
            if consume_stop_after_current_item_request():
                rows = rows[:index]
                break
            continue
        website = str(row.get("website", "") or "").strip()
        if not audit_websites:
            rows[index - 1] = annotate_lead_opportunity(row)
            if consume_stop_after_current_item_request():
                rows = rows[:index]
                break
            continue
        if not website:
            rows[index - 1] = annotate_lead_opportunity(row)
            if consume_stop_after_current_item_request():
                rows = rows[:index]
                break
            continue

        cache_key = website.rstrip("/").lower()
        if cache_key not in audit_cache:
            print(f"Audit sito {index}/{len(rows)}: {website}", flush=True)
            try:
                audit_cache[cache_key] = audit_business_website(
                    website,
                    timeout_seconds=max(float(website_timeout_seconds), 1.0),
                    max_pages=2,
                )
            except Exception as exc:
                audit_errors.append({"website": website, "error": f"{type(exc).__name__}: {exc}"})
                audit_cache[cache_key] = {
                    "website": website,
                    "website_status": "unreachable",
                    "website_error": f"{type(exc).__name__}: {exc}",
                    "opportunity_score": 85,
                    "lead_priority": "alta",
                    "lead_reason": "Sito non analizzabile automaticamente.",
                }
        enriched = dict(row)
        enriched.update(audit_cache[cache_key])
        rows[index - 1] = enriched
        if consume_stop_after_current_item_request():
            rows = rows[:index]
            break

    rows.sort(
        key=lambda row: (
            -int(row.get("opportunity_score", 0) or 0),
            str(row.get("name", "") or "").lower(),
        )
    )
    priority_counts = {"alta": 0, "media": 0, "bassa": 0}
    for row in rows:
        priority = str(row.get("lead_priority", "") or "").lower()
        if priority in priority_counts:
            priority_counts[priority] += 1

    payload["meta"].update(
        {
            "audit_websites": bool(audit_websites),
            "website_timeout_seconds": max(float(website_timeout_seconds), 1.0),
            "audited_website_count": len(audit_cache),
            "audit_errors": audit_errors,
            "lead_priority_counts": priority_counts,
            "row_count": len(rows),
        }
    )
    return ScrapeOutcome(source="google_maps", rows=rows, meta=payload["meta"])


@browser(
    profile=resolve_browser_profile,
    add_arguments=resolve_browser_arguments,
    wait_for_complete_page_load=False,
)
def _scrape_google_maps_task(driver: Driver, config: dict) -> dict:
    targets = _build_search_targets(
        config["search"],
        config.get("city", ""),
        config.get("province", ""),
        config.get("country", ""),
    )
    max_results = max(int(config.get("max_results", 25)), 1)
    rows_by_key: dict[str, dict] = {}
    cookie_actions: list[str] = []
    search_urls: list[str] = []
    search_errors: list[dict[str, str]] = []

    for target_index, target in enumerate(targets, start=1):
        search_url = target["url"]
        search_urls.append(search_url)
        print(f"Ricerca Maps {target_index}/{len(targets)}: {target['label']}", flush=True)
        try:
            driver.google_get(search_url, wait=Wait.LONG, timeout=30)
            if config.get("page_settle_seconds", 0):
                driver.sleep(float(config["page_settle_seconds"]))
            cookie_action = click_first_matching_text(driver, DEFAULT_COOKIE_REJECT_TEXTS)
            if cookie_action:
                cookie_actions.append(cookie_action)

            search_rows = _collect_search_rows(
                driver,
                search_url=search_url,
                search=target["search"],
                city=target["city"],
                province=target["province"],
                country=target["country"],
                max_results=max_results,
                exclude_sponsored=bool(config.get("exclude_sponsored", True)),
            )
        except Exception as exc:
            search_errors.append({
                "search": target["search"],
                "city": target["city"],
                "error": f"{type(exc).__name__}: {exc}",
            })
            continue

        for row in search_rows:
            key = _row_identity(row)
            if key in rows_by_key:
                _merge_search_context(rows_by_key[key], row)
                continue
            row["extracted_at"] = datetime.now().isoformat(timespec="seconds")
            row["extracted_order"] = len(rows_by_key) + 1
            rows_by_key[key] = row

    rows = list(rows_by_key.values())
    detail_errors: list[dict[str, str]] = []
    if config.get("include_details", True):
        for index, row in enumerate(rows, start=1):
            if consume_skip_current_item_request():
                row["detail_skipped"] = True
                if consume_stop_after_current_item_request():
                    rows = rows[:index]
                    break
                continue
            link = str(row.get("link", "") or "").strip()
            if not link:
                if consume_stop_after_current_item_request():
                    rows = rows[:index]
                    break
                continue
            print(f"Dettaglio Maps {index}/{len(rows)}: {row.get('name', link)}", flush=True)
            try:
                _enrich_row_from_place_page(driver, row, config)
            except Exception as exc:
                row["detail_error"] = f"{type(exc).__name__}: {exc}"
                detail_errors.append({"link": link, "error": row["detail_error"]})
            delay = float(config.get("action_delay_seconds", 0) or 0)
            if delay:
                driver.sleep(delay)
            if consume_stop_after_current_item_request():
                rows = rows[:index]
                break

    return {
        "rows": rows,
        "meta": {
            "cookie_banner_action": cookie_actions[-1] if cookie_actions else "",
            "cookie_banner_actions": cookie_actions,
            "search": config["search"],
            "city": config.get("city", ""),
            "province": config.get("province", ""),
            "country": config.get("country", ""),
            "search_url": search_urls[0] if search_urls else "",
            "search_urls": search_urls,
            "search_count": len(targets),
            "max_results_per_search": max_results,
            "exclude_sponsored": bool(config.get("exclude_sponsored", True)),
            "include_details": bool(config.get("include_details", True)),
            "search_errors": search_errors,
            "detail_errors": detail_errors,
            "row_count": len(rows),
        },
    }


def _collect_search_rows(
    driver: Driver,
    search_url: str,
    search: str,
    city: str,
    province: str,
    country: str,
    max_results: int,
    exclude_sponsored: bool,
) -> list[dict]:
    feed = driver.select('[role="feed"]', wait=Wait.VERY_LONG)
    if feed is None:
        navigate_with_retries(driver, search_url, wait=Wait.LONG, use_google_get=True, timeout_seconds=30)
        feed = driver.select('[role="feed"]', wait=Wait.VERY_LONG)

    rows_by_key: dict[str, dict] = {}
    stalled_scrolls = 0
    last_count = 0

    while feed is not None:
        for article_payload in _read_article_payloads(driver):
            row = _raw_article_to_row(
                raw_text=str(article_payload.get("raw_text", "") or ""),
                link=str(article_payload.get("link", "") or ""),
                search_url=search_url,
                search=search,
                city=city,
                province=province,
                country=country,
            )
            if exclude_sponsored and row.get("is_sponsored"):
                continue
            key = _row_identity(row)
            if not key:
                continue
            rows_by_key[key] = row

        if len(rows_by_key) >= max_results:
            break
        scroll_state = driver.run_js(
            """
const feed = document.querySelector('[role="feed"]');
if (!feed) return null;
const before = feed.scrollTop;
feed.scrollTop = feed.scrollHeight;
return {
  before,
  after: feed.scrollTop,
  height: feed.scrollHeight,
  viewport: feed.clientHeight
};
            """
        )
        if not scroll_state:
            break
        time.sleep(1.25)

        current_count = len(rows_by_key)
        if current_count == last_count:
            stalled_scrolls += 1
            if stalled_scrolls >= 3:
                break
        else:
            stalled_scrolls = 0
            last_count = current_count

    return list(rows_by_key.values())[:max_results]


def _article_to_row(
    article,
    search_url: str,
    search: str,
    city: str,
    province: str,
    country: str,
) -> dict:
    raw_text = article.text.strip()
    link = _get_element_href(article, 'a[href*="/maps/place/"], a[href*="/maps?cid="], a[href]')
    return _raw_article_to_row(raw_text, link, search_url, search, city, province, country)


def _raw_article_to_row(
    raw_text: str,
    link: str,
    search_url: str,
    search: str,
    city: str,
    province: str,
    country: str,
) -> dict:
    raw_text = str(raw_text or "").strip()
    lines = _clean_lines(raw_text)
    phones = extract_phone_numbers(raw_text)
    rating, reviews_count = _extract_rating_and_reviews(lines)

    return {
        "source": "google_maps",
        "search": search,
        "searches": [search] if search else [],
        "search_url": search_url,
        "city": city,
        "cities": [city] if city else [],
        "province": province,
        "country": country,
        "name": lines[0] if lines else "",
        "category": "",
        "phone": " | ".join(phones),
        "address": _extract_address(lines, phones),
        "location": _extract_address(lines, phones),
        "website": "",
        "rating": rating,
        "reviews_count": reviews_count,
        "link": link,
        "raw_text": raw_text,
        "is_sponsored": "sponsorizzato" in raw_text.lower() or "sponsored" in raw_text.lower(),
        "lead_status": "nuovo",
    }


def _read_article_payloads(driver: Driver) -> list[dict[str, str]]:
    payload = driver.run_js(
        """
return [...document.querySelectorAll('[role="article"]')].map((article) => {
  const preferredLink = article.querySelector('a[href*="/maps/place/"], a[href*="/maps?cid="]');
  const fallbackLink = article.querySelector('a[href]');
  const link = preferredLink || fallbackLink;
  return {
    raw_text: (article.innerText || article.textContent || '').trim(),
    link: link ? (link.href || link.getAttribute('href') || '') : ''
  };
});
        """
    )
    if not isinstance(payload, list):
        return []
    return [item for item in payload if isinstance(item, dict)]


def _enrich_row_from_place_page(driver: Driver, row: dict, config: dict) -> None:
    link = str(row.get("link", "") or "").strip()
    navigated = navigate_with_retries(driver, link, wait=Wait.LONG, timeout_seconds=30)
    if config.get("page_settle_seconds", 0):
        driver.sleep(float(config["page_settle_seconds"]))

    details = driver.run_js(
        """
const visible = (element) => {
  if (!element) return false;
  const style = window.getComputedStyle(element);
  return style.display !== "none" && style.visibility !== "hidden" && element.getClientRects().length > 0;
};
const clean = (value) => (value || "").replace(/\\s+/g, " ").trim();
const firstVisible = (selectors) => {
  for (const selector of selectors) {
    const element = [...document.querySelectorAll(selector)].find(visible);
    if (element) return element;
  }
  return null;
};
const textOf = (element) => clean(element ? (element.innerText || element.textContent || element.getAttribute("aria-label")) : "");
const address = firstVisible(['button[data-item-id="address"]', '[data-item-id="address"]']);
const phone = firstVisible(['button[data-item-id^="phone:tel:"]', '[data-item-id^="phone:tel:"]']);
const website = firstVisible(['a[data-item-id="authority"]', 'a[aria-label*="Sito web"]', 'a[aria-label*="Website"]']);
const category = firstVisible([
  'button[jsaction*="pane.rating.category"]',
  'button[jsaction*="category"]',
  '[role="main"] button.DkEaL'
]);
const ratingContainer = firstVisible(['div.F7nice', '[role="img"][aria-label*="stella"]', '[role="img"][aria-label*="star"]']);
const reviews = firstVisible([
  'button[jsaction*="moreReviews"]',
  'button[aria-label*="recension"]',
  'button[aria-label*="review"]'
]);
return {
  name: textOf(firstVisible(['h1'])),
  category: textOf(category),
  address: textOf(address),
  phone: textOf(phone),
  phoneDataId: phone ? clean(phone.getAttribute('data-item-id')) : '',
  website: website ? clean(website.href || website.getAttribute('href')) : '',
  ratingText: textOf(ratingContainer),
  ratingAria: ratingContainer ? clean(ratingContainer.getAttribute('aria-label')) : '',
  reviewsText: textOf(reviews),
  reviewsAria: reviews ? clean(reviews.getAttribute('aria-label')) : '',
  pageText: textOf(firstVisible(['[role="main"]']))
};
        """
    ) or {}

    row["detail_navigated"] = bool(navigated)
    row["detail_checked"] = True
    row["maps_detail_url"] = current_page_url(driver) or link
    name = _clean_detail_value(details.get("name", ""))
    category = _clean_detail_value(details.get("category", ""))
    address = _clean_labeled_value(details.get("address", ""), ("indirizzo", "address"))
    phone = _clean_phone_detail(details.get("phone", ""), details.get("phoneDataId", ""))
    website = str(details.get("website", "") or "").strip()
    rating = _parse_rating(f"{details.get('ratingText', '')} {details.get('ratingAria', '')}")
    reviews_count = _parse_reviews_count(f"{details.get('reviewsText', '')} {details.get('reviewsAria', '')}")

    if name:
        row["name"] = name
    if category:
        row["category"] = category
    if address:
        row["address"] = address
        row["location"] = address
    if phone:
        row["phone"] = phone
    if website:
        row["website"] = website
    if rating is not None:
        row["rating"] = rating
    if reviews_count is not None:
        row["reviews_count"] = reviews_count
    page_text = str(details.get("pageText", "") or "").strip()
    if page_text:
        row["detail_text"] = page_text[:5000]


def _build_search_targets(search: str, city: str, province: str, country: str) -> list[dict[str, str]]:
    searches = _parse_multi_value(search)
    if not searches:
        searches = [str(search or "").strip()]
    if len(searches) == 1 and searches[0].lower().startswith(("http://", "https://")):
        return [{
            "search": searches[0],
            "city": "",
            "province": "",
            "country": "",
            "url": searches[0],
            "label": searches[0],
        }]

    cities = _parse_multi_value(city) or [""]
    targets: list[dict[str, str]] = []
    for current_search in searches:
        for current_city in cities:
            url = build_google_maps_search_url(current_search, current_city, province, country)
            label = " ".join(part for part in (current_search, current_city, province, country) if part).strip()
            targets.append({
                "search": current_search,
                "city": current_city,
                "province": province,
                "country": country,
                "url": url,
                "label": label or url,
            })
    return targets


def _parse_multi_value(value: str) -> list[str]:
    parts = re.split(r"[,;\n]+", str(value or ""))
    values: list[str] = []
    seen: set[str] = set()
    for part in parts:
        cleaned = part.strip()
        lowered = cleaned.lower()
        if not cleaned or lowered in seen:
            continue
        seen.add(lowered)
        values.append(cleaned)
    return values


def _merge_search_context(existing: dict, incoming: dict) -> None:
    for list_field, scalar_field in (("searches", "search"), ("cities", "city")):
        values = list(existing.get(list_field, []) or [])
        candidate = str(incoming.get(scalar_field, "") or "").strip()
        if candidate and candidate not in values:
            values.append(candidate)
        existing[list_field] = values


def _row_identity(row: dict) -> str:
    link = str(row.get("link", "") or "").strip()
    if link:
        return link.split("?", 1)[0].rstrip("/").lower()
    phone = re.sub(r"\D", "", str(row.get("phone", "") or ""))
    if phone:
        return f"phone:{phone}"
    name = str(row.get("name", "") or "").strip().lower()
    address = str(row.get("address", "") or "").strip().lower()
    return f"{name}|{address}" if name or address else ""


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

        parts = [normalize_whitespace(part) for part in re.split(r"[·|]", line) if normalize_whitespace(part)]
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
        if not normalized or normalized in ACTION_LINES:
            continue
        if len(normalized) <= 2 and not any(char.isalnum() for char in normalized):
            continue
        cleaned_lines.append(normalized)
    return cleaned_lines


def _extract_rating_and_reviews(lines: list[str]) -> tuple[float | str, int | str]:
    for line in lines:
        match = RATING_PATTERN.match(line.replace(" ", ""))
        if not match:
            continue
        rating = float(match.group(1).replace(",", "."))
        reviews = int(re.sub(r"\D", "", match.group(2) or "")) if match.group(2) else ""
        return rating, reviews
    return "", ""


def _looks_like_rating(value: str) -> bool:
    return bool(RATING_PATTERN.match(str(value or "").replace(" ", "")))


def _parse_rating(value: str) -> float | None:
    match = re.search(r"(?<!\d)([0-5](?:[.,]\d)?)(?!\d)", str(value or ""))
    if not match:
        return None
    return float(match.group(1).replace(",", "."))


def _parse_reviews_count(value: str) -> int | None:
    text = normalize_whitespace(str(value or ""))
    match = re.search(r"([\d.,]+)\s*(?:recension|reviews?)", text, re.IGNORECASE)
    if not match:
        return None
    digits = re.sub(r"\D", "", match.group(1))
    return int(digits) if digits else None


def _clean_detail_value(value: object) -> str:
    text = "".join(
        " " if unicodedata.category(character) in {"Cc", "Cf", "Co", "Cs"} else character
        for character in str(value or "")
    )
    return normalize_whitespace(text)


def _clean_labeled_value(value: object, labels: tuple[str, ...]) -> str:
    cleaned = _clean_detail_value(value)
    for label in labels:
        cleaned = re.sub(rf"^{re.escape(label)}\s*:\s*", "", cleaned, flags=re.IGNORECASE)
    return cleaned


def _clean_phone_detail(phone_text: object, data_item_id: object) -> str:
    phones = extract_phone_numbers(str(phone_text or ""))
    if phones:
        return " | ".join(phones)
    data_value = str(data_item_id or "")
    if data_value.lower().startswith("phone:tel:"):
        return data_value.split(":", 2)[-1].strip()
    return _clean_labeled_value(phone_text, ("telefono", "phone"))


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
