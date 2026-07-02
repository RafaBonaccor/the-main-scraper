import re
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, quote_plus, urlsplit, urlunsplit

from botasaurus.browser import Driver, Wait, browser

from ..browser_helpers import DEFAULT_COOKIE_REJECT_TEXTS, click_first_matching_text, current_page_url
from ..browser_runtime import resolve_browser_arguments, resolve_browser_profile
from ..models import ScrapeOutcome
from ..utils import normalize_whitespace
from ..vinted_database import DEFAULT_VINTED_DB_PATH, save_vinted_rows


VINTED_BASE_URL = "https://www.vinted.it"
ITEM_ID_PATTERN = re.compile(r"/items/(\d+)")
PRICE_PATTERN = re.compile(r"(?:â‚¬\s*)?(\d[\d.\s]*(?:,\d{1,2})?)(?:\s*â‚¬)?")
SHIPPING_PATTERN = re.compile(
    r"(?:spedizione(?:\s+da)?|consegna)(?:[^0-9]{0,20})(\d[\d.\s]*(?:,\d{1,2})?\s*(?:â‚¬|€)?)",
    re.IGNORECASE,
)


def run_vinted_scraper(
    search: str,
    max_results: int = 100,
    db_path: str = str(DEFAULT_VINTED_DB_PATH),
    browser_mode: str = "isolated",
    browser_user_data_dir: str = "",
    browser_profile_directory: str = "Default",
    keep_browser_open: bool = False,
    refresh_browser_profile: bool = False,
    keep_open_seconds: int = 0,
    slow_mode: bool = False,
    action_delay_seconds: float = 1.5,
    page_settle_seconds: float = 3.0,
) -> ScrapeOutcome:
    search_url = build_vinted_search_url(search)
    search_term = extract_vinted_search_term(search_url) or str(search or "").strip()
    action_delay, page_settle = _vinted_timing_config(
        slow_mode=slow_mode,
        action_delay_seconds=action_delay_seconds,
        page_settle_seconds=page_settle_seconds,
    )
    config = {
        "search": search,
        "search_term": search_term,
        "search_url": search_url,
        "max_results": max(int(max_results), 0),
        "browser_mode": browser_mode,
        "browser_user_data_dir": browser_user_data_dir,
        "browser_profile_directory": browser_profile_directory,
        "keep_browser_open": bool(keep_browser_open),
        "refresh_browser_profile": bool(refresh_browser_profile),
        "keep_open_seconds": max(int(keep_open_seconds or 0), 0),
        "slow_mode": bool(slow_mode),
        "action_delay_seconds": action_delay,
        "page_settle_seconds": page_settle,
    }
    payload = _scrape_vinted_task(config, reuse_driver=bool(keep_browser_open))
    db_meta = save_vinted_rows(payload["rows"], db_path=db_path)
    for row in payload["rows"]:
        row["db_path"] = db_meta["db_path"]
        row["db_saved"] = True
    payload["meta"].update(db_meta)
    return ScrapeOutcome(source="vinted", rows=payload["rows"], meta=payload["meta"])


def run_vinted_description_extractor(
    items: list[dict | str],
    db_path: str = str(DEFAULT_VINTED_DB_PATH),
    browser_mode: str = "isolated",
    browser_user_data_dir: str = "",
    browser_profile_directory: str = "Default",
    keep_browser_open: bool = False,
    refresh_browser_profile: bool = False,
    keep_open_seconds: int = 0,
    slow_mode: bool = False,
    action_delay_seconds: float = 1.5,
    page_settle_seconds: float = 3.0,
) -> ScrapeOutcome:
    normalized_items = _normalize_vinted_items(items)
    action_delay, page_settle = _vinted_timing_config(
        slow_mode=slow_mode,
        action_delay_seconds=action_delay_seconds,
        page_settle_seconds=page_settle_seconds,
    )
    config = {
        "items": normalized_items,
        "browser_mode": browser_mode,
        "browser_user_data_dir": browser_user_data_dir,
        "browser_profile_directory": browser_profile_directory,
        "keep_browser_open": bool(keep_browser_open),
        "refresh_browser_profile": bool(refresh_browser_profile),
        "keep_open_seconds": max(int(keep_open_seconds or 0), 0),
        "slow_mode": bool(slow_mode),
        "action_delay_seconds": action_delay,
        "page_settle_seconds": page_settle,
    }
    payload = _scrape_vinted_descriptions_task(config, reuse_driver=bool(keep_browser_open))
    db_meta = save_vinted_rows(payload["rows"], db_path=db_path)
    for row in payload["rows"]:
        row["db_path"] = db_meta["db_path"]
        row["db_saved"] = True
    payload["meta"].update(db_meta)
    return ScrapeOutcome(source="vinted", rows=payload["rows"], meta=payload["meta"])


@browser(
    profile=resolve_browser_profile,
    add_arguments=resolve_browser_arguments,
    wait_for_complete_page_load=False,
)
def _scrape_vinted_task(driver: Driver, config: dict) -> dict:
    search_url = config["search_url"]
    driver.get(search_url, wait=Wait.LONG, timeout=30)
    time.sleep(float(config.get("page_settle_seconds", 3.0) or 0))
    cookie_action = click_first_matching_text(driver, DEFAULT_COOKIE_REJECT_TEXTS)
    if cookie_action:
        time.sleep(float(config.get("action_delay_seconds", 1.5) or 0))

    driver.select('a[href*="/items/"]', wait=Wait.VERY_LONG)
    rows_by_link: dict[str, dict] = {}
    max_results = int(config.get("max_results", 100) or 0)
    stalled_scrolls = 0
    last_count = 0

    while True:
        for payload in _read_vinted_cards(driver):
            row = _card_payload_to_row(
                payload,
                search_term=config["search_term"],
                search_url=search_url,
            )
            if not row["link"]:
                continue
            rows_by_link[row["link"]] = row

        if max_results > 0 and len(rows_by_link) >= max_results:
            break

        scroll_state = driver.run_js(
            """
const before = window.scrollY;
window.scrollTo(0, document.documentElement.scrollHeight);
return {before, after: window.scrollY, height: document.documentElement.scrollHeight};
            """
        )
        if not scroll_state:
            break
        time.sleep(float(config.get("action_delay_seconds", 1.5) or 0))

        current_count = len(rows_by_link)
        if current_count == last_count:
            stalled_scrolls += 1
            if stalled_scrolls >= 4:
                break
        else:
            stalled_scrolls = 0
            last_count = current_count

    rows = list(rows_by_link.values())
    if max_results > 0:
        rows = rows[:max_results]
    keep_open_seconds = int(config.get("keep_open_seconds", 0) or 0)
    keep_browser_open = bool(config.get("keep_browser_open", False))
    if keep_open_seconds > 0 and not keep_browser_open:
        _keep_browser_open(driver, keep_open_seconds)
    return {
        "rows": rows,
        "meta": {
            "search": config["search"],
            "search_term": config["search_term"],
            "tag": "ricercato",
            "search_url": search_url,
            "max_results": max_results,
            "keep_browser_open": keep_browser_open,
            "keep_open_seconds": keep_open_seconds,
            "slow_mode": bool(config.get("slow_mode", False)),
            "action_delay_seconds": float(config.get("action_delay_seconds", 1.5) or 0),
            "page_settle_seconds": float(config.get("page_settle_seconds", 3.0) or 0),
            "cookie_banner_action": cookie_action or "",
            "row_count": len(rows),
        },
    }


@browser(
    profile=resolve_browser_profile,
    add_arguments=resolve_browser_arguments,
    wait_for_complete_page_load=False,
)
def _scrape_vinted_descriptions_task(driver: Driver, config: dict) -> dict:
    rows: list[dict] = []
    keep_open_seconds = int(config.get("keep_open_seconds", 0) or 0)
    keep_browser_open = bool(config.get("keep_browser_open", False))

    for item in config.get("items", []):
        if isinstance(item, dict):
            current_link = normalize_vinted_item_url(str(item.get("link", "") or ""))
            search_term = str(item.get("search_term", "") or "").strip()
            search_url = str(item.get("search_url", "") or "").strip() or build_vinted_search_url(search_term)
            tag = str(item.get("tag", "") or "ricercato").strip() or "ricercato"
            item_name = str(item.get("name", "") or "").strip()
        else:
            current_link = normalize_vinted_item_url(str(item or ""))
            search_term = ""
            search_url = build_vinted_search_url("")
            tag = "ricercato"
            item_name = ""
        if not current_link:
            continue
        driver.get(current_link, wait=Wait.LONG, timeout=30)
        time.sleep(float(config.get("page_settle_seconds", 3.0) or 0))
        cookie_action = click_first_matching_text(driver, DEFAULT_COOKIE_REJECT_TEXTS)
        if cookie_action:
            time.sleep(float(config.get("action_delay_seconds", 1.5) or 0))

        page_text = _read_vinted_detail_text(driver)
        title = _read_vinted_title(driver)
        if not title:
            title = item_name
        if not title:
            title = _fallback_title({"image_alt": "", "aria_label": ""}, current_link, page_text)
        description = _extract_vinted_description_from_body_text(page_text)
        price_text = _extract_vinted_primary_price(page_text, title) or _read_vinted_price(driver) or _find_price(page_text)
        shipping_price = _read_vinted_shipping_price(driver, page_text)
        shipping_price_value = parse_vinted_price(shipping_price)
        offer_text = _read_vinted_offer_text(driver)
        total_price, total_price_value = _build_vinted_total(price_text, shipping_price)
        row = {
            "source": "vinted",
            "tag": tag,
            "search_term": search_term or extract_vinted_search_term(search_url),
            "search_url": search_url,
            "item_id": ITEM_ID_PATTERN.search(urlsplit(current_link).path).group(1)
            if ITEM_ID_PATTERN.search(urlsplit(current_link).path)
            else "",
            "name": normalize_whitespace(title) or current_link,
            "description": description,
            "price": normalize_whitespace(price_text),
            "price_value": parse_vinted_price(price_text),
            "shipping_price": shipping_price,
            "shipping_price_value": shipping_price_value,
            "total_price": total_price,
            "total_price_value": total_price_value,
            "offer_available": bool(offer_text),
            "offer_text": offer_text,
            "currency": "EUR" if "€" in price_text or "â‚¬" in price_text else "",
            "link": current_link,
            "raw_text": page_text,
            "extracted_at": datetime.now().isoformat(timespec="seconds"),
        }
        rows.append(row)

    if keep_open_seconds > 0 and not keep_browser_open:
        _keep_browser_open(driver, keep_open_seconds)
    return {
        "rows": rows,
        "meta": {
            "tag": "ricercato",
            "items_count": len(rows),
            "keep_browser_open": keep_browser_open,
            "keep_open_seconds": keep_open_seconds,
            "slow_mode": bool(config.get("slow_mode", False)),
            "action_delay_seconds": float(config.get("action_delay_seconds", 1.5) or 0),
            "page_settle_seconds": float(config.get("page_settle_seconds", 3.0) or 0),
        },
    }


def _read_vinted_cards(driver: Driver) -> list[dict]:
    payload = driver.run_js(
        """
const clean = (value) => (value || '').replace(/\\s+/g, ' ').trim();
const links = [...document.querySelectorAll('a[href*="/items/"]')];
return links.map((link) => {
  const root = link.closest('[data-testid^="grid-item"], article')
    || link.parentElement?.parentElement?.parentElement
    || link.parentElement
    || link;
  const title = root.querySelector('[data-testid*="description-title"], [data-testid*="item-title"]');
  const price = root.querySelector('[data-testid*="price-text"], [data-testid*="item-price"]');
  const image = root.querySelector('img[alt]');
  return {
    link: link.href || link.getAttribute('href') || '',
    title: clean(title ? (title.innerText || title.textContent) : ''),
    price: clean(price ? (price.innerText || price.textContent) : ''),
    image_alt: clean(image ? image.getAttribute('alt') : ''),
    aria_label: clean(link.getAttribute('aria-label')),
    raw_text: clean(root.innerText || root.textContent),
  };
});
        """
    )
    if not isinstance(payload, list):
        return []
    return [item for item in payload if isinstance(item, dict)]


def _read_vinted_detail_text(driver: Driver) -> str:
    payload = driver.run_js(
        """
return document.body ? (document.body.innerText || document.body.textContent || '') : '';
        """
    )
    return str(payload or "").strip()


def _read_vinted_title(driver: Driver) -> str:
    payload = driver.run_js(
        """
const clean = (value) => (value || '').replace(/\\s+/g, ' ').trim();
const title = document.querySelector('h1');
return clean(title ? (title.innerText || title.textContent) : '');
        """
    )
    return normalize_whitespace(str(payload or ""))


def _read_vinted_price(driver: Driver) -> str:
    payload = driver.run_js(
        """
const clean = (value) => (value || '').replace(/\\s+/g, ' ').trim();
const selectors = [
  '[data-testid*="price-text"]',
  '[data-testid*="item-price"]',
  'span[aria-label*="€"]',
];
for (const selector of selectors) {
  const node = document.querySelector(selector);
  if (node) {
    return clean(node.innerText || node.textContent || '');
  }
}
return '';
        """
    )
    return normalize_whitespace(str(payload or ""))


def _read_vinted_shipping_price(driver: Driver, page_text: str = "") -> str:
    payload = driver.run_js(
        """
const clean = (value) => (value || '').replace(/\\s+/g, ' ').trim();
const nodes = [...document.querySelectorAll('[data-testid], button, span, div, p, li')];
for (const node of nodes) {
  const text = clean(node.innerText || node.textContent || '');
  if (!text) {
    continue;
  }
  if (/spedizione|consegna/i.test(text) && /\\d/.test(text)) {
    return text;
  }
}
return '';
        """
    )
    shipping_text = normalize_whitespace(str(payload or ""))
    extracted = _extract_vinted_shipping_price_text(shipping_text)
    if extracted:
        return extracted
    return _extract_vinted_shipping_price_text(page_text)


def _read_vinted_offer_text(driver: Driver) -> str:
    payload = driver.run_js(
        """
const clean = (value) => (value || '').replace(/\\s+/g, ' ').trim();
const nodes = [...document.querySelectorAll('button, a, span, div')];
for (const node of nodes) {
  const text = clean(node.innerText || node.textContent || '');
  if (!text) {
    continue;
  }
  if (text.length > 40) {
    continue;
  }
  if (/offerta/i.test(text) && /fare|fai|invia/i.test(text)) {
    return text;
  }
}
return '';
        """
    )
    return normalize_whitespace(str(payload or ""))


def _vinted_timing_config(
    slow_mode: bool,
    action_delay_seconds: float,
    page_settle_seconds: float,
) -> tuple[float, float]:
    action_delay = _nonnegative_float(action_delay_seconds, 1.5)
    page_settle = _nonnegative_float(page_settle_seconds, 3.0)
    if slow_mode:
        action_delay = max(action_delay, 2.5)
        page_settle = max(page_settle, 4.0)
    return action_delay, page_settle


def _normalize_vinted_items(raw_items: list[dict | str]) -> list[dict | str]:
    items: list[dict | str] = []
    seen: set[str] = set()
    for raw_item in raw_items:
        if isinstance(raw_item, dict):
            link = normalize_vinted_item_url(str(raw_item.get("link", "") or ""))
            if not link or link in seen:
                continue
            seen.add(link)
            copied = dict(raw_item)
            copied["link"] = link
            items.append(copied)
            continue
        link = normalize_vinted_item_url(str(raw_item or ""))
        if not link or link in seen:
            continue
        seen.add(link)
        items.append(link)
    return items


def _extract_vinted_description_from_body_text(body_text: str) -> str:
    lines = [normalize_whitespace(line) for line in str(body_text or "").splitlines()]
    lines = [line for line in lines if line]
    if not lines:
        return ""

    heading_candidates = ("descrizione", "description")
    stop_words = {
        "marca",
        "brand",
        "categoria",
        "categoria:",
        "colore",
        "taglia",
        "tag",
        "condizione",
        "stato",
        "prezzo",
        "venditore",
        "localita",
        "località",
        "spedizione",
        "materiale",
        "misura",
        "dimensione",
        "anno",
        "anno di acquisto",
    }

    for index, line in enumerate(lines):
        lowered = line.lower().rstrip(":")
        if lowered not in heading_candidates:
            continue
        description_lines: list[str] = []
        for candidate in lines[index + 1 :]:
            candidate_lower = candidate.lower().rstrip(":")
            if candidate_lower in heading_candidates:
                break
            if candidate_lower in stop_words and description_lines:
                break
            if candidate_lower in stop_words:
                continue
            if len(candidate) < 10 and not description_lines:
                continue
            description_lines.append(candidate)
            if len(" ".join(description_lines)) > 240:
                break
        description = normalize_whitespace(" ".join(description_lines))
        if description:
            return description

    text = normalize_whitespace(str(body_text or ""))
    long_match = re.search(
        r"Caricato\s+.+?\s+(Vendo.+?)(?:Spedizione\s+da|Acquista|Fai un'offerta|Chiedi info)",
        text,
        re.IGNORECASE,
    )
    if long_match:
        return normalize_whitespace(long_match.group(1))

    long_lines = [
        line
        for line in lines
        if len(line) >= 60 and line.lower().rstrip(":") not in stop_words and line.lower().rstrip(":") not in heading_candidates
    ]
    if long_lines:
        return max(long_lines, key=len)
    return ""


def _nonnegative_float(value: object, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return max(parsed, 0.0)


def _keep_browser_open(driver: Driver, seconds: int) -> None:
    deadline = time.monotonic() + max(int(seconds), 0)
    print(f"Browser Vinted lasciato aperto per {seconds} secondi.")
    missing_checks = 0
    while time.monotonic() < deadline:
        time.sleep(1)
        if current_page_url(driver):
            missing_checks = 0
            continue
        missing_checks += 1
        if missing_checks >= 3:
            break


def _card_payload_to_row(payload: dict, search_term: str, search_url: str) -> dict:
    link = normalize_vinted_item_url(str(payload.get("link", "") or ""))
    raw_text = normalize_whitespace(str(payload.get("raw_text", "") or ""))
    title = normalize_whitespace(str(payload.get("title", "") or ""))
    if not title:
        title = _fallback_title(payload, link, raw_text)
    price = normalize_whitespace(str(payload.get("price", "") or "")) or _find_price(raw_text)
    price_value = parse_vinted_price(price)
    item_id_match = ITEM_ID_PATTERN.search(urlsplit(link).path)

    return {
        "source": "vinted",
        "search_term": search_term,
        "tag": "ricercato",
        "search_url": search_url,
        "item_id": item_id_match.group(1) if item_id_match else "",
        "name": title,
        "price": price,
        "price_value": price_value,
        "shipping_price": "",
        "shipping_price_value": None,
        "total_price": price,
        "total_price_value": price_value,
        "offer_available": False,
        "offer_text": "",
        "currency": "EUR" if "€" in price or "â‚¬" in price else "",
        "link": link,
        "raw_text": raw_text,
        "extracted_at": datetime.now().isoformat(timespec="seconds"),
    }


def build_vinted_search_url(search: str) -> str:
    value = str(search or "").strip()
    if value.lower().startswith(("http://", "https://")):
        return value
    return f"{VINTED_BASE_URL}/catalog?search_text={quote_plus(value)}"


def extract_vinted_search_term(url: str) -> str:
    values = parse_qs(urlsplit(str(url or "")).query).get("search_text", [])
    return str(values[0] if values else "").strip()


def normalize_vinted_item_url(url: str) -> str:
    parsed = urlsplit(str(url or "").strip())
    if not parsed.netloc:
        parsed = urlsplit(f"{VINTED_BASE_URL}/{str(url or '').lstrip('/')}")
    return urlunsplit((parsed.scheme or "https", parsed.netloc, parsed.path, "", ""))


def parse_vinted_price(value: str) -> float | None:
    match = PRICE_PATTERN.search(str(value or ""))
    if not match:
        return None
    numeric = match.group(1).replace(" ", "")
    if "," in numeric:
        numeric = numeric.replace(".", "").replace(",", ".")
    try:
        return float(numeric)
    except ValueError:
        return None


def _extract_vinted_shipping_price_text(value: str) -> str:
    text = normalize_whitespace(str(value or ""))
    if not text:
        return ""
    match = SHIPPING_PATTERN.search(text)
    if not match:
        return ""
    return normalize_whitespace(match.group(1))


def _build_vinted_total(price_text: str, shipping_text: str) -> tuple[str, float | None]:
    price_value = parse_vinted_price(price_text)
    shipping_value = parse_vinted_price(shipping_text)
    if price_value is None and shipping_value is None:
        return "", None
    if price_value is None:
        return _format_vinted_amount(shipping_value), shipping_value
    if shipping_value is None:
        return normalize_whitespace(price_text), price_value
    total_value = price_value + shipping_value
    return _format_vinted_amount(total_value), total_value


def _format_vinted_amount(value: float | None) -> str:
    if value is None:
        return ""
    integer_part, decimal_part = f"{value:.2f}".split(".")
    return f"{integer_part},{decimal_part} EUR"


def _extract_vinted_primary_price(page_text: str, title: str) -> str:
    text = str(page_text or "")
    title_text = normalize_whitespace(str(title or ""))
    if title_text:
        around_title = re.compile(
            re.escape(title_text)
            + r".{0,220}?(\d[\d.\s]*(?:,\d{1,2})?\s*(?:â‚¬|€))\s+(\d[\d.\s]*(?:,\d{1,2})?\s*(?:â‚¬|€)).{0,120}?Include la Protezione acquisti",
            re.IGNORECASE | re.DOTALL,
        )
        match = around_title.search(text)
        if match:
            return _pick_higher_vinted_price(
                normalize_whitespace(match.group(1)),
                normalize_whitespace(match.group(2)),
            )
    protection_match = re.search(
        r"(\d[\d.\s]*(?:,\d{1,2})?\s*(?:â‚¬|€))\s+(\d[\d.\s]*(?:,\d{1,2})?\s*(?:â‚¬|€))\s+Include la Protezione acquisti",
        text,
        re.IGNORECASE,
    )
    if protection_match:
        return _pick_higher_vinted_price(
            normalize_whitespace(protection_match.group(1)),
            normalize_whitespace(protection_match.group(2)),
        )
    return ""


def _pick_higher_vinted_price(first_price: str, second_price: str) -> str:
    first_value = parse_vinted_price(first_price)
    second_value = parse_vinted_price(second_price)
    if second_value is None:
        return first_price
    if first_value is None:
        return second_price
    return second_price if second_value >= first_value else first_price


def _find_price(raw_text: str) -> str:
    for segment in re.split(r"[|\n]", str(raw_text or "")):
        if "€" in segment or "â‚¬" in segment:
            return normalize_whitespace(segment)
    return ""


def _fallback_title(payload: dict, link: str, raw_text: str) -> str:
    for field in ("image_alt", "aria_label"):
        value = normalize_whitespace(str(payload.get(field, "") or ""))
        if value and "€" not in value and "â‚¬" not in value:
            return value
    path_tail = urlsplit(link).path.rstrip("/").rsplit("/", 1)[-1]
    slug = re.sub(r"^\d+-?", "", path_tail).replace("-", " ").strip()
    if slug:
        return slug
    return raw_text.split(" â‚¬", 1)[0].strip()
