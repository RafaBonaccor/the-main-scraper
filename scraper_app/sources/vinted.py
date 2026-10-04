import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, quote_plus, urlencode, urljoin, urlsplit, urlunsplit

from botasaurus.browser import Driver, Wait, browser

from ..browser_helpers import DEFAULT_COOKIE_REJECT_TEXTS, click_first_matching_text, current_page_url
from ..chrome_reuse import preferred_host_fragment_for_url, try_reuse_running_chrome
from ..discord_notifications import build_vinted_deal_discord_message, send_discord_webhook_message
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
from ..vinted_database import (
    DEFAULT_VINTED_DB_PATH,
    build_vinted_item_identity_keys,
    load_vinted_completed_detail_rows,
    load_vinted_known_item_keys,
    load_vinted_notified_deal_keys,
    save_vinted_deal_notifications,
    save_vinted_rows,
)
from ..vinted_access import emit_vinted_access_signal, read_vinted_access_status, wait_for_vinted_access_status
from ..vinted_deals import (
    VINTED_DEAL_HUNTER_DEFAULT_MAX_AGE_HOURS,
    annotate_vinted_deal_hunter_row,
    normalize_vinted_deal_hunter_max_age_hours,
    normalize_vinted_deal_hunter_min_favorites,
    normalize_vinted_deal_hunter_min_price,
    vinted_deal_hunter_enabled,
)


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
VINTED_HIGH_SHIPPING_THRESHOLD = 2.99
VINTED_PAGE_NOT_FOUND_PATTERN = re.compile(r"\b(page not found|pagina non trovata)\b", re.IGNORECASE)
VINTED_NAVIGATION_TIMEOUT_SECONDS = 15
VINTED_NAVIGATION_WAIT = Wait.SHORT
VINTED_DETAIL_ITEM_TIMEOUT_SECONDS = 60


def run_vinted_scraper(
    search: str,
    max_results: int = 100,
    min_price: float | None = None,
    max_price: float | None = None,
    deal_hunter_min_favorites: int = 0,
    deal_hunter_max_age_hours: float = VINTED_DEAL_HUNTER_DEFAULT_MAX_AGE_HOURS,
    exclude_known_items: bool = True,
    auto_submit_offers: bool = True,
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
    detach_browser_on_complete: bool = True,
    discord_deal_notifications: bool = False,
    discord_webhook_url: str = "",
    deal_hunter_url_only_discord: bool = False,
) -> ScrapeOutcome:
    search_url = build_vinted_search_url(search)
    search_term = extract_vinted_search_term(search_url) or str(search or "").strip()
    normalized_deal_hunter_min_favorites = normalize_vinted_deal_hunter_min_favorites(deal_hunter_min_favorites, default=0)
    normalized_deal_hunter_max_age_hours = normalize_vinted_deal_hunter_max_age_hours(
        deal_hunter_max_age_hours,
        default=VINTED_DEAL_HUNTER_DEFAULT_MAX_AGE_HOURS,
    )
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
        "min_price": min_price if min_price is None else max(float(min_price), 0.0),
        "max_price": max_price if max_price is None else max(float(max_price), 0.0),
        "deal_hunter_min_favorites": normalized_deal_hunter_min_favorites,
        "deal_hunter_max_age_hours": normalized_deal_hunter_max_age_hours,
        "deal_hunter_enabled": vinted_deal_hunter_enabled(
            normalized_deal_hunter_min_favorites,
            normalized_deal_hunter_max_age_hours,
        ),
        "exclude_known_items": bool(exclude_known_items),
        "auto_submit_offers": bool(auto_submit_offers),
        "db_path": db_path,
        "ui_result_json": ui_result_json,
        "browser_mode": browser_mode,
        "browser_user_data_dir": browser_user_data_dir,
        "browser_profile_directory": browser_profile_directory,
        "keep_browser_open": bool(keep_browser_open),
        "refresh_browser_profile": bool(refresh_browser_profile),
        "keep_open_seconds": max(int(keep_open_seconds or 0), 0),
        "detach_browser_on_complete": bool(detach_browser_on_complete),
        "slow_mode": bool(slow_mode),
        "action_delay_seconds": action_delay,
        "page_settle_seconds": page_settle,
        "discord_deal_notifications": bool(discord_deal_notifications),
        "discord_webhook_url": str(discord_webhook_url or "").strip(),
        "deal_hunter_url_only_discord": bool(deal_hunter_url_only_discord),
    }
    payload = _scrape_vinted_task(config, reuse_driver=bool(keep_browser_open))
    if not payload["meta"].get("db_saved_live"):
        db_meta = save_vinted_rows(payload["rows"], db_path=db_path, run_kind="search")
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
        db_meta = save_vinted_rows(payload["rows"], db_path=db_path, run_kind="details")
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
    driver.get(search_url, wait=VINTED_NAVIGATION_WAIT, timeout=VINTED_NAVIGATION_TIMEOUT_SECONDS)
    _wait_for_vinted_catalog_page_ready(
        driver,
        max_wait_seconds=float(config.get("page_settle_seconds", 3.0) or 0),
    )
    cookie_action = click_first_matching_text(driver, DEFAULT_COOKIE_REJECT_TEXTS)
    if cookie_action:
        time.sleep(min(float(config.get("action_delay_seconds", 1.5) or 0), 0.35))
    access_status = wait_for_vinted_access_status(
        driver,
        max_wait_seconds=min(max(float(config.get("page_settle_seconds", 3.0) or 0), 0.0), 0.8),
    )
    if not bool(config.get("deal_hunter_url_only_discord", False)):
        emit_vinted_access_signal(access_status)
    if not bool(config.get("deal_hunter_url_only_discord", False)):
        access_status = _wait_for_vinted_login_if_needed(
            driver,
            access_status,
            revisit_url=search_url,
            action_delay_seconds=float(config.get("action_delay_seconds", 1.5) or 0),
            page_settle_seconds=float(config.get("page_settle_seconds", 3.0) or 0),
        )

    _wait_for_vinted_catalog_cards(
        driver,
        max_wait_seconds=float(config.get("page_settle_seconds", 3.0) or 0),
    )
    rows_by_link: dict[str, dict] = {}
    max_results = int(config.get("max_results", 100) or 0)
    max_price = _normalize_vinted_max_price(config.get("max_price"))
    min_price = normalize_vinted_deal_hunter_min_price(config.get("min_price"))
    exclude_known_items = bool(config.get("exclude_known_items", True))
    known_item_keys = (
        load_vinted_known_item_keys(str(config.get("db_path", "") or DEFAULT_VINTED_DB_PATH))
        if exclude_known_items
        else set()
    )
    filtered_out_by_price = 0
    filtered_out_known_items = 0
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
                deal_hunter_min_favorites=int(config.get("deal_hunter_min_favorites", 0) or 0),
                deal_hunter_max_age_hours=float(
                    config.get("deal_hunter_max_age_hours", VINTED_DEAL_HUNTER_DEFAULT_MAX_AGE_HOURS) or 0
                ),
            )
            if not row["link"]:
                continue
            if exclude_known_items and _vinted_row_matches_known_item_keys(row, known_item_keys):
                filtered_out_known_items += 1
                continue
            if not _vinted_row_matches_max_price(row, max_price):
                filtered_out_by_price += 1
                continue
            rows_by_link[row["link"]] = row
            _persist_vinted_progress_results(
                rows=rows_by_link.values(),
                config=config,
                search_url=search_url,
                pages_visited=pages_visited,
                filtered_out_known_items=filtered_out_known_items,
                filtered_out_by_price=filtered_out_by_price,
                cookie_action=cookie_action or "",
                access_status=access_status,
                enrichment_meta={"enriched_count": 0, "demoted_count": 0},
                live_stage="catalog",
            )

        if max_results > 0 and len(rows_by_link) >= max_results:
            break

        next_page = current_page + 1
        next_page_url = _open_vinted_next_page(
            driver,
            next_page,
            page_settle_seconds=float(config.get("page_settle_seconds", 3.0) or 0),
        )
        if not next_page_url:
            break
        _wait_for_vinted_catalog_cards(
            driver,
            max_wait_seconds=float(config.get("page_settle_seconds", 3.0) or 0),
        )
        page_cookie_action = click_first_matching_text(driver, DEFAULT_COOKIE_REJECT_TEXTS)
        if page_cookie_action:
            time.sleep(min(float(config.get("action_delay_seconds", 1.5) or 0), 0.35))
            cookie_action = page_cookie_action
        access_status = wait_for_vinted_access_status(
            driver,
            max_wait_seconds=min(max(float(config.get("page_settle_seconds", 3.0) or 0), 0.0), 0.8),
        )
        if not bool(config.get("deal_hunter_url_only_discord", False)):
            emit_vinted_access_signal(access_status)
        if not bool(config.get("deal_hunter_url_only_discord", False)):
            access_status = _wait_for_vinted_login_if_needed(
                driver,
                access_status,
                revisit_url=next_page_url,
                action_delay_seconds=float(config.get("action_delay_seconds", 1.5) or 0),
                page_settle_seconds=float(config.get("page_settle_seconds", 3.0) or 0),
            )
        _wait_for_vinted_catalog_cards(
            driver,
            max_wait_seconds=float(config.get("page_settle_seconds", 3.0) or 0),
        )

    rows = _prioritize_vinted_rows(rows_by_link.values())
    if max_results > 0:
        rows = rows[:max_results]
    enrichment_meta = _enrich_vinted_priority_rows(driver, rows, config)
    rows = [
        annotate_vinted_deal_hunter_row(
            row,
            min_favorites=int(config.get("deal_hunter_min_favorites", 0) or 0),
            max_age_hours=float(config.get("deal_hunter_max_age_hours", VINTED_DEAL_HUNTER_DEFAULT_MAX_AGE_HOURS) or 0),
            min_price=min_price,
            max_price=max_price,
        )
        for row in rows
    ]
    deal_hunter_candidates = sum(1 for row in rows if bool(row.get("deal_hunter_candidate")))
    deal_hunter_matches = sum(1 for row in rows if bool(row.get("deal_hunter_match")))
    detail_cache_rows = [
        row
        for row in rows
        if str(row.get("description", "") or "").strip()
        and (str(row.get("price", "") or "").strip() or row.get("price_value") not in ("", None))
    ]
    if detail_cache_rows:
        save_vinted_rows(
            detail_cache_rows,
            db_path=str(config.get("db_path", "") or DEFAULT_VINTED_DB_PATH),
            run_kind="details",
        )
    if bool(config.get("deal_hunter_enabled", False)):
        if bool(config.get("deal_hunter_url_only_discord", False)):
            rows = [row for row in rows if bool(row.get("deal_hunter_candidate"))]
        else:
            rows = [row for row in rows if bool(row.get("deal_hunter_match"))]
    rows = [row for row in rows if _vinted_row_matches_max_price(row, max_price)]
    rows = [row for row in rows if _vinted_row_matches_min_price_for_deal_hunter(row, min_price)]
    rows = _prioritize_vinted_rows(rows)
    notification_meta = _notify_new_vinted_deals(rows, config)
    keep_open_seconds = int(config.get("keep_open_seconds", 0) or 0)
    keep_browser_open = bool(config.get("keep_browser_open", False))
    meta = {
        "search": config["search"],
        "search_term": config["search_term"],
        "search_count": 1,
        "tag": "",
        "search_url": search_url,
        "max_results": max_results,
        "min_price": min_price,
        "max_price": max_price,
        "deal_hunter_enabled": bool(config.get("deal_hunter_enabled", False)),
        "deal_hunter_min_favorites": int(config.get("deal_hunter_min_favorites", 0) or 0),
        "deal_hunter_max_age_hours": float(
            config.get("deal_hunter_max_age_hours", VINTED_DEAL_HUNTER_DEFAULT_MAX_AGE_HOURS) or 0
        ),
        "deal_hunter_candidates": deal_hunter_candidates,
        "deal_hunter_matches": deal_hunter_matches,
        "deal_hunter_url_only_discord": bool(config.get("deal_hunter_url_only_discord", False)),
        "exclude_known_items": exclude_known_items,
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
        "filtered_out_known_items": filtered_out_known_items,
        "filtered_out_by_price": filtered_out_by_price,
        "row_count": len(rows),
        "priority_rows_enriched": enrichment_meta["enriched_count"],
        "priority_rows_demoted_by_age": enrichment_meta["demoted_count"],
        "priority_rows_cached": enrichment_meta.get("cached_count", 0),
        **notification_meta,
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


def _notify_new_vinted_deals(rows: list[dict], config: dict) -> dict[str, int | bool | str]:
    if not bool(config.get("discord_deal_notifications", False)):
        return {
            "discord_deal_notifications": False,
            "discord_deal_notifications_sent": 0,
            "discord_deal_notifications_skipped": 0,
            "discord_deal_notifications_failed": 0,
            "discord_deal_notifications_last_error": "",
        }
    webhook_url = str(config.get("discord_webhook_url", "") or "").strip()
    if not webhook_url:
        return {
            "discord_deal_notifications": False,
            "discord_deal_notifications_sent": 0,
            "discord_deal_notifications_skipped": 0,
            "discord_deal_notifications_failed": 0,
            "discord_deal_notifications_last_error": "webhook assente",
        }

    if bool(config.get("deal_hunter_url_only_discord", False)):
        candidate_rows = [
            row
            for row in rows
            if bool(row.get("deal_hunter_candidate")) and str(row.get("link", "") or "").strip()
        ]
    else:
        candidate_rows = [
            row
            for row in rows
            if bool(row.get("deal_hunter_match")) and str(row.get("link", "") or "").strip()
        ]
    if not candidate_rows:
        return {
            "discord_deal_notifications": True,
            "discord_deal_notifications_sent": 0,
            "discord_deal_notifications_skipped": 0,
            "discord_deal_notifications_failed": 0,
            "discord_deal_notifications_last_error": "",
        }

    notified_keys = load_vinted_notified_deal_keys(
        str(config.get("db_path", "") or DEFAULT_VINTED_DB_PATH),
        webhook_target=webhook_url,
    )
    pending_rows: list[dict] = []
    skipped = 0
    for row in candidate_rows:
        identity_keys = build_vinted_item_identity_keys(item_id=row.get("item_id", ""), link=row.get("link", ""))
        if any(key in notified_keys for key in identity_keys):
            skipped += 1
            continue
        pending_rows.append(row)

    sent_rows: list[dict] = []
    failed = 0
    last_error = ""
    for row in pending_rows:
        message_row = dict(row)
        if bool(config.get("deal_hunter_url_only_discord", False)):
            message_row["deal_hunter_url_only_discord"] = True
        result = send_discord_webhook_message(
            webhook_url,
            build_vinted_deal_discord_message(message_row),
            embeds=_build_vinted_deal_discord_embeds(message_row),
        )
        if bool(result.get("ok")):
            annotated = dict(row)
            annotated["notification_sent_at"] = str(result.get("sent_at", "") or "")
            sent_rows.append(annotated)
            continue
        failed += 1
        last_error = str(result.get("error", "") or "")
        print(f"[discord-notify] invio fallito per {row.get('link', '')}: {last_error}", flush=True)

    if sent_rows:
        save_vinted_deal_notifications(
            sent_rows,
            db_path=str(config.get("db_path", "") or DEFAULT_VINTED_DB_PATH),
            webhook_target=webhook_url,
        )

    return {
        "discord_deal_notifications": True,
        "discord_deal_notifications_sent": len(sent_rows),
        "discord_deal_notifications_skipped": skipped,
        "discord_deal_notifications_failed": failed,
        "discord_deal_notifications_last_error": last_error,
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
    cached_detail_rows = load_vinted_completed_detail_rows(
        str(config.get("db_path", "") or DEFAULT_VINTED_DB_PATH)
    )
    cached_detail_count = 0
    detail_timeout_seconds = max(int(config.get("detail_item_timeout_seconds", VINTED_DETAIL_ITEM_TIMEOUT_SECONDS) or 0), 1)

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
        base_row = item if isinstance(item, dict) else {}
        cached_row = _find_cached_vinted_detail_row(base_row, current_link, cached_detail_rows)
        if cached_row is not None:
            rows.append(
                _merge_cached_vinted_detail_row(
                    base_row=base_row,
                    cached_row=cached_row,
                    current_link=current_link,
                    search_term=search_term,
                    search_url=search_url,
                    tag=tag,
                    item_name=item_name,
                    config=config,
                )
            )
            cached_detail_count += 1
            continue
        detail_started_at = time.monotonic()
        try:
            driver.get(current_link, wait=VINTED_NAVIGATION_WAIT, timeout=VINTED_NAVIGATION_TIMEOUT_SECONDS)
            _wait_for_vinted_detail_page_ready(
                driver,
                max_wait_seconds=float(config.get("page_settle_seconds", 3.0) or 0),
            )
            cookie_action = click_first_matching_text(driver, DEFAULT_COOKIE_REJECT_TEXTS)
            if cookie_action:
                time.sleep(min(float(config.get("action_delay_seconds", 1.5) or 0), 0.35))
            last_access_status = wait_for_vinted_access_status(
                driver,
                max_wait_seconds=min(max(float(config.get("page_settle_seconds", 3.0) or 0), 0.0), 0.8),
            )
            emit_vinted_access_signal(last_access_status)
            last_access_status = _wait_for_vinted_login_if_needed(
                driver,
                last_access_status,
                revisit_url=current_link,
                action_delay_seconds=float(config.get("action_delay_seconds", 1.5) or 0),
                page_settle_seconds=float(config.get("page_settle_seconds", 3.0) or 0),
            )
            if time.monotonic() - detail_started_at > detail_timeout_seconds:
                raise TimeoutError(f"detail timeout > {detail_timeout_seconds}s")
            row = _build_vinted_detail_row(
                driver=driver,
                current_link=current_link,
                search_term=search_term,
                search_url=search_url,
                tag=tag,
                item_name=item_name,
                base_row=base_row,
                deal_hunter_min_favorites=int(config.get("deal_hunter_min_favorites", 0) or 0),
                deal_hunter_max_age_hours=float(
                    config.get("deal_hunter_max_age_hours", VINTED_DEAL_HUNTER_DEFAULT_MAX_AGE_HOURS) or 0
                ),
            )
            if time.monotonic() - detail_started_at > detail_timeout_seconds:
                raise TimeoutError(f"detail timeout > {detail_timeout_seconds}s")
        except Exception as exc:
            print(f"[vinted-detail] timeout/errore, salto {current_link}: {exc}", flush=True)
            row = _build_vinted_missing_detail_row(
                current_link=current_link,
                search_term=search_term,
                search_url=search_url,
                tag=tag,
                item_name=item_name,
                base_row=base_row,
                page_text=str(base_row.get("raw_text", "") or ""),
                detail_error="detail_timeout",
            )
        if str(row.get("detail_error", "") or "").strip() == "page_not_found":
            print(f"[vinted-detail] pagina non trovata, salto {current_link}", flush=True)
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
        "cached_detail_count": cached_detail_count,
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
const imageSrc = image ? (
  image.currentSrc ||
  image.src ||
  image.getAttribute('src') ||
  image.getAttribute('data-src') ||
  ((image.getAttribute('srcset') || '').split(',')[0] || '').trim().split(/\\s+/)[0] ||
  ''
) : '';
const secondaryBadge = root.querySelector('[data-testid*="secondary-badge--content"], [data-testid*="secondary-badge"]');
const favouriteCount = root.querySelector('[data-testid="favourite-count-text"]');
const secondaryBadgeText = clean(secondaryBadge ? (secondaryBadge.innerText || secondaryBadge.textContent) : '');
  return {
    link: link.href || link.getAttribute('href') || '',
    title: clean(title ? (title.innerText || title.textContent) : ''),
    price: clean(price ? (price.innerText || price.textContent) : ''),
    image_url: imageSrc,
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


def _read_vinted_detail_payload(driver: Driver) -> dict[str, object]:
    payload = driver.run_js(
        """
const clean = (value) => (value || '').replace(/\\s+/g, ' ').trim();
const bodyText = clean(document.body ? (document.body.innerText || document.body.textContent || '') : '');
const title = document.querySelector('h1');
const price = document.querySelector('[data-testid*="price-text"], [data-testid*="item-price"], span[aria-label*="€"]');
const offer = [...document.querySelectorAll('button, a, span, div')].find((node) => {
  const text = clean(node.innerText || node.textContent || '');
  return text && text.length <= 40 && /offerta/i.test(text) && /fare|fai|invia/i.test(text);
});
let shippingText = '';
for (const node of document.querySelectorAll('[data-testid], button, span, div, p, li')) {
  const text = clean(node.innerText || node.textContent || '');
  if (!text) continue;
  if (/spedizione|consegna/i.test(text) && /\\d/.test(text)) {
    shippingText = text;
    break;
  }
}
let publishedText = '';
for (const node of document.querySelectorAll('span, div, p, li')) {
  const text = clean(node.innerText || node.textContent || '');
  if (!text) continue;
  if (/^(\\d+\\s+\\w+\\s+fa)$/i.test(text)) {
    publishedText = text;
    break;
  }
  if (/^Caricato\\s+/i.test(text)) {
    publishedText = clean(text.replace(/^Caricato\\s+/i, ''));
    break;
  }
}
return {
  readyState: document.readyState || '',
  bodyText,
  bodyLength: bodyText.length,
  pageNotFound: /page not found|pagina non trovata/i.test(bodyText),
  title: clean(title ? (title.innerText || title.textContent || '') : ''),
  rawPriceText: clean(price ? (price.innerText || price.textContent || '') : ''),
  shippingText,
  offerText: clean(offer ? (offer.innerText || offer.textContent || '') : ''),
  publishedText,
};
        """
    )
    if not isinstance(payload, dict):
        return {}
    return payload


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


def _read_vinted_favourite_button_state(driver: Driver) -> dict[str, object]:
    payload = driver.run_js(
        """
const clean = (value) => (value || '').replace(/\\s+/g, ' ').trim();
const button = document.querySelector('[data-testid="favourite-button"]');
if (!button) {
  return { found: false, pressed: false, label: '', count: '' };
}
const countNode = button.querySelector('[data-testid="favourite-count-text"]');
return {
  found: true,
  pressed: String(button.getAttribute('aria-pressed') || '').toLowerCase() === 'true',
  label: clean(button.getAttribute('aria-label') || ''),
  count: clean(countNode ? (countNode.innerText || countNode.textContent || '') : ''),
};
        """
    )
    if not isinstance(payload, dict):
        return {"found": False, "pressed": False, "label": "", "count": ""}
    return {
        "found": bool(payload.get("found", False)),
        "pressed": bool(payload.get("pressed", False)),
        "label": normalize_whitespace(str(payload.get("label", "") or "")),
        "count": normalize_whitespace(str(payload.get("count", "") or "")),
    }


def _click_vinted_favourite_button(
    driver: Driver,
    action_delay_seconds: float,
    confirm_wait_seconds: float = 1.5,
) -> dict[str, object]:
    before = _read_vinted_favourite_button_state(driver)
    if not before.get("found"):
        return {
            "favorite_button_found": False,
            "favorite_clicked": False,
            "favorite_action": "button_not_found",
            "favorite_label": "",
            "favorite_count_text": "",
        }
    if before.get("pressed"):
        return {
            "favorite_button_found": True,
            "favorite_clicked": False,
            "favorite_action": "already_liked",
            "favorite_label": str(before.get("label", "") or ""),
            "favorite_count_text": str(before.get("count", "") or ""),
        }
    clicked = driver.run_js(
        """
const button = document.querySelector('[data-testid="favourite-button"]');
if (!button) return false;
try {
  button.scrollIntoView({ block: 'center', inline: 'center' });
} catch (_error) {}
button.click();
button.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
return true;
        """
    )
    if not bool(clicked):
        return {
            "favorite_button_found": True,
            "favorite_clicked": False,
            "favorite_action": "click_failed",
            "favorite_label": str(before.get("label", "") or ""),
            "favorite_count_text": str(before.get("count", "") or ""),
        }
    time.sleep(min(max(float(action_delay_seconds or 0), 0.0), 0.35))
    deadline = time.monotonic() + max(_nonnegative_float(confirm_wait_seconds, 1.5), 0.1)
    after = _read_vinted_favourite_button_state(driver)
    while not bool(after.get("pressed")) and time.monotonic() < deadline:
        time.sleep(0.1)
        after = _read_vinted_favourite_button_state(driver)
    return {
        "favorite_button_found": True,
        "favorite_clicked": bool(after.get("pressed")),
        "favorite_action": "clicked" if bool(after.get("pressed")) else "click_unconfirmed",
        "favorite_label": str(after.get("label", "") or str(before.get("label", "") or "")),
        "favorite_count_text": str(after.get("count", "") or str(before.get("count", "") or "")),
    }


def _like_vinted_deal_if_needed(
    driver: Driver,
    row: dict,
    *,
    current_link: str,
    config: dict,
    page_already_open: bool,
) -> dict[str, object]:
    if not bool(row.get("deal_hunter_match")):
        return {
            "favorite_button_found": False,
            "favorite_clicked": False,
            "favorite_action": "skipped_not_deal",
        }
    target_url = normalize_vinted_item_url(current_link or str(row.get("link", "") or ""))
    if not target_url:
        return {
            "favorite_button_found": False,
            "favorite_clicked": False,
            "favorite_action": "skipped_missing_link",
        }
    try:
        current_url = normalize_vinted_item_url(str(current_page_url(driver) or ""))
    except Exception:
        current_url = ""
    try:
        if not page_already_open or current_url != target_url:
            driver.get(target_url, wait=VINTED_NAVIGATION_WAIT, timeout=VINTED_NAVIGATION_TIMEOUT_SECONDS)
            _wait_for_vinted_detail_page_ready(
                driver,
                max_wait_seconds=float(config.get("page_settle_seconds", 3.0) or 0),
            )
            cookie_action = click_first_matching_text(driver, DEFAULT_COOKIE_REJECT_TEXTS)
            if cookie_action:
                time.sleep(min(float(config.get("action_delay_seconds", 1.5) or 0), 0.35))
            access_status = wait_for_vinted_access_status(
                driver,
                max_wait_seconds=min(max(float(config.get("page_settle_seconds", 3.0) or 0), 0.0), 0.8),
            )
            emit_vinted_access_signal(access_status)
            _wait_for_vinted_login_if_needed(
                driver,
                access_status,
                revisit_url=target_url,
                action_delay_seconds=float(config.get("action_delay_seconds", 1.5) or 0),
                page_settle_seconds=float(config.get("page_settle_seconds", 3.0) or 0),
            )
        result = _click_vinted_favourite_button(
            driver,
            action_delay_seconds=float(config.get("action_delay_seconds", 1.5) or 0),
        )
        if bool(result.get("favorite_clicked")):
            print(f"[vinted-like] preferito aggiunto per {target_url}", flush=True)
        return result
    except Exception as exc:
        print(f"[vinted-like] impossibile aggiungere ai preferiti {target_url}: {exc}", flush=True)
        return {
            "favorite_button_found": False,
            "favorite_clicked": False,
            "favorite_action": "error",
            "favorite_error": normalize_whitespace(str(exc or ""))[:240],
        }


def _read_vinted_published_text(driver: Driver, page_text: str = "") -> str:
    payload = driver.run_js(
        """
const clean = (value) => (value || '').replace(/\\s+/g, ' ').trim();
const nodes = [...document.querySelectorAll('span, div, p, li')];
for (const node of nodes) {
  const text = clean(node.innerText || node.textContent || '');
  if (!text) continue;
  if (/^(\\d+\\s+\\w+\\s+fa)$/i.test(text)) return text;
  if (/^Caricato\\s+/i.test(text)) return clean(text.replace(/^Caricato\\s+/i, ''));
}
return '';
        """
    )
    extracted = normalize_whitespace(str(payload or ""))
    if extracted:
        return extracted
    text = normalize_whitespace(str(page_text or ""))
    if not text:
        return ""
    prefixed_match = re.search(r"Caricato\s+(\d+\s+\w+\s+fa)", text, re.IGNORECASE)
    if prefixed_match:
        return normalize_whitespace(prefixed_match.group(1))
    relative_match = re.search(r"\b(\d+\s+\w+\s+fa)\b", text, re.IGNORECASE)
    if relative_match:
        return normalize_whitespace(relative_match.group(1))
    return ""


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


def _enrich_vinted_priority_rows(driver: Driver, rows: list[dict], config: dict) -> dict[str, int]:
    if bool(config.get("deal_hunter_url_only_discord", False)):
        return {"enriched_count": 0, "demoted_count": 0, "cached_count": 0}
    targets: list[dict] = []
    seen_links: set[str] = set()
    deal_hunter_mode = bool(config.get("deal_hunter_enabled", False))
    for row in rows:
        current_link = normalize_vinted_item_url(str(row.get("link", "") or ""))
        if not current_link or current_link in seen_links:
            continue
        should_extract = (
            _should_extract_vinted_deal_hunter_row(row, config)
            if deal_hunter_mode
            else (_should_extract_vinted_priority_row(row) or _should_extract_vinted_deal_hunter_row(row, config))
        )
        if not should_extract:
            continue
        seen_links.add(current_link)
        targets.append(row)
    if not targets:
        return {"enriched_count": 0, "demoted_count": 0, "cached_count": 0}
    demoted_count = 0
    cached_count = 0
    cached_detail_rows = load_vinted_completed_detail_rows(
        str(config.get("db_path", "") or DEFAULT_VINTED_DB_PATH)
    )
    detail_timeout_seconds = max(int(config.get("detail_item_timeout_seconds", VINTED_DETAIL_ITEM_TIMEOUT_SECONDS) or 0), 1)
    for row in targets:
        row["min_price"] = config.get("min_price")
        current_link = normalize_vinted_item_url(str(row.get("link", "") or ""))
        if not current_link:
            continue
        cached_row = _find_cached_vinted_detail_row(row, current_link, cached_detail_rows)
        if cached_row is not None:
            previous_label = str(row.get("evaluation_label", "") or "").strip().lower()
            row.update(
                _merge_cached_vinted_detail_row(
                    base_row=row,
                    cached_row=cached_row,
                    current_link=current_link,
                    search_term=str(row.get("search_term", "") or ""),
                    search_url=str(row.get("search_url", "") or ""),
                    tag=str(row.get("tag", "") or ""),
                    item_name=str(row.get("name", "") or ""),
                    config=config,
                )
            )
            current_label = str(row.get("evaluation_label", "") or "").strip().lower()
            if previous_label == "da valutare assolutamente" and current_label == "da valutare":
                demoted_count += 1
            cached_count += 1
            _persist_vinted_progress_results(
                rows=rows,
                config=config,
                search_url=str(row.get("search_url", "") or config.get("search_url", "")),
                pages_visited=[],
                filtered_out_known_items=0,
                filtered_out_by_price=0,
                cookie_action="",
                access_status={},
                enrichment_meta={"enriched_count": len(targets), "demoted_count": demoted_count, "cached_count": cached_count},
                live_stage="detail_cached",
            )
            continue
        detail_started_at = time.monotonic()
        try:
            driver.get(current_link, wait=VINTED_NAVIGATION_WAIT, timeout=VINTED_NAVIGATION_TIMEOUT_SECONDS)
            _wait_for_vinted_detail_page_ready(
                driver,
                max_wait_seconds=float(config.get("page_settle_seconds", 3.0) or 0),
            )
            cookie_action = click_first_matching_text(driver, DEFAULT_COOKIE_REJECT_TEXTS)
            if cookie_action:
                time.sleep(min(float(config.get("action_delay_seconds", 1.5) or 0), 0.35))
            access_status = wait_for_vinted_access_status(
                driver,
                max_wait_seconds=min(max(float(config.get("page_settle_seconds", 3.0) or 0), 0.0), 0.8),
            )
            emit_vinted_access_signal(access_status)
            _wait_for_vinted_login_if_needed(
                driver,
                access_status,
                revisit_url=current_link,
                action_delay_seconds=float(config.get("action_delay_seconds", 1.5) or 0),
                page_settle_seconds=float(config.get("page_settle_seconds", 3.0) or 0),
            )
            if time.monotonic() - detail_started_at > detail_timeout_seconds:
                raise TimeoutError(f"detail timeout > {detail_timeout_seconds}s")
            previous_label = str(row.get("evaluation_label", "") or "").strip().lower()
            enriched_row = _build_vinted_detail_row(
                driver=driver,
                current_link=current_link,
                search_term=str(row.get("search_term", "") or ""),
                search_url=str(row.get("search_url", "") or ""),
                tag=str(row.get("tag", "") or ""),
                item_name=str(row.get("name", "") or ""),
                base_row=row,
                deal_hunter_min_favorites=int(config.get("deal_hunter_min_favorites", 0) or 0),
                deal_hunter_max_age_hours=float(
                    config.get("deal_hunter_max_age_hours", VINTED_DEAL_HUNTER_DEFAULT_MAX_AGE_HOURS) or 0
                ),
            )
            if time.monotonic() - detail_started_at > detail_timeout_seconds:
                raise TimeoutError(f"detail timeout > {detail_timeout_seconds}s")
        except Exception as exc:
            print(f"[vinted-detail] timeout/errore, salto {current_link}: {exc}", flush=True)
            previous_label = str(row.get("evaluation_label", "") or "").strip().lower()
            access_status = {}
            enriched_row = _build_vinted_missing_detail_row(
                current_link=current_link,
                search_term=str(row.get("search_term", "") or ""),
                search_url=str(row.get("search_url", "") or ""),
                tag=str(row.get("tag", "") or ""),
                item_name=str(row.get("name", "") or ""),
                base_row=row,
                page_text=str(row.get("raw_text", "") or ""),
                detail_error="detail_timeout",
            )
        if str(enriched_row.get("detail_error", "") or "").strip() == "page_not_found":
            print(f"[vinted-detail] pagina non trovata, salto {current_link}", flush=True)
        row.update(enriched_row)
        if str(enriched_row.get("detail_error", "") or "").strip() == "":
            row.update(
                _like_vinted_deal_if_needed(
                    driver,
                    row,
                    current_link=current_link,
                    config=config,
                    page_already_open=True,
                )
            )
        current_label = str(row.get("evaluation_label", "") or "").strip().lower()
        if previous_label == "da valutare assolutamente" and current_label == "da valutare":
            demoted_count += 1
        _persist_vinted_progress_results(
            rows=rows,
            config=config,
            search_url=str(row.get("search_url", "") or config.get("search_url", "")),
            pages_visited=[],
            filtered_out_known_items=0,
            filtered_out_by_price=0,
            cookie_action="",
            access_status=access_status,
            enrichment_meta={"enriched_count": len(targets), "demoted_count": demoted_count},
            live_stage="detail",
        )
    return {"enriched_count": len(targets), "demoted_count": demoted_count, "cached_count": cached_count}


def _should_extract_vinted_priority_row(row: dict) -> bool:
    return str(row.get("evaluation_label", "") or "").strip().lower() == "da valutare assolutamente"


def _should_extract_vinted_deal_hunter_row(row: dict, config: dict) -> bool:
    if not bool(config.get("deal_hunter_enabled", False)):
        return False
    return bool(row.get("deal_hunter_candidate")) and not bool(row.get("deal_hunter_match"))


def _find_cached_vinted_detail_row(row: dict, current_link: str, cached_detail_rows: dict[str, dict]) -> dict | None:
    for key in build_vinted_item_identity_keys(
        item_id=row.get("item_id", ""),
        link=current_link or row.get("link", ""),
    ):
        cached_row = cached_detail_rows.get(key)
        if cached_row is not None:
            return cached_row
    return None


def _merge_cached_vinted_detail_row(
    *,
    base_row: dict,
    cached_row: dict,
    current_link: str,
    search_term: str,
    search_url: str,
    tag: str,
    item_name: str,
    config: dict,
) -> dict:
    existing_row = dict(base_row or {})
    merged = dict(existing_row)
    merged.update(
        {
            key: value
            for key, value in cached_row.items()
            if value not in ("", None) or key in {"offer_available", "detail_cached", "db_saved"}
        }
    )
    normalized_search_term = search_term or str(existing_row.get("search_term", "") or "") or extract_vinted_search_term(search_url)
    normalized_search_url = search_url or str(existing_row.get("search_url", "") or "") or build_vinted_search_url(normalized_search_term)
    secondary_badge_text = normalize_whitespace(str(existing_row.get("secondary_badge_text", "") or merged.get("secondary_badge_text", "") or ""))
    has_ricercato_badge = bool(existing_row.get("has_ricercato_badge")) or bool(
        merged.get("has_ricercato_badge")
    ) or bool(RICERCATO_BADGE_PATTERN.search(secondary_badge_text)) or str(tag or merged.get("tag", "") or "").strip().lower() == "ricercato"
    favorite_count = parse_vinted_favorite_count(merged.get("favorite_count"))
    if favorite_count is None:
        favorite_count = parse_vinted_favorite_count(existing_row.get("favorite_count"))
    published_at = str(merged.get("published_at", "") or "")
    merged.update(
        {
            "source": "vinted",
            "tag": "ricercato" if has_ricercato_badge else (tag or str(existing_row.get("tag", "") or merged.get("tag", "") or "")),
            "search_term": normalized_search_term,
            "search_url": normalized_search_url,
            "item_id": str(merged.get("item_id", "") or existing_row.get("item_id", "") or ""),
            "name": normalize_whitespace(str(item_name or existing_row.get("name", "") or merged.get("name", "") or current_link)),
            "link": normalize_vinted_item_url(current_link or str(merged.get("link", "") or "")),
            "favorite_count": favorite_count,
            "secondary_badge_text": secondary_badge_text,
            "has_ricercato_badge": has_ricercato_badge,
            "evaluation_label": classify_vinted_evaluation(
                favorite_count=favorite_count,
                has_ricercato_badge=has_ricercato_badge,
                published_at=published_at,
            ),
            "shipping_alert": _vinted_shipping_alert_text(merged.get("shipping_price_value")),
            "detail_cached": True,
        }
    )
    return annotate_vinted_deal_hunter_row(
        merged,
        min_favorites=int(config.get("deal_hunter_min_favorites", 0) or 0),
        max_age_hours=float(config.get("deal_hunter_max_age_hours", VINTED_DEAL_HUNTER_DEFAULT_MAX_AGE_HOURS) or 0),
        min_price=config.get("min_price"),
        max_price=config.get("max_price"),
    )


def _build_vinted_detail_row(
    driver: Driver,
    current_link: str,
    search_term: str,
    search_url: str,
    tag: str,
    item_name: str,
    base_row: dict | None = None,
    deal_hunter_min_favorites: int = 0,
    deal_hunter_max_age_hours: float = VINTED_DEAL_HUNTER_DEFAULT_MAX_AGE_HOURS,
) -> dict:
    existing_row = dict(base_row or {})
    detail_payload = _read_vinted_detail_payload(driver)
    page_text = normalize_whitespace(str(detail_payload.get("bodyText", "") or "")) or _read_vinted_detail_text(driver)
    if _is_vinted_page_not_found_text(page_text):
        return _build_vinted_missing_detail_row(
            current_link=current_link,
            search_term=search_term,
            search_url=search_url,
            tag=tag,
            item_name=item_name,
            base_row=existing_row,
            page_text=page_text,
            detail_error="page_not_found",
        )
    title = normalize_whitespace(str(detail_payload.get("title", "") or ""))
    if not title:
        title = _read_vinted_title(driver)
    if not title:
        title = item_name
    if not title:
        title = str(existing_row.get("name", "") or "")
    if not title:
        title = _fallback_title({"image_alt": "", "aria_label": ""}, current_link, page_text)
    if _is_vinted_page_not_found_text(title):
        return _build_vinted_missing_detail_row(
            current_link=current_link,
            search_term=search_term,
            search_url=search_url,
            tag=tag,
            item_name=item_name,
            base_row=existing_row,
            page_text=page_text,
            detail_error="page_not_found",
        )
    description = _extract_vinted_description_from_body_text(page_text)
    published_at = normalize_whitespace(str(detail_payload.get("publishedText", "") or "")) or _read_vinted_published_text(driver, page_text)
    raw_price_text = normalize_whitespace(str(detail_payload.get("rawPriceText", "") or "")) or _read_vinted_price(driver)
    total_price_text = _extract_vinted_primary_price(page_text, title)
    base_price_text = (
        _extract_vinted_base_price(page_text, title)
        or _extract_vinted_base_price(raw_price_text, title)
        or _find_price(raw_price_text)
        or _find_price(page_text)
        or total_price_text
    )
    shipping_price = _extract_vinted_shipping_price_text(str(detail_payload.get("shippingText", "") or "")) or _read_vinted_shipping_price(driver, page_text)
    shipping_price_value = parse_vinted_price(shipping_price)
    shipping_alert = _vinted_shipping_alert_text(shipping_price_value)
    offer_text = normalize_whitespace(str(detail_payload.get("offerText", "") or "")) or _read_vinted_offer_text(driver)
    if total_price_text:
        total_price = normalize_whitespace(total_price_text)
        total_price_value = parse_vinted_price(total_price_text)
    else:
        total_price, total_price_value = _build_vinted_total(base_price_text, shipping_price)
    favorite_count = parse_vinted_favorite_count(detail_payload.get("favorite_count_text"))
    if favorite_count is None:
        favorite_count = parse_vinted_favorite_count(existing_row.get("favorite_count"))
    secondary_badge_text = normalize_whitespace(str(existing_row.get("secondary_badge_text", "") or ""))
    has_ricercato_badge = bool(existing_row.get("has_ricercato_badge")) or bool(
        RICERCATO_BADGE_PATTERN.search(secondary_badge_text)
    ) or str(tag or "").strip().lower() == "ricercato"
    evaluation_label = classify_vinted_evaluation(
        favorite_count=favorite_count,
        has_ricercato_badge=has_ricercato_badge,
        published_at=published_at,
    )
    item_id_match = ITEM_ID_PATTERN.search(urlsplit(current_link).path)
    normalized_search_term = search_term or extract_vinted_search_term(search_url)
    row = {
        "source": "vinted",
        "tag": "ricercato" if has_ricercato_badge else tag,
        "search_term": normalized_search_term,
        "search_url": search_url or build_vinted_search_url(normalized_search_term),
        "item_id": item_id_match.group(1) if item_id_match else "",
        "name": normalize_whitespace(title) or current_link,
        "description": description,
        "published_at": published_at,
        "price": normalize_whitespace(base_price_text),
        "price_value": parse_vinted_price(base_price_text),
        "shipping_price": shipping_price,
        "shipping_price_value": shipping_price_value,
        "total_price": total_price,
        "total_price_value": total_price_value,
        "offer_available": bool(offer_text),
        "offer_text": offer_text,
        "currency": "EUR" if "€" in base_price_text or "â‚¬" in base_price_text else "",
        "link": current_link,
        "image_url": normalize_vinted_image_url(str(existing_row.get("image_url", "") or "")),
        "favorite_count": favorite_count,
        "evaluation_label": evaluation_label,
        "shipping_alert": shipping_alert,
        "secondary_badge_text": secondary_badge_text,
        "has_ricercato_badge": has_ricercato_badge,
        "raw_text": page_text,
        "extracted_at": datetime.now().isoformat(timespec="seconds"),
    }
    return annotate_vinted_deal_hunter_row(
        row,
        min_favorites=deal_hunter_min_favorites,
        max_age_hours=deal_hunter_max_age_hours,
        min_price=base_row.get("min_price") if isinstance(base_row, dict) else None,
        max_price=base_row.get("max_price") if isinstance(base_row, dict) else None,
    )


def _is_vinted_page_not_found_text(value: object) -> bool:
    return bool(VINTED_PAGE_NOT_FOUND_PATTERN.search(normalize_whitespace(str(value or ""))))


def _build_vinted_missing_detail_row(
    *,
    current_link: str,
    search_term: str,
    search_url: str,
    tag: str,
    item_name: str,
    base_row: dict | None,
    page_text: str,
    detail_error: str,
) -> dict:
    existing_row = dict(base_row or {})
    item_id_match = ITEM_ID_PATTERN.search(urlsplit(current_link).path)
    normalized_search_term = search_term or extract_vinted_search_term(search_url)
    row = dict(existing_row)
    row.update(
        {
            "source": "vinted",
            "tag": str(existing_row.get("tag", "") or tag or ""),
            "search_term": normalized_search_term,
            "search_url": search_url or build_vinted_search_url(normalized_search_term),
            "item_id": str(existing_row.get("item_id", "") or (item_id_match.group(1) if item_id_match else "")),
            "name": normalize_whitespace(str(existing_row.get("name", "") or item_name or current_link)) or current_link,
            "link": current_link,
            "raw_text": page_text or str(existing_row.get("raw_text", "") or ""),
            "detail_error": detail_error,
            "extracted_at": datetime.now().isoformat(timespec="seconds"),
        }
    )
    return row


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


def _wait_for_vinted_detail_page_ready(
    driver: Driver,
    max_wait_seconds: float,
    poll_interval_seconds: float = 0.1,
) -> bool:
    max_wait = _nonnegative_float(max_wait_seconds, 0.0)
    if max_wait <= 0:
        return _is_vinted_detail_page_ready(driver)
    poll_interval = max(_nonnegative_float(poll_interval_seconds, 0.2), 0.05)
    deadline = time.monotonic() + max_wait
    while True:
        if _is_vinted_detail_page_ready(driver):
            return True
        if time.monotonic() >= deadline:
            break
        time.sleep(min(poll_interval, max(deadline - time.monotonic(), 0.05)))
    return _is_vinted_detail_page_ready(driver)


def _wait_for_vinted_catalog_page_ready(
    driver: Driver,
    max_wait_seconds: float,
    poll_interval_seconds: float = 0.1,
) -> bool:
    max_wait = _nonnegative_float(max_wait_seconds, 0.0)
    if max_wait <= 0:
        return _is_vinted_catalog_page_ready(driver)
    poll_interval = max(_nonnegative_float(poll_interval_seconds, 0.2), 0.05)
    deadline = time.monotonic() + max_wait
    while True:
        if _is_vinted_catalog_page_ready(driver):
            return True
        if time.monotonic() >= deadline:
            break
        time.sleep(min(poll_interval, max(deadline - time.monotonic(), 0.05)))
    return _is_vinted_catalog_page_ready(driver)


def _wait_for_vinted_catalog_cards(
    driver: Driver,
    max_wait_seconds: float,
    poll_interval_seconds: float = 0.1,
) -> bool:
    max_wait = _nonnegative_float(max_wait_seconds, 0.0)
    if max_wait <= 0:
        return _vinted_catalog_card_count(driver) > 0
    poll_interval = max(_nonnegative_float(poll_interval_seconds, 0.1), 0.05)
    deadline = time.monotonic() + max_wait
    while True:
        if _vinted_catalog_card_count(driver) > 0:
            return True
        if time.monotonic() >= deadline:
            break
        time.sleep(min(poll_interval, max(deadline - time.monotonic(), 0.05)))
    return _vinted_catalog_card_count(driver) > 0


def _vinted_catalog_card_count(driver: Driver) -> int:
    try:
        value = driver.run_js(
            """
return document.querySelectorAll('a[href*="/items/"]').length;
            """
        )
    except Exception:
        return 0
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError):
        return 0


def _is_vinted_detail_page_ready(driver: Driver) -> bool:
    payload = _read_vinted_detail_payload(driver)
    if not isinstance(payload, dict):
        return False
    if bool(payload.get("pageNotFound", False)):
        return True
    has_title = bool(str(payload.get("title", "") or "").strip())
    has_price = bool(str(payload.get("rawPriceText", "") or "").strip())
    has_offer = bool(str(payload.get("offerText", "") or "").strip())
    body_length = int(payload.get("bodyLength", 0) or 0)
    ready_state = str(payload.get("readyState", "") or "").strip().lower()
    if has_title and has_price:
        return True
    if has_title and has_offer:
        return True
    if ready_state == "complete" and has_title and body_length >= 120:
        return True
    return False


def _is_vinted_catalog_page_ready(driver: Driver) -> bool:
    payload = driver.run_js(
        """
const itemLinks = document.querySelectorAll('a[href*="/items/"]').length;
const paginationNodes = document.querySelectorAll('[data-testid^="catalog-pagination--page-"]').length;
const bodyText = (document.body ? (document.body.innerText || document.body.textContent || '') : '').trim();
const readyState = document.readyState || '';
return {
  readyState,
  itemLinks,
  paginationNodes,
  bodyLength: bodyText.length,
  ready: (readyState === 'interactive' || readyState === 'complete') && (itemLinks > 0 || paginationNodes > 0 || bodyText.length > 160),
};
        """
    )
    if not isinstance(payload, dict):
        return False
    return bool(payload.get("ready", False))


def _hold_vinted_browser_if_requested(driver: Driver, keep_browser_open: bool, keep_open_seconds: int) -> None:
    if keep_browser_open:
        _keep_browser_open(driver, 0)
        return
    if keep_open_seconds > 0:
        _keep_browser_open(driver, keep_open_seconds)


def _wait_for_vinted_login_if_needed(
    driver: Driver,
    access_status: dict[str, object],
    revisit_url: str = "",
    action_delay_seconds: float = 1.5,
    page_settle_seconds: float = 3.0,
) -> dict[str, object]:
    print(
        "[vinted-login] check "
        f"marker_present={bool(access_status.get('marker_present'))} "
        f"page_not_found={bool(access_status.get('page_not_found'))} "
        f"url={str(access_status.get('current_url', '') or '')}",
        flush=True,
    )
    if bool(access_status.get("page_not_found")):
        return access_status
    if bool(access_status.get("marker_present")):
        return access_status
    emit_vinted_login_required_signal(access_status)
    access_status = _open_vinted_google_login_flow(
        driver,
        access_status=access_status,
        action_delay_seconds=action_delay_seconds,
    )
    print(
        "[vinted-login] after-google-flow "
        f"marker_present={bool(access_status.get('marker_present'))} "
        f"page_not_found={bool(access_status.get('page_not_found'))} "
        f"url={str(access_status.get('current_url', '') or '')}",
        flush=True,
    )
    if bool(access_status.get("page_not_found")) or bool(access_status.get("marker_present")):
        return access_status
    target_url = str(revisit_url or access_status.get("current_url", "") or "").strip()
    while True:
        if consume_stop_after_current_item_request():
            raise RuntimeError("Attesa login Vinted interrotta su richiesta dell'utente.")
        refreshed_status = wait_for_vinted_access_status(
            driver,
            max_wait_seconds=min(max(float(page_settle_seconds or 0), 0.0), 0.75),
        )
        print(
            "[vinted-login] wait-loop "
            f"marker_present={bool(refreshed_status.get('marker_present'))} "
            f"page_not_found={bool(refreshed_status.get('page_not_found'))} "
            f"url={str(refreshed_status.get('current_url', '') or '')}",
            flush=True,
        )
        emit_vinted_access_signal(refreshed_status)
        if bool(refreshed_status.get("page_not_found")):
            return refreshed_status
        if bool(refreshed_status.get("marker_present")):
            return _reopen_vinted_target_after_login(
                driver,
                refreshed_status,
                target_url=target_url,
                action_delay_seconds=action_delay_seconds,
                page_settle_seconds=page_settle_seconds,
            )
        if consume_vinted_login_confirmed_request():
            refreshed_status = _reopen_vinted_target_after_login(
                driver,
                refreshed_status,
                target_url=target_url,
                action_delay_seconds=action_delay_seconds,
                page_settle_seconds=page_settle_seconds,
            )
            if bool(refreshed_status.get("page_not_found")) or bool(refreshed_status.get("marker_present")):
                return refreshed_status
            emit_vinted_login_required_signal(refreshed_status)
            refreshed_status = _open_vinted_google_login_flow(
                driver,
                access_status=refreshed_status,
                action_delay_seconds=action_delay_seconds,
            )
            if bool(refreshed_status.get("page_not_found")) or bool(refreshed_status.get("marker_present")):
                return refreshed_status
        time.sleep(0.25)
    return access_status


def emit_vinted_login_required_signal(access_status: dict[str, object]) -> None:
    print(f"__VINTED_LOGIN_REQUIRED__:{json.dumps(access_status, ensure_ascii=False)}", flush=True)


def _open_vinted_google_login_flow(
    driver: Driver,
    *,
    access_status: dict[str, object],
    action_delay_seconds: float = 1.5,
) -> dict[str, object]:
    current_status = dict(access_status or {})
    print(
        "[vinted-login] google-flow-start "
        f"marker_present={bool(current_status.get('marker_present'))} "
        f"page_not_found={bool(current_status.get('page_not_found'))} "
        f"url={str(current_status.get('current_url', '') or '')}",
        flush=True,
    )
    if bool(current_status.get("page_not_found")):
        return current_status
    login_clicked = bool(
        driver.run_js(
            """
const normalize = (value) => (value || '').replace(/\\s+/g, ' ').trim().toLowerCase();
const isVisible = (element) => {
  const style = window.getComputedStyle(element);
  return style.display !== 'none' && style.visibility !== 'hidden' && element.getClientRects().length > 0;
};
const candidates = [
  ...document.querySelectorAll('a[data-testid="header--login-button"], a, button, [role="button"]'),
];
const target = candidates.find((element) => {
  if (!isVisible(element)) return false;
  const testId = normalize(element.getAttribute('data-testid') || '');
  if (testId === 'header--login-button') return true;
  const text = normalize(element.innerText || element.textContent);
  return text.includes('iscriviti') || text.includes('accedi') || text.includes('sign up') || text.includes('log in');
});
if (!target) return false;
try { target.scrollIntoView({ block: 'center' }); } catch {}
try { target.focus(); } catch {}
try { target.click(); return true; } catch (error) { return false; }
            """
        )
    )
    if not login_clicked:
        current_status = wait_for_vinted_access_status(
            driver,
            max_wait_seconds=0.75,
            poll_interval_seconds=0.2,
        )
        print(
            "[vinted-login] header-login-missing "
            f"marker_present={bool(current_status.get('marker_present'))} "
            f"page_not_found={bool(current_status.get('page_not_found'))} "
            f"url={str(current_status.get('current_url', '') or '')}",
            flush=True,
        )
        return current_status
    wait_before_google_seconds = max(float(action_delay_seconds or 0), 2.0)
    print(
        f"[vinted-login] waiting-before-google seconds={wait_before_google_seconds:.2f}",
        flush=True,
    )
    time.sleep(wait_before_google_seconds)
    google_button_still_visible = True
    for attempt in range(1, 7):
        google_clicked = bool(
            driver.run_js(
        """
const normalize = (value) => (value || '').replace(/\\s+/g, ' ').trim().toLowerCase();
const isVisible = (element) => {
  const style = window.getComputedStyle(element);
  return style.display !== 'none' && style.visibility !== 'hidden' && element.getClientRects().length > 0;
};
const candidates = [
  ...document.querySelectorAll('a[data-testid="google-oauth-button"], a, button, [role="button"]'),
];
const target = candidates.find((element) => {
  if (!isVisible(element)) return false;
  const testId = normalize(element.getAttribute('data-testid') || '');
  if (testId === 'google-oauth-button') return true;
  const text = normalize(element.innerText || element.textContent);
  return text.includes('accedi con google') || text.includes('continue with google') || text.includes('sign in with google');
});
if (!target) return false;
try { target.scrollIntoView({ block: 'center' }); } catch {}
try { target.focus(); } catch {}
try { target.click(); return true; } catch (error) { return false; }
        """
            )
        )
        wait_after_google_click_seconds = max(float(action_delay_seconds or 0), 4.0)
        print(
            f"[vinted-login] google-click attempt={attempt} clicked={google_clicked} wait_after={wait_after_google_click_seconds:.2f}",
            flush=True,
        )
        time.sleep(wait_after_google_click_seconds)
        google_button_still_visible = bool(
            driver.run_js(
                """
const normalize = (value) => (value || '').replace(/\\s+/g, ' ').trim().toLowerCase();
const isVisible = (element) => {
  const style = window.getComputedStyle(element);
  return style.display !== 'none' && style.visibility !== 'hidden' && element.getClientRects().length > 0;
};
const candidates = [
  ...document.querySelectorAll('a[data-testid="google-oauth-button"], a, button, [role="button"]'),
];
return candidates.some((element) => {
  if (!isVisible(element)) return false;
  const testId = normalize(element.getAttribute('data-testid') || '');
  if (testId === 'google-oauth-button') return true;
  const text = normalize(element.innerText || element.textContent);
  return text.includes('accedi con google') || text.includes('continue with google') || text.includes('sign in with google');
});
                """
            )
        )
        print(
            f"[vinted-login] google-visibility attempt={attempt} visible={google_button_still_visible}",
            flush=True,
        )
        if not google_button_still_visible:
            break
        if not google_clicked:
            break
    current_status = wait_for_vinted_access_status(
        driver,
        max_wait_seconds=2.5,
        poll_interval_seconds=0.25,
    )
    print(
        "[vinted-login] google-flow-end "
        f"marker_present={bool(current_status.get('marker_present'))} "
        f"page_not_found={bool(current_status.get('page_not_found'))} "
        f"url={str(current_status.get('current_url', '') or '')}",
        flush=True,
    )
    emit_vinted_access_signal(current_status)
    return current_status


def _reopen_vinted_target_after_login(
    driver: Driver,
    access_status: dict[str, object],
    target_url: str,
    action_delay_seconds: float = 1.5,
    page_settle_seconds: float = 3.0,
) -> dict[str, object]:
    normalized_target_url = str(target_url or "").strip()
    current_url = str(access_status.get("current_url", "") or "").strip()
    if normalized_target_url and current_url != normalized_target_url:
        driver.get(normalized_target_url, wait=VINTED_NAVIGATION_WAIT, timeout=VINTED_NAVIGATION_TIMEOUT_SECONDS)
        if "/items/" in normalized_target_url:
            _wait_for_vinted_detail_page_ready(
                driver,
                max_wait_seconds=float(page_settle_seconds or 0),
            )
        else:
            _wait_for_vinted_catalog_page_ready(
                driver,
                max_wait_seconds=float(page_settle_seconds or 0),
            )
        cookie_action = click_first_matching_text(driver, DEFAULT_COOKIE_REJECT_TEXTS)
        if cookie_action:
            time.sleep(min(float(action_delay_seconds or 0), 0.35))
        access_status = wait_for_vinted_access_status(
            driver,
            max_wait_seconds=min(max(float(page_settle_seconds or 0), 0.0), 1.0),
        )
        emit_vinted_access_signal(access_status)
    return access_status


def _detach_vinted_browser_if_requested(driver: Driver, config: dict) -> None:
    if not bool(config.get("detach_browser_on_complete", True)):
        return
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


def _persist_vinted_progress_results(
    *,
    rows,
    config: dict,
    search_url: str,
    pages_visited: list[int],
    filtered_out_known_items: int,
    filtered_out_by_price: int,
    cookie_action: str,
    access_status: dict,
    enrichment_meta: dict[str, int],
    live_stage: str,
) -> None:
    ui_result_json = str(config.get("ui_result_json", "") or "").strip()
    if not ui_result_json:
        return
    max_results = int(config.get("max_results", 100) or 0)
    max_price = _normalize_vinted_max_price(config.get("max_price"))
    min_price = normalize_vinted_deal_hunter_min_price(config.get("min_price"))
    progress_rows = _prioritize_vinted_rows(
        annotate_vinted_deal_hunter_row(
            dict(row),
            min_favorites=int(config.get("deal_hunter_min_favorites", 0) or 0),
            max_age_hours=float(config.get("deal_hunter_max_age_hours", VINTED_DEAL_HUNTER_DEFAULT_MAX_AGE_HOURS) or 0),
            min_price=min_price,
            max_price=max_price,
        )
        for row in rows
    )
    if max_results > 0:
        progress_rows = progress_rows[:max_results]
    progress_rows = [row for row in progress_rows if _vinted_row_matches_max_price(row, max_price)]
    progress_rows = [row for row in progress_rows if _vinted_row_matches_min_price_for_deal_hunter(row, min_price)]
    deal_hunter_candidates = sum(1 for row in progress_rows if bool(row.get("deal_hunter_candidate")))
    deal_hunter_matches = sum(1 for row in progress_rows if bool(row.get("deal_hunter_match")))
    meta = {
        "search": config.get("search", ""),
        "search_term": config.get("search_term", ""),
        "search_count": 1,
        "tag": "",
        "search_url": search_url,
        "max_results": max_results,
        "min_price": min_price,
        "max_price": max_price,
        "deal_hunter_enabled": bool(config.get("deal_hunter_enabled", False)),
        "deal_hunter_min_favorites": int(config.get("deal_hunter_min_favorites", 0) or 0),
        "deal_hunter_max_age_hours": float(
            config.get("deal_hunter_max_age_hours", VINTED_DEAL_HUNTER_DEFAULT_MAX_AGE_HOURS) or 0
        ),
        "deal_hunter_candidates": deal_hunter_candidates,
        "deal_hunter_matches": deal_hunter_matches,
        "exclude_known_items": bool(config.get("exclude_known_items", True)),
        "keep_browser_open": bool(config.get("keep_browser_open", False)),
        "keep_open_seconds": int(config.get("keep_open_seconds", 0) or 0),
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
        "filtered_out_known_items": filtered_out_known_items,
        "filtered_out_by_price": filtered_out_by_price,
        "row_count": len(progress_rows),
        "priority_rows_enriched": int(enrichment_meta.get("enriched_count", 0) or 0),
        "priority_rows_demoted_by_age": int(enrichment_meta.get("demoted_count", 0) or 0),
        "priority_rows_cached": int(enrichment_meta.get("cached_count", 0) or 0),
        "live_partial": True,
        "live_stage": live_stage,
        "db_saved_live": False,
    }
    ui_result_path = Path(ui_result_json).expanduser()
    ui_result_path.parent.mkdir(parents=True, exist_ok=True)
    write_outcome_json(ui_result_path, ScrapeOutcome(source="vinted", rows=progress_rows, meta=meta))


def _persist_vinted_live_results(rows: list[dict], meta: dict, db_path: str, ui_result_json: str) -> None:
    db_meta = save_vinted_rows(rows, db_path=db_path, run_kind="search")
    for row in rows:
        row["db_path"] = db_meta["db_path"]
        row["db_saved"] = True
    meta.update(db_meta)
    meta["db_saved_live"] = True
    if ui_result_json:
        ui_result_path = Path(ui_result_json).expanduser()
        ui_result_path.parent.mkdir(parents=True, exist_ok=True)
        write_outcome_json(ui_result_path, ScrapeOutcome(source="vinted", rows=rows, meta=meta))


def _card_payload_to_row(
    payload: dict,
    search_term: str,
    search_url: str,
    deal_hunter_min_favorites: int = 0,
    deal_hunter_max_age_hours: float = VINTED_DEAL_HUNTER_DEFAULT_MAX_AGE_HOURS,
) -> dict:
    link = normalize_vinted_item_url(str(payload.get("link", "") or ""))
    raw_text = normalize_whitespace(str(payload.get("raw_text", "") or ""))
    title = normalize_whitespace(str(payload.get("title", "") or ""))
    if not title:
        title = _fallback_title(payload, link, raw_text)
    price = normalize_whitespace(str(payload.get("price", "") or "")) or _find_price(raw_text)
    price_value = parse_vinted_price(price)
    item_id_match = ITEM_ID_PATTERN.search(urlsplit(link).path)
    image_url = normalize_vinted_image_url(str(payload.get("image_url", "") or ""))
    secondary_badge_text = normalize_whitespace(str(payload.get("secondary_badge_text", "") or ""))
    has_ricercato_badge = bool(RICERCATO_BADGE_PATTERN.search(secondary_badge_text))
    favorite_count = parse_vinted_favorite_count(payload.get("favorite_count_text"))
    evaluation_label = classify_vinted_evaluation(
        favorite_count=favorite_count,
        has_ricercato_badge=has_ricercato_badge,
    )
    shipping_alert = _vinted_shipping_alert_text(None)

    row = {
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
        "image_url": image_url,
        "favorite_count": favorite_count,
        "evaluation_label": evaluation_label,
        "shipping_alert": shipping_alert,
        "secondary_badge_text": secondary_badge_text,
        "has_ricercato_badge": has_ricercato_badge,
        "raw_text": raw_text,
        "extracted_at": datetime.now().isoformat(timespec="seconds"),
    }
    return annotate_vinted_deal_hunter_row(
        row,
        min_favorites=deal_hunter_min_favorites,
        max_age_hours=deal_hunter_max_age_hours,
        max_price=None,
    )


def _vinted_row_identity_keys(row: dict) -> tuple[str, ...]:
    return build_vinted_item_identity_keys(
        item_id=row.get("item_id", ""),
        link=row.get("link", ""),
    )


def _vinted_row_matches_known_item_keys(row: dict, known_item_keys: set[str]) -> bool:
    if not known_item_keys:
        return False
    return any(key in known_item_keys for key in _vinted_row_identity_keys(row))


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


def normalize_vinted_image_url(url: str) -> str:
    value = str(url or "").strip()
    if not value or value.startswith("data:"):
        return ""
    return urljoin(VINTED_BASE_URL, value)


def _build_vinted_deal_discord_embeds(row: dict) -> list[dict[str, object]]:
    image_url = normalize_vinted_image_url(str(row.get("image_url", "") or ""))
    if not image_url:
        return []
    title = normalize_whitespace(str(row.get("name", "") or "Vinted"))
    link = str(row.get("link", "") or "").strip()
    embed: dict[str, object] = {
        "title": title[:256] if title else "Vinted",
        "image": {"url": image_url},
    }
    if link:
        embed["url"] = link
    return [embed]


def _normalize_vinted_catalog_url(url: str, page_number: int | None = None) -> str:
    next_url = str(url or "").strip()
    if not next_url:
        return ""
    parsed = urlsplit(next_url)
    if parsed.netloc:
        normalized_url = urlunsplit((parsed.scheme or "https", parsed.netloc, parsed.path, parsed.query, ""))
    else:
        normalized_url = f"{VINTED_BASE_URL}{next_url if next_url.startswith('/') else '/' + next_url}"
    if page_number is None:
        return normalized_url
    return build_vinted_page_url(normalized_url, page_number)


def _read_vinted_next_page_target(driver: Driver, next_page_number: int) -> dict[str, object]:
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
if (!node) {{
  return {{ href: '', clicked: false }};
}}
const href = node.href || node.getAttribute('href') || '';
let clicked = false;
try {{
  node.click();
  clicked = true;
}} catch (_error) {{
  clicked = false;
}}
return {{ href, clicked }};
        """
    )
    if not isinstance(payload, dict):
        return {"href": "", "clicked": False}
    return {
        "href": _normalize_vinted_catalog_url(str(payload.get("href", "") or ""), next_page_number),
        "clicked": bool(payload.get("clicked")),
    }


def _is_vinted_catalog_page_active(driver: Driver, page_number: int) -> bool:
    target_page_number = max(int(page_number or 1), 1)
    current_url = str(current_page_url(driver) or "").strip()
    if current_url and extract_vinted_page_number(current_url) == target_page_number:
        return True
    payload = driver.run_js(
        f"""
const selector = `[data-testid="catalog-pagination--page-{target_page_number}"]`;
const node = document.querySelector(selector);
if (!node) {{
  return false;
}}
return String(node.getAttribute('aria-current') || '').toLowerCase() === 'true';
        """
    )
    return bool(payload)


def _wait_for_vinted_catalog_page(driver: Driver, page_number: int, max_wait_seconds: float) -> bool:
    deadline = time.monotonic() + max(max_wait_seconds, 0.0)
    while True:
        if _is_vinted_catalog_page_active(driver, page_number):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.1)


def _open_vinted_next_page(driver: Driver, next_page_number: int, page_settle_seconds: float = 3.0) -> str:
    target = _read_vinted_next_page_target(driver, next_page_number)
    next_page_url = str(target.get("href", "") or "").strip()
    clicked = bool(target.get("clicked"))
    if not next_page_url:
        return ""

    navigation_wait_seconds = max(float(page_settle_seconds or 0), 0.8) + 1.0
    if clicked and _wait_for_vinted_catalog_page(driver, next_page_number, navigation_wait_seconds):
        return str(current_page_url(driver) or next_page_url)

    driver.get(next_page_url, wait=VINTED_NAVIGATION_WAIT, timeout=VINTED_NAVIGATION_TIMEOUT_SECONDS)
    _wait_for_vinted_catalog_page(driver, next_page_number, navigation_wait_seconds)
    return str(current_page_url(driver) or next_page_url)


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


def _normalize_vinted_max_price(value: object) -> float | None:
    if value in (None, ""):
        return None
    text = normalize_whitespace(str(value or ""))
    if not text:
        return None
    normalized = text.replace("€", "").replace("â‚¬", "").replace(" ", "")
    if "," in normalized:
        normalized = normalized.replace(".", "").replace(",", ".")
    try:
        parsed = float(normalized)
    except ValueError:
        return None
    if parsed < 0:
        return None
    return parsed


def _vinted_row_matches_max_price(row: dict, max_price: float | None) -> bool:
    normalized_max_price = _normalize_vinted_max_price(max_price)
    if normalized_max_price is None:
        return True
    price_value = row.get("total_price_value")
    if price_value in ("", None):
        price_value = row.get("price_value")
    try:
        numeric_price = float(price_value)
    except (TypeError, ValueError):
        return True
    return numeric_price <= normalized_max_price


def _vinted_row_matches_min_price_for_deal_hunter(row: dict, min_price: float | None) -> bool:
    normalized_min_price = normalize_vinted_deal_hunter_min_price(min_price)
    if normalized_min_price is None:
        return True
    price_value = row.get("price_value")
    if price_value in ("", None):
        price_value = row.get("total_price_value")
    try:
        numeric_price = float(price_value)
    except (TypeError, ValueError):
        return True
    return numeric_price >= normalized_min_price


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


def classify_vinted_evaluation(
    favorite_count: int | None,
    has_ricercato_badge: bool,
    published_at: str = "",
) -> str:
    if _is_vinted_older_than_one_month(published_at):
        return ""
    if has_ricercato_badge and not _is_vinted_older_than_one_week(published_at):
        return "da valutare assolutamente"
    if favorite_count is None or favorite_count <= FAVORITE_COUNT_REVIEW_THRESHOLD:
        return ""
    return "da valutare"


def _is_vinted_older_than_one_week(published_at: str) -> bool:
    amount, unit = _parse_vinted_relative_age(published_at)
    if amount is None or unit is None:
        return False
    if unit.startswith("minut") or unit.startswith("second") or unit.startswith("or"):
        return False
    if unit.startswith("giorn"):
        return amount > 7
    if unit.startswith("settiman"):
        return amount > 1
    if unit.startswith("mes") or unit.startswith("ann"):
        return True
    return False


def _is_vinted_older_than_one_month(published_at: str) -> bool:
    amount, unit = _parse_vinted_relative_age(published_at)
    if amount is None or unit is None:
        return False
    if unit.startswith("minut") or unit.startswith("second") or unit.startswith("or") or unit.startswith("giorn") or unit.startswith("settiman"):
        return False
    if unit.startswith("mes"):
        return amount > 1
    if unit.startswith("ann"):
        return True
    return False


def _parse_vinted_relative_age(published_at: str) -> tuple[int | None, str | None]:
    text = normalize_whitespace(str(published_at or "")).lower()
    if not text:
        return None, None
    if text == "ieri":
        return 1, "giorno"
    match = re.match(r"^(\d+)\s+([^\s]+)\s+fa$", text)
    if not match:
        return None, None
    return int(match.group(1)), match.group(2).strip().lower()


def _vinted_shipping_alert_text(shipping_price_value: object) -> str:
    shipping_value = parse_vinted_price(shipping_price_value)
    if shipping_value is None:
        return ""
    if shipping_value > VINTED_HIGH_SHIPPING_THRESHOLD:
        return "sped > 2,99"
    return ""


def _vinted_priority_rank(row: dict) -> int:
    if bool(row.get("deal_hunter_match")):
        return 0
    evaluation_label = str(row.get("evaluation_label", "") or "").strip().lower()
    if evaluation_label == "da valutare assolutamente":
        return 1
    if evaluation_label == "da valutare":
        return 2
    if row.get("has_ricercato_badge"):
        return 3
    return 4


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
        title_window_match = re.search(
            re.escape(title_text) + r"(.{0,320})",
            text,
            re.IGNORECASE | re.DOTALL,
        )
        if title_window_match:
            price_before_incl = _extract_vinted_price_before_incl(title_window_match.group(1))
            if price_before_incl:
                return price_before_incl
    price_before_incl = _extract_vinted_price_before_incl(text)
    if price_before_incl:
        return price_before_incl
    return ""


def _extract_vinted_base_price(page_text: str, title: str) -> str:
    text = str(page_text or "")
    title_text = normalize_whitespace(str(title or ""))
    if title_text:
        title_window_match = re.search(
            re.escape(title_text) + r"(.{0,320})",
            text,
            re.IGNORECASE | re.DOTALL,
        )
        if title_window_match:
            first_price_before_incl = _extract_vinted_first_price_before_incl(title_window_match.group(1))
            if first_price_before_incl:
                return first_price_before_incl
            first_price = _extract_first_vinted_price_text(title_window_match.group(1))
            if first_price:
                return first_price
    first_price_before_incl = _extract_vinted_first_price_before_incl(text)
    if first_price_before_incl:
        return first_price_before_incl
    return _extract_first_vinted_price_text(text)


def _extract_vinted_price_before_incl(text: str) -> str:
    text_value = str(text or "")
    if not text_value:
        return ""
    match = re.search(
        r"((?:\d[\d.\s]*(?:,\d{1,2})?\s*(?:â‚¬|€)\s*){1,4})(?:incl\.?|include(?:\s+la\s+protezione\s+acquisti)?)",
        text_value,
        re.IGNORECASE,
    )
    if not match:
        return ""
    prices = re.findall(
        r"\d[\d.\s]*(?:,\d{1,2})?\s*(?:â‚¬|€)",
        match.group(1),
        re.IGNORECASE,
    )
    if not prices:
        return ""
    return normalize_whitespace(prices[-1])


def _extract_vinted_first_price_before_incl(text: str) -> str:
    text_value = str(text or "")
    if not text_value:
        return ""
    match = re.search(
        r"((?:\d[\d.\s]*(?:,\d{1,2})?\s*(?:â‚¬|€)\s*){1,4})(?:incl\.?|include(?:\s+la\s+protezione\s+acquisti)?)",
        text_value,
        re.IGNORECASE,
    )
    if not match:
        return ""
    prices = re.findall(
        r"\d[\d.\s]*(?:,\d{1,2})?\s*(?:â‚¬|€)",
        match.group(1),
        re.IGNORECASE,
    )
    if not prices:
        return ""
    return normalize_whitespace(prices[0])


def _extract_first_vinted_price_text(text: str) -> str:
    match = re.search(r"\d[\d.\s]*(?:,\d{1,2})?\s*(?:â‚¬|€)", str(text or ""), re.IGNORECASE)
    if not match:
        return ""
    return normalize_whitespace(match.group(0))


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
