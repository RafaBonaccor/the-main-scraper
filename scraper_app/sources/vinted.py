import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, quote_plus, urlencode, urlsplit, urlunsplit

from botasaurus.browser import Driver, Wait, browser

from ..browser_helpers import DEFAULT_COOKIE_REJECT_TEXTS, click_first_matching_text, current_page_url
from ..chrome_reuse import preferred_host_fragment_for_url, try_reuse_running_chrome
from ..browser_runtime import (
    PROFILE_SKIP_DIR_NAMES,
    PROFILE_SKIP_FILE_NAMES,
    resolve_browser_arguments,
    resolve_browser_profile,
)
from ..exporters import write_outcome_json
from ..models import ScrapeOutcome
from ..runtime_controls import consume_stop_after_current_item_request, consume_vinted_login_confirmed_request
from ..utils import normalize_whitespace
from ..vinted_browser_session import get_active_vinted_browser_session, register_vinted_browser_session
from ..vinted_database import DEFAULT_VINTED_DB_PATH, save_vinted_rows
from ..vinted_access import emit_vinted_access_signal, read_vinted_access_status


VINTED_BASE_URL = "https://www.vinted.it"
MAIN_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "main.py"
ITEM_ID_PATTERN = re.compile(r"/items/(\d+)")
PRICE_PATTERN = re.compile(r"(?:â‚¬\s*)?(\d[\d.\s]*(?:,\d{1,2})?)(?:\s*â‚¬)?")
SHIPPING_PATTERN = re.compile(
    r"(?:spedizione(?:\s+da)?|consegna)(?:[^0-9]{0,20})(\d[\d.\s]*(?:,\d{1,2})?\s*(?:â‚¬|€)?)",
    re.IGNORECASE,
)
RICERCATO_BADGE_PATTERN = re.compile(r"\bricercato\b", re.IGNORECASE)
FAVORITE_COUNT_PATTERN = re.compile(r"\d+")
FAVORITE_COUNT_REVIEW_THRESHOLD = 15


def run_vinted_scraper(
    search: str,
    max_results: int = 100,
    db_path: str = str(DEFAULT_VINTED_DB_PATH),
    ui_result_json: str = "",
    browser_mode: str = "chrome_normale",
    browser_user_data_dir: str = "",
    browser_profile_directory: str = "Default",
    keep_browser_open: bool = True,
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
        "db_path": db_path,
        "ui_result_json": ui_result_json,
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
    if not payload["meta"].get("db_saved_live"):
        db_meta = save_vinted_rows(payload["rows"], db_path=db_path)
        for row in payload["rows"]:
            row["db_path"] = db_meta["db_path"]
            row["db_saved"] = True
        payload["meta"].update(db_meta)
    return ScrapeOutcome(source="vinted", rows=payload["rows"], meta=payload["meta"])


def run_vinted_description_extractor(
    items: list[dict | str],
    db_path: str = str(DEFAULT_VINTED_DB_PATH),
    ui_result_json: str = "",
    browser_mode: str = "chrome_normale",
    browser_user_data_dir: str = "",
    browser_profile_directory: str = "Default",
    keep_browser_open: bool = True,
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
        "db_path": db_path,
        "ui_result_json": ui_result_json,
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
    if not payload["meta"].get("db_saved_live"):
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
    access_status = read_vinted_access_status(driver)
    emit_vinted_access_signal(access_status)
    access_status = _wait_for_vinted_login_if_needed(driver, access_status)

    driver.select('a[href*="/items/"]', wait=Wait.VERY_LONG)
    rows_by_link: dict[str, dict] = {}
    max_results = int(config.get("max_results", 100) or 0)
    pages_visited: list[int] = []
    seen_pages: set[int] = set()

    while True:
        current_page = extract_vinted_page_number(current_page_url(driver) or search_url)
        if current_page in seen_pages:
            break
        seen_pages.add(current_page)
        pages_visited.append(current_page)

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

        next_page = current_page + 1
        next_page_url = _read_vinted_next_page_url(driver, next_page)
        if not next_page_url:
            break
        driver.get(next_page_url, wait=Wait.LONG, timeout=30)
        time.sleep(float(config.get("page_settle_seconds", 3.0) or 0))
        page_cookie_action = click_first_matching_text(driver, DEFAULT_COOKIE_REJECT_TEXTS)
        if page_cookie_action:
            time.sleep(float(config.get("action_delay_seconds", 1.5) or 0))
            cookie_action = page_cookie_action
        access_status = read_vinted_access_status(driver)
        emit_vinted_access_signal(access_status)
        access_status = _wait_for_vinted_login_if_needed(driver, access_status)
        driver.select('a[href*="/items/"]', wait=Wait.VERY_LONG)

    rows = _prioritize_vinted_rows(rows_by_link.values())
    if max_results > 0:
        rows = rows[:max_results]
    keep_open_seconds = int(config.get("keep_open_seconds", 0) or 0)
    keep_browser_open = bool(config.get("keep_browser_open", False))
    meta = {
        "search": config["search"],
        "search_term": config["search_term"],
        "tag": "",
        "search_url": search_url,
        "max_results": max_results,
        "keep_browser_open": keep_browser_open,
        "keep_open_seconds": keep_open_seconds,
        "slow_mode": bool(config.get("slow_mode", False)),
        "action_delay_seconds": float(config.get("action_delay_seconds", 1.5) or 0),
        "page_settle_seconds": float(config.get("page_settle_seconds", 3.0) or 0),
        "cookie_banner_action": cookie_action or "",
        "vinted_access_marker_present": bool(access_status.get("marker_present")),
        "vinted_access_expected_alt": str(access_status.get("expected_alt", "") or ""),
        "vinted_access_current_url": str(access_status.get("current_url", "") or ""),
        "vinted_access_checked_at": str(access_status.get("checked_at", "") or ""),
        "pages_visited": pages_visited,
        "pages_visited_count": len(pages_visited),
        "row_count": len(rows),
    }
    _persist_vinted_live_results(
        rows=rows,
        meta=meta,
        db_path=str(config.get("db_path", "") or DEFAULT_VINTED_DB_PATH),
        ui_result_json=str(config.get("ui_result_json", "") or ""),
    )
    _detach_vinted_browser_if_requested(driver, config)
    return {
        "rows": rows,
        "meta": meta,
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
    last_access_status: dict[str, object] = {}

    for item in config.get("items", []):
        if isinstance(item, dict):
            current_link = normalize_vinted_item_url(str(item.get("link", "") or ""))
            search_term = str(item.get("search_term", "") or "").strip()
            search_url = str(item.get("search_url", "") or "").strip() or build_vinted_search_url(search_term)
            tag = str(item.get("tag", "") or "").strip()
            item_name = str(item.get("name", "") or "").strip()
        else:
            current_link = normalize_vinted_item_url(str(item or ""))
            search_term = ""
            search_url = build_vinted_search_url("")
            tag = ""
            item_name = ""
        if not current_link:
            continue
        driver.get(current_link, wait=Wait.LONG, timeout=30)
        time.sleep(float(config.get("page_settle_seconds", 3.0) or 0))
        cookie_action = click_first_matching_text(driver, DEFAULT_COOKIE_REJECT_TEXTS)
        if cookie_action:
            time.sleep(float(config.get("action_delay_seconds", 1.5) or 0))
        last_access_status = read_vinted_access_status(driver)
        emit_vinted_access_signal(last_access_status)
        last_access_status = _wait_for_vinted_login_if_needed(driver, last_access_status)

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

    meta = {
        "tag": "",
        "items_count": len(rows),
        "keep_browser_open": keep_browser_open,
        "keep_open_seconds": keep_open_seconds,
        "slow_mode": bool(config.get("slow_mode", False)),
        "action_delay_seconds": float(config.get("action_delay_seconds", 1.5) or 0),
        "page_settle_seconds": float(config.get("page_settle_seconds", 3.0) or 0),
        "vinted_access_marker_present": bool(last_access_status.get("marker_present")),
        "vinted_access_expected_alt": str(last_access_status.get("expected_alt", "") or ""),
        "vinted_access_current_url": str(last_access_status.get("current_url", "") or ""),
        "vinted_access_checked_at": str(last_access_status.get("checked_at", "") or ""),
    }
    _persist_vinted_live_results(
        rows=rows,
        meta=meta,
        db_path=str(config.get("db_path", "") or DEFAULT_VINTED_DB_PATH),
        ui_result_json=str(config.get("ui_result_json", "") or ""),
    )
    _detach_vinted_browser_if_requested(driver, config)
    return {
        "rows": rows,
        "meta": meta,
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
  const secondaryBadge = root.querySelector('[data-testid*="secondary-badge--content"], [data-testid*="secondary-badge"]');
  const favouriteCount = root.querySelector('[data-testid="favourite-count-text"]');
  const secondaryBadgeText = clean(secondaryBadge ? (secondaryBadge.innerText || secondaryBadge.textContent) : '');
  return {
    link: link.href || link.getAttribute('href') || '',
    title: clean(title ? (title.innerText || title.textContent) : ''),
    price: clean(price ? (price.innerText || price.textContent) : ''),
    image_alt: clean(image ? image.getAttribute('alt') : ''),
    aria_label: clean(link.getAttribute('aria-label')),
    favorite_count_text: clean(favouriteCount ? (favouriteCount.innerText || favouriteCount.textContent) : ''),
    secondary_badge_text: secondaryBadgeText,
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


def _hold_vinted_browser_if_requested(driver: Driver, keep_browser_open: bool, keep_open_seconds: int) -> None:
    if keep_browser_open:
        _keep_browser_open(driver, 0)
        return
    if keep_open_seconds > 0:
        _keep_browser_open(driver, keep_open_seconds)


def _wait_for_vinted_login_if_needed(driver: Driver, access_status: dict[str, object]) -> dict[str, object]:
    if bool(access_status.get("marker_present")):
        return access_status
    emit_vinted_login_required_signal(access_status)
    while True:
        if consume_stop_after_current_item_request():
            raise RuntimeError("Attesa login Vinted interrotta su richiesta dell'utente.")
        if consume_vinted_login_confirmed_request():
            refreshed_status = read_vinted_access_status(driver)
            emit_vinted_access_signal(refreshed_status)
            if bool(refreshed_status.get("marker_present")):
                return refreshed_status
            emit_vinted_login_required_signal(refreshed_status)
        time.sleep(1)


def emit_vinted_login_required_signal(access_status: dict[str, object]) -> None:
    print(f"__VINTED_LOGIN_REQUIRED__:{json.dumps(access_status, ensure_ascii=False)}", flush=True)


def _detach_vinted_browser_if_requested(driver: Driver, config: dict) -> None:
    keep_browser_open = bool(config.get("keep_browser_open", False))
    keep_open_seconds = int(config.get("keep_open_seconds", 0) or 0)
    if not keep_browser_open and keep_open_seconds <= 0:
        return
    active_session = get_active_vinted_browser_session()
    if active_session is not None:
        print(
            "Browser Vinted gia aperto: riuso la sessione esistente senza aprirne un altro.",
            flush=True,
        )
        return
    target_url = str(current_page_url(driver) or config.get("search_url", "") or VINTED_BASE_URL).strip() or VINTED_BASE_URL
    reused_chrome = try_reuse_running_chrome(
        target_url,
        preferred_host_fragment=preferred_host_fragment_for_url(target_url),
    )
    if reused_chrome.get("reused"):
        print(
            "Chrome gia aperto: riuso il browser esistente per lasciare Vinted disponibile.",
            flush=True,
        )
        return
    command = _build_detached_vinted_browser_command(target_url, config, keep_open_seconds)
    launched_process = subprocess.Popen(
        command,
        cwd=str(MAIN_SCRIPT_PATH.parent),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    register_vinted_browser_session(launched_process.pid, target_url, source="detached")
    if keep_browser_open:
        print("Browser Vinted sganciato in un processo separato e lasciato aperto.", flush=True)
    else:
        print(
            f"Browser Vinted sganciato in un processo separato per {keep_open_seconds} secondi.",
            flush=True,
        )


def _build_detached_vinted_browser_command(target_url: str, config: dict, keep_open_seconds: int) -> list[str]:
    detached_mode, detached_root, detached_profile_directory = _build_detached_vinted_browser_profile(config)
    command = [
        sys.executable,
        str(MAIN_SCRIPT_PATH),
        "browser",
        "--url",
        str(target_url or VINTED_BASE_URL),
        "--keep-open-seconds",
        "0" if bool(config.get("keep_browser_open", False)) else str(max(int(keep_open_seconds or 0), 0)),
        "--browser-mode",
        detached_mode,
        "--browser-user-data-dir",
        detached_root,
        "--browser-profile-directory",
        detached_profile_directory,
    ]
    return command


def _build_detached_vinted_browser_profile(config: dict) -> tuple[str, str, str]:
    resolved_root = str(config.get("_resolved_browser_profile_root", "") or "").strip()
    profile_directory = str(config.get("browser_profile_directory", "") or "Default").strip() or "Default"
    if resolved_root and Path(resolved_root).exists():
        detached_root = _clone_browser_profile_root(Path(resolved_root))
        return "profilo_personalizzato", detached_root, profile_directory
    return (
        str(config.get("browser_mode", "chrome_normale") or "chrome_normale"),
        str(config.get("browser_user_data_dir", "") or ""),
        profile_directory,
    )


def _clone_browser_profile_root(source_root: Path) -> str:
    target_root = Path(tempfile.mkdtemp(prefix="tms_vinted_hold_"))
    for child in source_root.iterdir():
        if child.name in PROFILE_SKIP_DIR_NAMES:
            continue
        if child.name in PROFILE_SKIP_FILE_NAMES:
            continue
        target_child = target_root / child.name
        if child.is_dir():
            shutil.copytree(
                child,
                target_child,
                ignore=shutil.ignore_patterns(*PROFILE_SKIP_DIR_NAMES, *PROFILE_SKIP_FILE_NAMES, "*.tmp", "*.log"),
                dirs_exist_ok=True,
            )
        else:
            shutil.copy2(child, target_child)
    return str(target_root)


def _keep_browser_open(driver: Driver, seconds: int) -> None:
    wait_forever = max(int(seconds), 0) == 0
    deadline = time.monotonic() + max(int(seconds), 0)
    if wait_forever:
        print("Browser Vinted lasciato aperto finche non lo chiudi manualmente.", flush=True)
    else:
        print(f"Browser Vinted lasciato aperto per {seconds} secondi.", flush=True)
    missing_checks = 0
    while wait_forever or time.monotonic() < deadline:
        time.sleep(1)
        if current_page_url(driver):
            missing_checks = 0
            continue
        missing_checks += 1
        if missing_checks >= 3:
            break


def _persist_vinted_live_results(rows: list[dict], meta: dict, db_path: str, ui_result_json: str) -> None:
    db_meta = save_vinted_rows(rows, db_path=db_path)
    for row in rows:
        row["db_path"] = db_meta["db_path"]
        row["db_saved"] = True
    meta.update(db_meta)
    meta["db_saved_live"] = True
    if ui_result_json:
        ui_result_path = Path(ui_result_json).expanduser()
        ui_result_path.parent.mkdir(parents=True, exist_ok=True)
        write_outcome_json(ui_result_path, ScrapeOutcome(source="vinted", rows=rows, meta=meta))


def _card_payload_to_row(payload: dict, search_term: str, search_url: str) -> dict:
    link = normalize_vinted_item_url(str(payload.get("link", "") or ""))
    raw_text = normalize_whitespace(str(payload.get("raw_text", "") or ""))
    title = normalize_whitespace(str(payload.get("title", "") or ""))
    if not title:
        title = _fallback_title(payload, link, raw_text)
    price = normalize_whitespace(str(payload.get("price", "") or "")) or _find_price(raw_text)
    price_value = parse_vinted_price(price)
    item_id_match = ITEM_ID_PATTERN.search(urlsplit(link).path)
    secondary_badge_text = normalize_whitespace(str(payload.get("secondary_badge_text", "") or ""))
    has_ricercato_badge = bool(RICERCATO_BADGE_PATTERN.search(secondary_badge_text))
    favorite_count = parse_vinted_favorite_count(payload.get("favorite_count_text"))
    evaluation_label = classify_vinted_evaluation(
        favorite_count=favorite_count,
        has_ricercato_badge=has_ricercato_badge,
    )

    return {
        "source": "vinted",
        "search_term": search_term,
        "tag": "ricercato" if has_ricercato_badge else "",
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
        "favorite_count": favorite_count,
        "evaluation_label": evaluation_label,
        "secondary_badge_text": secondary_badge_text,
        "has_ricercato_badge": has_ricercato_badge,
        "raw_text": raw_text,
        "extracted_at": datetime.now().isoformat(timespec="seconds"),
    }


def _prioritize_vinted_rows(rows) -> list[dict]:
    return [
        row
        for _, row in sorted(
            enumerate(rows),
            key=lambda item: (
                _vinted_priority_rank(item[1]),
                -(parse_vinted_favorite_count(item[1].get("favorite_count")) or 0),
                item[0],
            ),
        )
    ]


def build_vinted_search_url(search: str) -> str:
    value = str(search or "").strip()
    if value.lower().startswith(("http://", "https://")):
        return value
    return f"{VINTED_BASE_URL}/catalog?search_text={quote_plus(value)}"


def extract_vinted_search_term(url: str) -> str:
    values = parse_qs(urlsplit(str(url or "")).query).get("search_text", [])
    return str(values[0] if values else "").strip()


def extract_vinted_page_number(url: str) -> int:
    values = parse_qs(urlsplit(str(url or "")).query).get("page", [])
    try:
        page_number = int(values[0]) if values else 1
    except (TypeError, ValueError):
        return 1
    return page_number if page_number > 0 else 1


def build_vinted_page_url(url: str, page_number: int) -> str:
    normalized_page = max(int(page_number or 1), 1)
    parsed = urlsplit(str(url or "").strip() or f"{VINTED_BASE_URL}/catalog")
    query = parse_qs(parsed.query, keep_blank_values=True)
    if normalized_page <= 1:
        query.pop("page", None)
    else:
        query["page"] = [str(normalized_page)]
    return urlunsplit(
        (
            parsed.scheme or "https",
            parsed.netloc or urlsplit(VINTED_BASE_URL).netloc,
            parsed.path or "/catalog",
            urlencode(query, doseq=True),
            "",
        )
    )


def normalize_vinted_item_url(url: str) -> str:
    parsed = urlsplit(str(url or "").strip())
    if not parsed.netloc:
        parsed = urlsplit(f"{VINTED_BASE_URL}/{str(url or '').lstrip('/')}")
    return urlunsplit((parsed.scheme or "https", parsed.netloc, parsed.path, "", ""))


def _read_vinted_next_page_url(driver: Driver, next_page_number: int) -> str:
    payload = driver.run_js(
        f"""
const pageNumber = {max(int(next_page_number or 1), 1)};
window.scrollTo(0, document.documentElement.scrollHeight);
const selector = `[data-testid="catalog-pagination--page-${{pageNumber}}"]`;
const node = document.querySelector(selector)
  || [...document.querySelectorAll('a[href*="/catalog"]')].find((link) => {{
    try {{
      const target = new URL(link.href || link.getAttribute('href') || '', window.location.href);
      return target.searchParams.get('page') === String(pageNumber);
    }} catch (_error) {{
      return false;
    }}
  }});
return node ? (node.href || node.getAttribute('href') || '') : '';
        """
    )
    next_url = str(payload or "").strip()
    if not next_url:
        return ""
    parsed = urlsplit(next_url)
    if parsed.netloc:
        return urlunsplit((parsed.scheme or "https", parsed.netloc, parsed.path, parsed.query, ""))
    return build_vinted_page_url(f"{VINTED_BASE_URL}{next_url if next_url.startswith('/') else '/' + next_url}", next_page_number)


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


def parse_vinted_favorite_count(value: object) -> int | None:
    if isinstance(value, int):
        return value if value >= 0 else None
    text = normalize_whitespace(str(value or ""))
    if not text:
        return None
    match = FAVORITE_COUNT_PATTERN.search(text.replace(".", "").replace(" ", ""))
    if not match:
        return None
    try:
        parsed = int(match.group(0))
    except ValueError:
        return None
    return parsed if parsed >= 0 else None


def classify_vinted_evaluation(favorite_count: int | None, has_ricercato_badge: bool) -> str:
    if favorite_count is None or favorite_count <= FAVORITE_COUNT_REVIEW_THRESHOLD:
        return ""
    if has_ricercato_badge:
        return "da valutare assolutamente"
    return "da valutare"


def _vinted_priority_rank(row: dict) -> int:
    evaluation_label = str(row.get("evaluation_label", "") or "").strip().lower()
    if evaluation_label == "da valutare assolutamente":
        return 0
    if evaluation_label == "da valutare":
        return 1
    if row.get("has_ricercato_badge"):
        return 2
    return 3


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
