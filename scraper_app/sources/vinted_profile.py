import time
import json
import os
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib.request import urlopen

from botasaurus.browser import Driver, Wait, browser

from ..browser_helpers import DEFAULT_COOKIE_REJECT_TEXTS, click_first_matching_text, current_page_url, navigate_with_retries
from ..browser_runtime import resolve_browser_arguments, resolve_browser_profile
from ..discord_notifications import (
    build_vinted_profile_daily_report_discord_message,
    build_vinted_profile_report_discord_message,
    send_discord_webhook_message,
    split_discord_message,
)
from ..exporters import write_outcome_json
from ..models import ScrapeOutcome
from ..runtime_controls import consume_stop_after_current_item_request
from ..utils import normalize_whitespace
from ..vinted_database import (
    DEFAULT_VINTED_DB_PATH,
    extract_vinted_item_id_from_link,
    format_vinted_elapsed_seconds,
    load_vinted_profile_known_items,
    load_vinted_profile_latest_snapshot_items,
    load_vinted_profile_daily_changes,
    load_vinted_deal_sales,
    normalize_vinted_member_id,
    parse_vinted_precise_listing_age_seconds,
    save_vinted_profile_snapshot,
    summarize_vinted_deal_sales,
)
from .vinted import VINTED_BASE_URL, normalize_vinted_image_url, normalize_vinted_item_url
from .vinted_deal_sales import _sold_status_from_page


VINTED_PROFILE_NAVIGATION_TIMEOUT_SECONDS = 20
DEFAULT_AGENT_LAB_RUNTIME_URL = "http://127.0.0.1:8000"

# The wardrobe endpoint requires the page-specific anonymous/CSRF headers.
# Capture only those two non-secret request values before the profile app makes
# its initial page-one request; later pages are fetched by that same tab.
_VINTED_WARDROBE_HEADER_CAPTURE_SCRIPT = r"""
(() => {
  const capture = (input, init = {}) => {
    const url = typeof input === 'string' ? input : (input && input.url) || '';
    if (!/\/api\/v2\/wardrobe\/[^/]+\/items/.test(url)) return;
    const source = init.headers || (input && input.headers) || {};
    const headers = source instanceof Headers ? Object.fromEntries(source.entries())
      : Array.isArray(source) ? Object.fromEntries(source)
      : { ...source };
    window.__agentLabWardrobeHeaders = headers;
  };
  const originalFetch = window.fetch.bind(window);
  window.fetch = function(input, init) {
    capture(input, init || {});
    return originalFetch(input, init);
  };
  const originalOpen = XMLHttpRequest.prototype.open;
  const originalSetRequestHeader = XMLHttpRequest.prototype.setRequestHeader;
  const originalSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function(method, url, ...rest) {
    this.__agentLabWardrobeUrl = String(url || '');
    this.__agentLabWardrobeHeaders = {};
    return originalOpen.call(this, method, url, ...rest);
  };
  XMLHttpRequest.prototype.setRequestHeader = function(name, value) {
    if (/\/api\/v2\/wardrobe\/[^/]+\/items/.test(this.__agentLabWardrobeUrl || '')) {
      this.__agentLabWardrobeHeaders[String(name)] = String(value);
    }
    return originalSetRequestHeader.call(this, name, value);
  };
  XMLHttpRequest.prototype.send = function(...args) {
    if (/\/api\/v2\/wardrobe\/[^/]+\/items/.test(this.__agentLabWardrobeUrl || '')) {
      window.__agentLabWardrobeHeaders = { ...this.__agentLabWardrobeHeaders };
    }
    return originalSend.apply(this, args);
  };
})();
"""


def _download_vinted_daily_html_report(*, db_path: str, days: int = 30) -> Path:
    runtime_url = str(os.environ.get("AGENT_LAB_RUNTIME_URL", DEFAULT_AGENT_LAB_RUNTIME_URL) or "").strip().rstrip("/")
    if not runtime_url:
        raise ValueError("Agent Lab runtime URL is empty.")
    query = urlencode({"days": max(min(int(days or 30), 365), 1), "limit": 500})
    endpoint = f"{runtime_url}/api/vinted/reports/export?{query}"
    with urlopen(endpoint, timeout=30) as response:
        content = response.read()
        status = int(getattr(response, "status", 200) or 200)
    if status < 200 or status >= 300 or not content:
        raise RuntimeError(f"Marketplace HTML report export failed with HTTP {status}.")
    output_dir = Path(db_path).expanduser().resolve().parent / "reports"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"marketplace-report-{datetime.now().strftime('%Y%m%d-%H%M%S')}.html"
    output_path.write_bytes(content)
    return output_path


def _log_vinted_profile(message: str, **fields: object) -> None:
    def safe_log_value(value: object, limit: int = 180) -> str:
        text = str(value)
        text = text.replace("\r", "\\r").replace("\n", "\\n")
        if len(text) > limit:
            return f"{text[:limit]}…"
        return text

    details = " ".join(f"{key}={safe_log_value(value)}" for key, value in fields.items() if value is not None)
    if details:
        print(f"[vinted-profile] {message} | {details}", flush=True)
    else:
        print(f"[vinted-profile] {message}", flush=True)


def _wait_for_vinted_profile_dom_ready(
    driver: Driver,
    *,
    max_wait_seconds: float,
    poll_interval_seconds: float = 0.2,
) -> bool:
    """Wait for a stable profile shell without waiting on every network request."""
    deadline = time.monotonic() + max(float(max_wait_seconds or 0), 8.0)
    poll_interval = max(float(poll_interval_seconds or 0.2), 0.05)
    previous_signature = ""
    stable_observations = 0
    while True:
        try:
            signature = str(
                driver.run_js(
                    """
const clean = (value) => (value || '').replace(/\\s+/g, ' ').trim();
const h1 = clean(document.querySelector('h1')?.innerText || document.querySelector('h1')?.textContent || '');
const notFound = /^(?:page\\s+)?not found$/i.test(h1) || /^pagina non trovata$/i.test(h1)
  || Boolean(document.querySelector('video[poster*="error-page/notfound"], img[src*="error-page/notfound"]'));
const links = [...document.querySelectorAll('a[href*="/items/"]')];
const sample = links.slice(0, 6).map((node) => node.href).join('|');
const readyState = document.readyState || '';
const bodyLength = clean(document.body?.innerText || document.body?.textContent || '').length;
const ready = notFound || ((readyState === 'interactive' || readyState === 'complete') && (links.length > 0 || h1 || bodyLength > 180));
return ready ? `${notFound ? 'not-found' : readyState}|${links.length}|${sample}|${Math.floor(bodyLength / 160)}` : '';
                    """
                )
                or ""
            ).strip()
        except Exception:
            signature = ""
        if signature and signature == previous_signature:
            stable_observations += 1
        elif signature:
            previous_signature = signature
            stable_observations = 1
        else:
            previous_signature = ""
            stable_observations = 0
        if stable_observations >= 2:
            _log_vinted_profile("profile-dom-ready", stable_observations=stable_observations, signature=signature[:120])
            return True
        if time.monotonic() >= deadline:
            _log_vinted_profile("profile-dom-timeout", stable_observations=stable_observations)
            return False
        time.sleep(min(poll_interval, max(deadline - time.monotonic(), 0.05)))


def run_vinted_profile_monitor(
    profile_url: str,
    profile_urls: list[str] | None = None,
    max_items: int = 0,
    interval_seconds: int = 0,
    db_path: str = str(DEFAULT_VINTED_DB_PATH),
    ui_result_json: str = "",
    browser_mode: str = "chrome_normale",
    browser_user_data_dir: str = "",
    browser_profile_directory: str = "Default",
    keep_browser_open: bool = True,
    close_existing_runtime_browser: bool = False,
    refresh_browser_profile: bool = False,
    keep_open_seconds: int = 0,
    slow_mode: bool = False,
    action_delay_seconds: float = 1.5,
    page_settle_seconds: float = 3.0,
    discord_profile_report: bool = False,
    discord_webhook_url: str = "",
    daily_report_hour: int = 10,
    send_daily_report: bool = True,
    check_listing_statuses: bool = True,
    profile_categories: str = "",
) -> ScrapeOutcome:
    normalized_profile_urls = normalize_vinted_profile_urls(profile_urls or profile_url)
    if not normalized_profile_urls:
        raise ValueError("Inserisci un URL profilo Vinted valido, per esempio https://www.vinted.it/member/262102939")
    normalized_interval = max(int(interval_seconds or 0), 0)
    _log_vinted_profile(
        "monitor-start",
        profiles=len(normalized_profile_urls),
        interval_seconds=normalized_interval,
        max_items=max(int(max_items or 0), 0),
        db_path=str(db_path or DEFAULT_VINTED_DB_PATH),
        discord_report=bool(discord_profile_report),
        check_listing_statuses=bool(check_listing_statuses),
        profile_categories=profile_categories or "all",
        keep_browser_open=bool(keep_browser_open),
        browser_mode=browser_mode,
    )
    for index, normalized_url in enumerate(normalized_profile_urls, start=1):
        _log_vinted_profile("profile-input", index=index, profile_url=normalized_url)
    latest_outcome: ScrapeOutcome | None = None
    cycle_index = 0
    last_daily_report_date = ""
    while True:
        if consume_stop_after_current_item_request():
            break
        cycle_index += 1
        cycle_rows: list[dict] = []
        profile_metas: list[dict] = []
        _log_vinted_profile(
            "cycle-start",
            cycle=cycle_index,
            profiles=len(normalized_profile_urls),
            db_path=str(db_path or DEFAULT_VINTED_DB_PATH),
        )
        for index, current_profile_url in enumerate(normalized_profile_urls, start=1):
            if consume_stop_after_current_item_request():
                break
            _log_vinted_profile(
                "profile-task-start",
                cycle=cycle_index,
                index=index,
                profiles=len(normalized_profile_urls),
                profile_url=current_profile_url,
            )
            try:
                payload = _scrape_vinted_profile_task(
                    {
                    "profile_url": current_profile_url,
                    "profile_index": index,
                    "profile_count": len(normalized_profile_urls),
                    "max_items": max(int(max_items or 0), 0),
                    "db_path": db_path,
                    "ui_result_json": "",
                    "browser_mode": browser_mode,
                    "browser_user_data_dir": browser_user_data_dir,
                    "browser_profile_directory": browser_profile_directory,
                    "keep_browser_open": bool(keep_browser_open),
                    "close_existing_runtime_browser": bool(close_existing_runtime_browser),
                    "refresh_browser_profile": bool(refresh_browser_profile),
                    "keep_open_seconds": max(int(keep_open_seconds or 0), 0),
                    "slow_mode": bool(slow_mode),
                    "action_delay_seconds": _nonnegative_float(action_delay_seconds, 1.5),
                    "page_settle_seconds": _nonnegative_float(page_settle_seconds, 3.0),
                    "cycle_index": cycle_index,
                    "interval_seconds": normalized_interval,
                    "discord_profile_report": bool(discord_profile_report),
                    "discord_webhook_url": str(discord_webhook_url or "").strip(),
                    "check_listing_statuses": bool(check_listing_statuses),
                    "profile_categories": str(profile_categories or ""),
                    },
                    reuse_driver=bool(keep_browser_open),
                )
            except Exception as error:
                # One inaccessible profile must never cancel the other profiles
                # or turn a transient browser readiness issue into a failed
                # continuous-monitoring job. No snapshot is written here, so
                # existing active listings cannot be falsely marked as gone.
                error_text = f"{type(error).__name__}: {error}"
                _log_vinted_profile(
                    "profile-task-error",
                    cycle=cycle_index,
                    index=index,
                    profile_url=current_profile_url,
                    error=error_text,
                )
                profile_metas.append(
                    {
                        "profile_url": current_profile_url,
                        "member_id": normalize_vinted_member_id(current_profile_url),
                        "row_count": 0,
                        "profile_error": error_text,
                    }
                )
                continue
            cycle_rows.extend(payload["rows"])
            profile_metas.append(payload["meta"])
            profile_meta = payload["meta"]
            _log_vinted_profile(
                "profile-task-finished",
                cycle=cycle_index,
                index=index,
                member_id=profile_meta.get("member_id") or profile_meta.get("profile_member_id") or "",
                rows=len(payload["rows"]),
                active=profile_meta.get("profile_item_count", 0),
                new=profile_meta.get("profile_new_count", 0),
                gone=profile_meta.get("profile_gone_count", 0),
                sold=profile_meta.get("profile_sold_count", 0),
                discord_sent=profile_meta.get("discord_profile_report_sent", False),
                current_url=profile_meta.get("current_url", ""),
            )
        aggregate_meta = _build_vinted_profile_aggregate_meta(
            profile_urls=normalized_profile_urls,
            profile_metas=profile_metas,
            cycle_index=cycle_index,
            interval_seconds=normalized_interval,
            db_path=db_path,
        )
        latest_outcome = ScrapeOutcome(source="vinted_profile", rows=cycle_rows, meta=aggregate_meta)
        _log_vinted_profile(
            "cycle-finished",
            cycle=cycle_index,
            rows=len(cycle_rows),
            active=aggregate_meta.get("profile_item_count", 0),
            new=aggregate_meta.get("profile_new_count", 0),
            gone=aggregate_meta.get("profile_gone_count", 0),
            sold=aggregate_meta.get("profile_sold_count", 0),
            db_path=aggregate_meta.get("db_path", ""),
        )
        if ui_result_json:
            ui_result_path = Path(ui_result_json).expanduser()
            ui_result_path.parent.mkdir(parents=True, exist_ok=True)
            write_outcome_json(ui_result_path, latest_outcome)
            _log_vinted_profile("ui-result-written", path=str(ui_result_path), rows=len(cycle_rows))
        if send_daily_report:
            last_daily_report_date = maybe_send_vinted_profile_daily_report(
                db_path=db_path,
                profile_urls=normalized_profile_urls,
                discord_profile_report=bool(discord_profile_report),
                discord_webhook_url=str(discord_webhook_url or "").strip(),
                daily_report_hour=int(daily_report_hour if daily_report_hour is not None else 10),
                last_daily_report_date=last_daily_report_date,
            )
        if normalized_interval <= 0:
            return latest_outcome
        print(
            f"[vinted-profile] ciclo {cycle_index} completato: "
            f"{aggregate_meta.get('profile_count', 0)} profili, "
            f"{aggregate_meta.get('profile_item_count', 0)} attivi, "
            f"{aggregate_meta.get('profile_new_count', 0)} nuovi, "
            f"{aggregate_meta.get('profile_gone_count', 0)} spariti. "
            f"Pausa {normalized_interval}s.",
            flush=True,
        )
        deadline = time.monotonic() + normalized_interval
        while time.monotonic() < deadline:
            if consume_stop_after_current_item_request():
                return latest_outcome
            time.sleep(min(1.0, max(deadline - time.monotonic(), 0.0)))
    if latest_outcome is not None:
        return latest_outcome
    raise RuntimeError("Monitor profilo Vinted interrotto prima di completare il primo ciclo.")


def maybe_send_vinted_profile_daily_report(
    *,
    db_path: str,
    profile_urls: list[str],
    discord_profile_report: bool,
    discord_webhook_url: str,
    daily_report_hour: int,
    last_daily_report_date: str,
) -> str:
    if not discord_profile_report:
        return last_daily_report_date
    webhook_url = str(discord_webhook_url or "").strip()
    if not webhook_url:
        return last_daily_report_date
    hour = max(min(int(daily_report_hour or 10), 23), 0)
    now = datetime.now()
    today_key = now.date().isoformat()
    if now.hour < hour or last_daily_report_date == today_key:
        return last_daily_report_date
    since_at = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")
    until_at = now.isoformat(timespec="seconds")
    try:
        rows = load_vinted_profile_daily_changes(
            db_path=db_path,
            since_at=since_at,
            until_at=until_at,
            profile_urls=profile_urls,
            limit=120,
        )
        deal_sales = load_vinted_deal_sales(
            db_path=db_path,
            since_at=since_at,
            limit=0,
        )
        meta = {
            "profile_count": len(profile_urls),
            "daily_since_at": since_at,
            "daily_until_at": until_at,
            "daily_report_hour": hour,
        }
        message = build_vinted_profile_daily_report_discord_message(
            meta,
            rows,
            deal_sales=deal_sales,
            deal_sales_summary=summarize_vinted_deal_sales(deal_sales),
        )
        messages = split_discord_message(message)
        attachment_paths: list[Path] = []
        try:
            html_report_path = _download_vinted_daily_html_report(db_path=db_path, days=30)
            attachment_paths.append(html_report_path)
            _log_vinted_profile(
                "daily-html-report-ready",
                path=html_report_path,
                size_bytes=html_report_path.stat().st_size,
            )
        except Exception as exc:
            _log_vinted_profile("daily-html-report-error", date=today_key, error=exc)
        results = []
        for index, chunk in enumerate(messages):
            results.append(
                send_discord_webhook_message(
                    webhook_url,
                    chunk,
                    attachment_paths=attachment_paths if index == 0 else None,
                    embeds=_build_vinted_profile_report_embeds(rows) if index == 0 else None,
                )
            )
        if results and all(bool(result.get("ok")) for result in results):
            _log_vinted_profile(
                "daily-report-sent",
                date=today_key,
                hour=hour,
                rows=len(rows),
                sold_deals=len(deal_sales),
                messages=len(results),
                html_report_attached=bool(attachment_paths),
                sent_at=str(results[-1].get("sent_at", "") or ""),
            )
            return today_key
        failed_result = next((result for result in results if not bool(result.get("ok"))), {})
        _log_vinted_profile(
            "daily-report-failed",
            date=today_key,
            hour=hour,
            messages=len(results),
            error=str(failed_result.get("error", "") or "No Discord message generated"),
        )
    except Exception as exc:
        _log_vinted_profile("daily-report-error", date=today_key, hour=hour, error=exc)
    return last_daily_report_date


@browser(
    profile=resolve_browser_profile,
    add_arguments=resolve_browser_arguments,
    wait_for_complete_page_load=False,
    close_on_crash=True,
    max_retry=2,
    retry_wait=1,
    raise_exception=True,
)
def _scrape_vinted_profile_task(driver: Driver, config: dict) -> dict:
    profile_url = str(config["profile_url"])
    member_id = normalize_vinted_member_id(profile_url)
    db_path = str(config.get("db_path", "") or DEFAULT_VINTED_DB_PATH)
    _log_vinted_profile(
        "browser-open-profile",
        cycle=config.get("cycle_index", 1),
        index=config.get("profile_index", 1),
        profile_url=profile_url,
        member_id=member_id,
        db_path=db_path,
    )
    navigated = navigate_with_retries(
        driver,
        profile_url,
        wait=Wait.SHORT,
        timeout_seconds=VINTED_PROFILE_NAVIGATION_TIMEOUT_SECONDS,
    )
    navigation_url = str(current_page_url(driver) or "")
    if not navigated and member_id and normalize_vinted_member_id(navigation_url) == member_id:
        # Vinted may canonicalize /member/<id> to /member/<id>-<slug>;
        # the shared URL matcher treated that redirect as a failed navigation.
        navigated = True
        _log_vinted_profile(
            "profile-navigation-canonical-redirect-accepted",
            member_id=member_id,
            requested_url=profile_url,
            current_url=navigation_url,
        )
    if not navigated:
        _log_vinted_profile(
            "profile-navigation-failed",
            member_id=member_id,
            requested_url=profile_url,
            current_url=navigation_url,
        )
        raise RuntimeError("profile navigation did not reach the requested URL")
    if not _wait_for_vinted_profile_dom_ready(
        driver,
        max_wait_seconds=float(config.get("page_settle_seconds", 3.0) or 0),
    ):
        raise TimeoutError("profile DOM did not become stable")
    cookie_action = click_first_matching_text(driver, DEFAULT_COOKIE_REJECT_TEXTS)
    if cookie_action:
        time.sleep(min(float(config.get("action_delay_seconds", 1.5) or 0), 0.35))
    _log_vinted_profile(
        "browser-profile-loaded",
        profile_url=profile_url,
        member_id=member_id,
        current_url=str(current_page_url(driver) or ""),
        cookie_action=cookie_action or "",
    )
    rows = _scroll_and_read_vinted_profile_items(
        driver,
        max_items=max(int(config.get("max_items", 0) or 0), 0),
        page_settle_seconds=float(config.get("page_settle_seconds", 3.0) or 0),
    )
    api_rows = _read_all_vinted_profile_items_via_browser_session(
        driver,
        member_id=member_id,
        max_items=max(int(config.get("max_items", 0) or 0), 0),
    )
    if len(api_rows) > len(rows):
        _log_vinted_profile("profile-wardrobe-pagination-used", dom_rows=len(rows), api_rows=len(api_rows))
        rows = api_rows
    declared_item_count = _read_vinted_profile_declared_item_count(driver)
    paged_rows = _read_vinted_profile_additional_pages(
        driver,
        profile_url=profile_url,
        existing_rows=rows,
        declared_item_count=declared_item_count,
        max_items=max(int(config.get("max_items", 0) or 0), 0),
    )
    if len(paged_rows) > len(rows):
        rows = paged_rows
    category_filters = _normalize_vinted_profile_categories(config.get("profile_categories", ""))
    missing_identity_before = sum(
        1
        for row in rows
        if not str(row.get("profile_url", "") or "").strip() or not str(row.get("member_id", "") or "").strip()
    )
    _log_vinted_profile(
        "profile-items-read",
        profile_url=profile_url,
        member_id=member_id,
        rows=len(rows),
        missing_identity_before=missing_identity_before,
    )
    rows = _attach_vinted_profile_identity(rows, profile_url=profile_url, member_id=member_id)
    # Verify profile-missing listings on their detail pages for both owned and
    # competitor profiles. A missing profile card alone is never proof of sale.
    if bool(config.get("check_listing_statuses", False)):
        known_rows = load_vinted_profile_known_items(profile_url=profile_url, db_path=db_path)
        latest_snapshot_rows = load_vinted_profile_latest_snapshot_items(profile_url=profile_url, db_path=db_path)
        # Compare against the last complete inventory, not every listing ever
        # observed. Also carry forward unresolved items already marked gone:
        # otherwise a later snapshot can overwrite the inventory before their
        # detail page is checked.
        missing_rows = _vinted_profile_rows_missing_from_current(
            current_rows=rows,
            historical_rows=latest_snapshot_rows,
        )
        current_links = {
            str(row.get("link", "") or "").strip()
            for row in rows
            if str(row.get("link", "") or "").strip()
        }
        pending_gone_rows = [
            row
            for row in known_rows
            if str(row.get("profile_item_status", "") or "").strip().lower() == "gone"
            and not str(row.get("profile_status_checked_at", "") or "").strip()
            and str(row.get("link", "") or "").strip() not in current_links
        ]
        if category_filters:
            pending_gone_rows = _filter_vinted_profile_rows_by_category(pending_gone_rows, category_filters)
        missing_by_link = {
            str(row.get("link", "") or "").strip(): dict(row)
            for row in [*missing_rows, *pending_gone_rows]
            if str(row.get("link", "") or "").strip()
        }
        missing_rows = list(missing_by_link.values())
        if category_filters:
            before_category_filter = len(missing_rows)
            missing_rows = _filter_vinted_profile_rows_by_category(missing_rows, category_filters)
            _log_vinted_profile(
                "profile-status-category-filter",
                member_id=member_id,
                categories=", ".join(category_filters),
                missing_before=before_category_filter,
                missing_after=len(missing_rows),
            )
        current_unique = {str(row.get("link", "") or "").strip() for row in rows if str(row.get("link", "") or "").strip()}
        historical_unique = {
            str(row.get("link", "") or "").strip()
            for row in latest_snapshot_rows
            if str(row.get("link", "") or "").strip()
        }
        likely_incomplete = bool(
            historical_unique
            and (
                not current_unique
                or (
                    len(historical_unique) >= 12
                    and len(historical_unique - current_unique) >= max(8, int(len(historical_unique) * 0.25))
                    and len(historical_unique - current_unique) / len(historical_unique) >= 0.35
                )
            )
        )
        if likely_incomplete:
            _log_vinted_profile(
                "listing-status-check-skipped-incomplete-profile-read",
                member_id=member_id,
                current_items=len(current_unique),
                historical_items=len(historical_unique),
                candidate_missing=len(missing_rows),
            )
            # Do not inspect rows inferred from an untrusted current read, but
            # still finish previously-confirmed profile-gone checks. This lets
            # a bad inventory response never block status checks already queued
            # by an earlier complete snapshot.
            missing_rows = pending_gone_rows
        _log_vinted_profile(
            "listing-status-check-requested",
            member_id=member_id,
            current_items=len(rows),
            historical_items=len(known_rows),
            missing_items=len(missing_rows),
        )
        checked_missing_rows: list[dict] = []
        if missing_rows:
            checked_missing_rows = _check_profile_listing_status(
                driver,
                missing_rows,
                page_settle_seconds=float(config.get("page_settle_seconds", 3.0) or 0),
            )
            rows = [*rows, *checked_missing_rows]
        if likely_incomplete:
            # Keep the last trusted inventory and persist only the detail-page
            # outcomes from known gone listings. Never turn an inconsistent
            # profile response into a mass new/gone snapshot.
            rows = [*latest_snapshot_rows, *checked_missing_rows]
    missing_identity_after = sum(
        1
        for row in rows
        if not str(row.get("profile_url", "") or "").strip() or not str(row.get("member_id", "") or "").strip()
    )
    if missing_identity_after:
        _log_vinted_profile(
            "profile-items-enriched",
            profile_url=profile_url,
            member_id=member_id,
            rows=len(rows),
            missing_identity_after=missing_identity_after,
        )
    snapshot_meta = save_vinted_profile_snapshot(
        profile_url=profile_url,
        rows=rows,
        db_path=db_path,
    )
    if bool(snapshot_meta.get("profile_snapshot_incomplete")):
        # Keep the last complete inventory visible when this read was empty or
        # incomplete. The DB intentionally retains the older trusted snapshot.
        rows = list(snapshot_meta.get("profile_current_rows", rows) or [])
        _log_vinted_profile(
            "incomplete-read-preserved-last-complete-inventory",
            member_id=member_id,
            displayed_rows=len(rows),
            previous_count=snapshot_meta.get("profile_previous_item_count", 0),
            missing_count=snapshot_meta.get("profile_missing_item_count", 0),
        )
    _log_vinted_profile(
        "snapshot-saved",
        profile_url=profile_url,
        member_id=member_id,
        snapshot_id=snapshot_meta.get("profile_snapshot_id", ""),
        active=snapshot_meta.get("profile_item_count", 0),
        new=snapshot_meta.get("profile_new_count", 0),
        gone=snapshot_meta.get("profile_gone_count", 0),
        sold=snapshot_meta.get("profile_sold_count", 0),
        still=snapshot_meta.get("profile_still_count", 0),
        incomplete=snapshot_meta.get("profile_snapshot_incomplete", False),
        previous=snapshot_meta.get("profile_previous_item_count", ""),
        missing=snapshot_meta.get("profile_missing_item_count", ""),
        db_path=snapshot_meta.get("db_path", db_path),
    )
    gone_rows = list(snapshot_meta.get("profile_gone_rows", []) or [])
    output_rows = _prioritize_profile_rows([*rows, *gone_rows])
    meta = {
        "profile_url": profile_url,
        "member_id": member_id,
        "profile_index": int(config.get("profile_index", 1) or 1),
        "profile_count": int(config.get("profile_count", 1) or 1),
        "current_url": str(current_page_url(driver) or ""),
        "max_items": max(int(config.get("max_items", 0) or 0), 0),
        "cycle_index": int(config.get("cycle_index", 1) or 1),
        "interval_seconds": int(config.get("interval_seconds", 0) or 0),
        "cookie_banner_action": cookie_action or "",
        "row_count": len(output_rows),
        "keep_browser_open": bool(config.get("keep_browser_open", False)),
        "keep_open_seconds": int(config.get("keep_open_seconds", 0) or 0),
        "extracted_at": datetime.now().isoformat(timespec="seconds"),
        **snapshot_meta,
    }
    meta.update(_notify_vinted_profile_report(output_rows, meta, config))
    _log_vinted_profile(
        "profile-output-ready",
        profile_url=profile_url,
        member_id=member_id,
        output_rows=len(output_rows),
        discord_report=meta.get("discord_profile_report", False),
        discord_sent=meta.get("discord_profile_report_sent", False),
        discord_error=meta.get("discord_profile_report_error", ""),
    )
    ui_result_json = str(config.get("ui_result_json", "") or "").strip()
    if ui_result_json:
        ui_result_path = Path(ui_result_json).expanduser()
        ui_result_path.parent.mkdir(parents=True, exist_ok=True)
        write_outcome_json(ui_result_path, ScrapeOutcome(source="vinted_profile", rows=output_rows, meta=meta))
        _log_vinted_profile("profile-ui-result-written", path=str(ui_result_path), output_rows=len(output_rows))
    return {"rows": output_rows, "meta": meta}


def _check_profile_listing_status(
    driver: Driver,
    rows: list[dict],
    *,
    page_settle_seconds: float,
) -> list[dict]:
    checked_rows: list[dict] = []
    _log_vinted_profile("profile-missing-item-status-check-start", items=len(rows))
    for index, row in enumerate(rows, start=1):
        link = str(row.get("link", "") or "").strip()
        enriched = dict(row)
        if not link:
            enriched["profile_item_status"] = "unknown"
            checked_rows.append(enriched)
            continue
        try:
            driver.get(link, wait=Wait.SHORT, timeout=VINTED_PROFILE_NAVIGATION_TIMEOUT_SECONDS)
            time.sleep(min(max(page_settle_seconds, 0.25), 1.0))
            status = _sold_status_from_page(driver)
            current_url = str(status.get("current_url", "") or "")
            item_id = str(enriched.get("item_id", "") or "")
            if bool(status.get("sold")):
                state = "sold"
            elif bool(status.get("page_not_found")):
                state = "unavailable"
            elif item_id and item_id not in current_url:
                state = "unknown"
            else:
                # The page is still live, but the item is absent from the
                # seller's profile: retain it as profile-gone, not sold.
                state = "gone"
            enriched["profile_item_status"] = state
            enriched["profile_page_status"] = "active" if state == "gone" else state
            enriched["profile_status_checked_at"] = datetime.now().isoformat(timespec="seconds")
            if state == "sold":
                listing_age_text = str(status.get("published_text", "") or "").strip()
                listing_age_seconds = parse_vinted_precise_listing_age_seconds(listing_age_text)
                if listing_age_seconds is not None:
                    enriched["profile_sold_after_seconds"] = listing_age_seconds
                    enriched["profile_sold_after_text"] = format_vinted_elapsed_seconds(listing_age_seconds)
                    enriched["profile_sold_duration_source"] = "listing_age"
                else:
                    first_seen = str(enriched.get("profile_first_seen_at", "") or "")
                    try:
                        elapsed = max(int((datetime.now() - datetime.fromisoformat(first_seen)).total_seconds()), 0)
                    except ValueError:
                        elapsed = 0
                    enriched["profile_sold_after_seconds"] = elapsed
                    enriched["profile_sold_after_text"] = format_vinted_elapsed_seconds(elapsed)
                    enriched["profile_sold_duration_source"] = "profile_first_seen"
                enriched["profile_sold_at"] = datetime.now().isoformat(timespec="seconds")
            _log_vinted_profile(
                "profile-missing-item-status-checked",
                index=index,
                total=len(rows),
                item_id=enriched.get("item_id", ""),
                status=state,
                link=link,
            )
        except Exception as exc:
            # A failed navigation is not evidence that an item sold or disappeared.
            enriched["profile_item_status"] = "unknown"
            _log_vinted_profile("profile-missing-item-status-error", index=index, item_id=enriched.get("item_id", ""), error=exc)
        checked_rows.append(enriched)
    _log_vinted_profile(
        "profile-missing-item-status-check-finished",
        checked=len(checked_rows),
        sold=sum(row.get("profile_item_status") == "sold" for row in checked_rows),
        live_but_missing=sum(
            row.get("profile_item_status") == "gone" and row.get("profile_page_status") == "active"
            for row in checked_rows
        ),
        unresolved=sum(row.get("profile_item_status") in {"unknown", "unavailable"} for row in checked_rows),
    )
    return checked_rows


def _attach_vinted_profile_identity(rows: list[dict], *, profile_url: str, member_id: str) -> list[dict]:
    enriched_rows: list[dict] = []
    for row in rows:
        enriched = dict(row)
        enriched["profile_url"] = str(enriched.get("profile_url", "") or profile_url)
        enriched["member_id"] = str(enriched.get("member_id", "") or member_id)
        enriched_rows.append(enriched)
    return enriched_rows


def _vinted_profile_rows_missing_from_current(
    *, current_rows: list[dict], historical_rows: list[dict]
) -> list[dict]:
    """Return only historic listing IDs no longer found in the latest profile response."""
    current_links = {
        str(row.get("link", "") or "").strip()
        for row in current_rows
        if str(row.get("link", "") or "").strip()
    }
    missing_by_link: dict[str, dict] = {}
    for row in historical_rows:
        link = str(row.get("link", "") or "").strip()
        status = str(row.get("profile_item_status", "") or "").strip().lower()
        page_status = str(row.get("profile_page_status", "") or "").strip().lower()
        already_detail_checked = bool(row.get("profile_status_checked_at")) and page_status in {"active", "sold", "unavailable"}
        if link and link not in current_links and status not in {"sold", "unavailable"} and not already_detail_checked:
            missing_by_link.setdefault(link, dict(row))
    return list(missing_by_link.values())


def _build_vinted_profile_aggregate_meta(
    *,
    profile_urls: list[str],
    profile_metas: list[dict],
    cycle_index: int,
    interval_seconds: int,
    db_path: str,
) -> dict[str, object]:
    return {
        "profile_urls": profile_urls,
        "profile_count": len(profile_urls),
        "profile_url": profile_urls[0] if len(profile_urls) == 1 else f"{len(profile_urls)} profili Vinted",
        "member_id": "",
        "cycle_index": cycle_index,
        "interval_seconds": interval_seconds,
        "db_path": str(db_path or DEFAULT_VINTED_DB_PATH),
        "row_count": sum(int(meta.get("row_count", 0) or 0) for meta in profile_metas),
        "profile_item_count": sum(int(meta.get("profile_item_count", 0) or 0) for meta in profile_metas),
        "profile_new_count": sum(int(meta.get("profile_new_count", 0) or 0) for meta in profile_metas),
        "profile_gone_count": sum(int(meta.get("profile_gone_count", 0) or 0) for meta in profile_metas),
        "profile_sold_count": sum(int(meta.get("profile_sold_count", 0) or 0) for meta in profile_metas),
        "profile_still_count": sum(int(meta.get("profile_still_count", 0) or 0) for meta in profile_metas),
        "profiles": profile_metas,
        "extracted_at": datetime.now().isoformat(timespec="seconds"),
        "db_saved_live": True,
    }


def _notify_vinted_profile_report(rows: list[dict], meta: dict, config: dict) -> dict[str, object]:
    if not bool(config.get("discord_profile_report", False)):
        return {
            "discord_profile_report": False,
            "discord_profile_report_sent": False,
            "discord_profile_report_error": "",
        }
    webhook_url = str(config.get("discord_webhook_url", "") or "").strip()
    if not webhook_url:
        return {
            "discord_profile_report": False,
            "discord_profile_report_sent": False,
            "discord_profile_report_error": "webhook Discord assente",
        }
    message = build_vinted_profile_report_discord_message(meta, rows)
    result = send_discord_webhook_message(webhook_url, message, embeds=_build_vinted_profile_report_embeds(rows))
    if bool(result.get("ok")):
        return {
            "discord_profile_report": True,
            "discord_profile_report_sent": True,
            "discord_profile_report_error": "",
            "discord_profile_report_sent_at": str(result.get("sent_at", "") or ""),
        }
    error = str(result.get("error", "") or "")
    print(f"[vinted-profile][discord] invio report fallito: {error}", flush=True)
    return {
        "discord_profile_report": True,
        "discord_profile_report_sent": False,
        "discord_profile_report_error": error,
    }


def _build_vinted_profile_report_embeds(rows: list[dict]) -> list[dict[str, object]]:
    embeds: list[dict[str, object]] = []
    for row in rows:
        if str(row.get("profile_item_status", "") or "") not in {"new", "gone", "sold"}:
            continue
        image_url = normalize_vinted_image_url(str(row.get("image_url", "") or ""))
        if not image_url:
            continue
        title = normalize_whitespace(str(row.get("name", "") or "Vinted"))
        link = str(row.get("link", "") or "").strip()
        embed: dict[str, object] = {
            "title": title[:256] if title else "Vinted",
            "image": {"url": image_url},
        }
        if link:
            embed["url"] = link
        embeds.append(embed)
        if len(embeds) >= 4:
            break
    return embeds


def _scroll_and_read_vinted_profile_items(
    driver: Driver,
    *,
    max_items: int,
    page_settle_seconds: float,
) -> list[dict]:
    # Profile grids use lazy loading and, on some accounts, virtualised cards.
    # Reaching the document bottom in one jump can therefore leave the grid at
    # its first page (usually 20 items).  Always let each incremental scroll
    # settle for three seconds, then read the cards again before proceeding.
    scroll_settle_seconds = 3.0
    rows_by_link: dict[str, dict] = {}
    stable_bottom_rounds = 0
    stable_stop_rounds = 6 if max_items <= 0 else 4
    max_rounds = 220 if max_items <= 0 else max(20, min(220, (max_items // 8) + 20))
    last_height = 0
    _log_vinted_profile("scroll-start", max_items=max_items, max_rounds=max_rounds)
    for _round in range(max_rounds):
        for row in _read_vinted_profile_items(driver):
            link = str(row.get("link", "") or "").strip()
            if not link:
                continue
            rows_by_link[link] = row
        if max_items > 0 and len(rows_by_link) >= max_items:
            _log_vinted_profile("scroll-stop-max-items", round=_round + 1, rows=len(rows_by_link), max_items=max_items)
            break
        before_count = len(rows_by_link)
        scroll_state = _scroll_vinted_profile(driver)
        moved = bool(scroll_state.get("moved"))
        at_bottom = bool(scroll_state.get("at_bottom"))
        load_more_clicked = bool(scroll_state.get("load_more_clicked"))
        page_height = int(scroll_state.get("height") or 0)
        height_changed_before_wait = bool(page_height and page_height != last_height)
        time.sleep(scroll_settle_seconds)
        for row in _read_vinted_profile_items(driver):
            link = str(row.get("link", "") or "").strip()
            if not link:
                continue
            rows_by_link[link] = row
        # Read the height only on the next scroll call.  An item increase is
        # the strongest signal that lazy content arrived; a changed height also
        # means we must continue even if this particular read was unchanged.
        added_count = max(len(rows_by_link) - before_count, 0)
        height_changed = height_changed_before_wait
        if page_height:
            last_height = page_height
        if at_bottom and added_count == 0 and not height_changed and not load_more_clicked:
            stable_bottom_rounds += 1
        else:
            stable_bottom_rounds = 0
        if added_count or stable_bottom_rounds >= 2 or (_round + 1) % 5 == 0:
            _log_vinted_profile(
                "scroll-round",
                round=_round + 1,
                rows=len(rows_by_link),
                added=added_count,
                moved=moved,
                at_bottom=at_bottom,
                height=page_height,
                height_changed=height_changed,
                load_more_clicked=load_more_clicked,
                stable_bottom_rounds=stable_bottom_rounds,
                settle_seconds=scroll_settle_seconds,
            )
        if stable_bottom_rounds >= stable_stop_rounds:
            _log_vinted_profile("scroll-stop-stable", round=_round + 1, rows=len(rows_by_link))
            break
    for verify_round in range(1, 4):
        scroll_state = _scroll_vinted_profile(driver)
        time.sleep(scroll_settle_seconds)
        before_count = len(rows_by_link)
        for row in _read_vinted_profile_items(driver):
            link = str(row.get("link", "") or "").strip()
            if not link:
                continue
            rows_by_link[link] = row
        added_count = max(len(rows_by_link) - before_count, 0)
        _log_vinted_profile(
            "scroll-verify",
            round=verify_round,
            rows=len(rows_by_link),
            added=added_count,
            at_bottom=bool(scroll_state.get("at_bottom")),
            height=int(scroll_state.get("height") or 0),
            settle_seconds=scroll_settle_seconds,
        )
        if max_items > 0 and len(rows_by_link) >= max_items:
            break
    rows = list(rows_by_link.values())
    if max_items > 0:
        rows = rows[:max_items]
    _log_vinted_profile("scroll-finished", rows=len(rows))
    return rows


def _read_vinted_profile_declared_item_count(driver: Driver) -> int:
    try:
        count = driver.run_js(
            """
const text = (document.body?.innerText || '').replace(/\\s+/g, ' ');
const matches = [...text.matchAll(/(?:^|\\s)([0-9]{1,5})\\s+articoli\\b/gi)];
return matches.length ? Number(matches[matches.length - 1][1]) : 0;
            """
        )
        return max(int(count or 0), 0)
    except Exception:
        return 0


def _read_vinted_profile_additional_pages(
    driver: Driver,
    *,
    profile_url: str,
    existing_rows: list[dict],
    declared_item_count: int,
    max_items: int,
) -> list[dict]:
    """Collect remaining public wardrobe pages when the profile advertises more than its first grid."""
    rows_by_link = {
        str(row.get("link", "") or "").strip(): row
        for row in existing_rows
        if str(row.get("link", "") or "").strip()
    }
    if max_items > 0 and len(rows_by_link) >= max_items:
        return list(rows_by_link.values())[:max_items]
    if declared_item_count <= len(rows_by_link):
        return list(rows_by_link.values())
    # The public profile route serves twenty cards per page.  The stated total
    # gives a bounded loop; the duplicate-page guard handles layouts that do
    # not honour the `page` query parameter.
    page_size = 20
    max_pages = min(max((declared_item_count + page_size - 1) // page_size, 2), 100)
    _log_vinted_profile(
        "profile-pages-start",
        declared_items=declared_item_count,
        initial_rows=len(rows_by_link),
        max_pages=max_pages,
    )
    duplicate_pages = 0
    for page in range(2, max_pages + 1):
        if consume_stop_after_current_item_request():
            break
        page_url = f"{profile_url}?page={page}"
        try:
            driver.get(page_url, wait=Wait.SHORT, timeout=VINTED_PROFILE_NAVIGATION_TIMEOUT_SECONDS)
            # Required settle time: page cards can arrive after navigation has
            # completed, exactly like the lazy cards on page one.
            time.sleep(3.0)
            page_rows = _read_vinted_profile_items(driver)
        except Exception as error:
            _log_vinted_profile("profile-page-error", page=page, error=type(error).__name__)
            break
        before_count = len(rows_by_link)
        for row in page_rows:
            link = str(row.get("link", "") or "").strip()
            if link:
                rows_by_link[link] = row
        added_count = len(rows_by_link) - before_count
        _log_vinted_profile(
            "profile-page",
            page=page,
            rows=len(page_rows),
            added=added_count,
            total=len(rows_by_link),
        )
        if max_items > 0 and len(rows_by_link) >= max_items:
            break
        if added_count == 0:
            duplicate_pages += 1
            if duplicate_pages >= 2:
                _log_vinted_profile("profile-pages-stop-duplicate", page=page, total=len(rows_by_link))
                break
        else:
            duplicate_pages = 0
        if len(rows_by_link) >= declared_item_count:
            break
    rows = list(rows_by_link.values())
    return rows[:max_items] if max_items > 0 else rows


def _read_all_vinted_profile_items_via_browser_session(
    driver: Driver,
    *,
    member_id: str,
    max_items: int,
) -> list[dict]:
    """Read every public wardrobe page through the already-open browser session.

    The endpoint is same-origin, so its cookies and anti-bot context come from
    the Chrome tab rather than from a separate HTTP client.  If it is rejected
    or changes shape, the caller simply keeps the DOM-scroll result.
    """
    normalized_member_id = normalize_vinted_member_id(member_id)
    if not normalized_member_id:
        return []
    per_page = 96
    max_pages = max(1, (max_items + per_page - 1) // per_page) if max_items > 0 else 30
    session_headers = _read_vinted_wardrobe_session_headers(driver)
    if not session_headers.get("x-csrf-token") or not session_headers.get("x-anon-id"):
        _log_vinted_profile("profile-api-session-headers-missing", keys=sorted(session_headers))
        return []
    collected: dict[str, dict] = {}
    for page in range(1, max_pages + 1):
        endpoint = (
            f"{VINTED_BASE_URL}/api/v2/wardrobe/{normalized_member_id}/items"
            f"?order=newest_first&page={page}&per_page={per_page}"
        )
        try:
            response = driver.run_js(
                """
const endpoint = args.endpoint;
const captured = args.headers || window.__agentLabWardrobeHeaders || {};
const pick = (...names) => {
  for (const name of names) if (captured[name]) return captured[name];
  return '';
};
// Botasaurus waits unreliably for a fetch Promise.  The browser's synchronous
// XHR runs in this already-authorised page context and returns a concrete
// object to Python for every wardrobe page.
try {
  const request = new XMLHttpRequest();
  request.open('GET', endpoint, false);
  request.setRequestHeader('accept', 'application/json, text/plain, */*');
  request.setRequestHeader('x-requested-with', 'XMLHttpRequest');
  const csrf = pick('x-csrf-token', 'X-CSRF-Token');
  const anonId = pick('x-anon-id', 'X-Anon-Id');
  if (csrf) request.setRequestHeader('x-csrf-token', csrf);
  if (anonId) request.setRequestHeader('x-anon-id', anonId);
  request.setRequestHeader('locale', pick('locale', 'Locale') || 'it-IT');
  request.send(null);
  return { status: request.status, text: request.responseText || '' };
} catch (error) {
  return { error: String(error || 'xhr failed') };
}
                """,
                args={"endpoint": endpoint, "headers": session_headers},
            )
            if not isinstance(response, dict):
                _log_vinted_profile("profile-api-page-invalid", page=page, payload_type=type(response).__name__)
                break
            if bool(response.get("timeout")):
                _log_vinted_profile("profile-api-page-timeout", page=page, timeout_seconds=12)
                break
            if response.get("error"):
                _log_vinted_profile("profile-api-page-error", page=page, error=str(response.get("error"))[:120])
                break
            status_code = int(response.get("status", 0) or 0)
            if status_code != 200:
                _log_vinted_profile("profile-api-page-unavailable", page=page, status=status_code)
                break
            payload = json.loads(str(response.get("text", "") or ""))
        except Exception as error:
            _log_vinted_profile("profile-api-page-error", page=page, error=type(error).__name__)
            break
        if not isinstance(payload, dict):
            _log_vinted_profile("profile-api-page-invalid", page=page, payload_type=type(payload).__name__)
            break
        items = payload.get("items")
        if not isinstance(items, list) or not items:
            _log_vinted_profile("profile-api-page-empty", page=page, collected=len(collected))
            break
        page_added = 0
        for item in items:
            row = _normalize_vinted_profile_api_item(item)
            link = str(row.get("link", "") or "").strip()
            if not link:
                continue
            if link not in collected:
                page_added += 1
            collected[link] = row
            if max_items > 0 and len(collected) >= max_items:
                break
        _log_vinted_profile(
            "profile-api-page",
            page=page,
            received=len(items),
            added=page_added,
            collected=len(collected),
        )
        if max_items > 0 and len(collected) >= max_items:
            break
        pagination = payload.get("pagination") if isinstance(payload.get("pagination"), dict) else {}
        total_pages = int(pagination.get("total_pages", 0) or 0)
        current_page = int(pagination.get("current_page", page) or page)
        if total_pages and current_page >= total_pages:
            break
        if not total_pages and len(items) < per_page:
            break
    return list(collected.values())[:max_items] if max_items > 0 else list(collected.values())


def _read_vinted_wardrobe_session_headers(driver: Driver) -> dict[str, str]:
    """Read the page bootstrap values used by Vinted's own wardrobe XHR."""
    try:
        payload = driver.run_js(
            """
const markup = (document.documentElement?.innerHTML || '').split(String.fromCharCode(92, 34)).join('"');
const csrf = (markup.match(/"CSRF_TOKEN":"([^"]+)"/) || [])[1] || '';
const cookieAnon = (document.cookie.match(/(?:^|;\\s*)anon_id=([^;]+)/) || [])[1] || '';
const bootstrapAnon = (markup.match(/"anonId":"([^"]+)"/) || [])[1] || '';
return { csrf, anonId: decodeURIComponent(cookieAnon || bootstrapAnon || '') };
            """
        )
    except Exception as error:
        _log_vinted_profile("profile-api-session-header-error", error=type(error).__name__)
        return {}
    if not isinstance(payload, dict):
        return {}
    csrf = str(payload.get("csrf", "") or "").strip()
    anon_id = str(payload.get("anonId", "") or "").strip()
    return {
        "x-csrf-token": csrf,
        "x-anon-id": anon_id,
        "locale": "it-IT",
    }


def _normalize_vinted_profile_api_item(item: object) -> dict:
    source = item if isinstance(item, dict) else {}
    item_id = str(source.get("id", source.get("item_id", "")) or "").strip()
    raw_link = str(source.get("url", source.get("link", "")) or "").strip()
    if raw_link and raw_link.startswith("/"):
        raw_link = f"{VINTED_BASE_URL}{raw_link}"
    link = normalize_vinted_item_url(raw_link)
    if not link and item_id:
        link = f"{VINTED_BASE_URL}/items/{item_id}"
    photo = source.get("photo") if isinstance(source.get("photo"), dict) else {}
    image_url = (
        source.get("image_url")
        or source.get("thumbnail_url")
        or photo.get("url")
        or photo.get("full_size_url")
        or photo.get("thumbnail_url")
        or ""
    )
    price = source.get("price") or source.get("price_numeric") or ""
    currency = str(source.get("currency", "") or "").strip()
    price_text = normalize_whitespace(f"{price} {currency}".strip())
    category = _vinted_profile_item_category(source)
    return {
        "source": "vinted_profile",
        "profile_url": "",
        "member_id": "",
        "item_id": extract_vinted_item_id_from_link(link) or item_id,
        "name": normalize_whitespace(str(source.get("title", source.get("name", "")) or "")) or link,
        "price": price_text,
        "link": link,
        "image_url": normalize_vinted_image_url(str(image_url or "")),
        "raw_text": normalize_whitespace(str(source.get("description", "") or "")),
        "category": category,
        "profile_item_status": "current",
        "extracted_at": datetime.now().isoformat(timespec="seconds"),
    }


def _vinted_profile_item_category(source: dict) -> str:
    """Extract the most specific public category label across wardrobe API variants."""
    direct_values = (
        source.get("catalog_name"), source.get("category_name"), source.get("catalog_title"),
        source.get("category_title"), source.get("catalog"), source.get("category"),
    )
    for value in direct_values:
        if isinstance(value, str) and normalize_whitespace(value):
            return normalize_whitespace(value)
        if isinstance(value, dict):
            for key in ("title", "name", "label"):
                text = normalize_whitespace(str(value.get(key, "") or ""))
                if text:
                    return text
    path = source.get("catalog_path", source.get("category_path", []))
    if isinstance(path, list):
        for value in reversed(path):
            if isinstance(value, str) and normalize_whitespace(value):
                return normalize_whitespace(value)
            if isinstance(value, dict):
                text = normalize_whitespace(str(value.get("title", value.get("name", "")) or ""))
                if text:
                    return text
    # Some wardrobe responses expose only a catalog id. Keep a conservative
    # product-name fallback so category filtering remains usable while the
    # profile's richer catalog labels are unavailable.
    title = normalize_whitespace(str(source.get("title", source.get("name", "")) or "")).casefold()
    inferred = (
        (("charm", "ciondolo", "bedel"), "Charms e ciondoli"),
        (("braccial", "bracelet", "bangle"), "Braccialetti"),
        (("collan", "necklace", "catena"), "Collane"),
        (("anello", "ring"), "Anelli"),
        (("orecchin", "earring"), "Orecchini"),
        (("spilla", "brooch", "pin"), "Spille"),
    )
    for keywords, label in inferred:
        if any(keyword in title for keyword in keywords):
            return label
    return "Altro"


def _normalize_vinted_profile_categories(value: object) -> list[str]:
    labels = [normalize_whitespace(part) for part in re.split(r"[,\n;]+", str(value or ""))]
    return list(dict.fromkeys(label for label in labels if label and label.casefold() not in {"all", "tutte", "tutti"}))


def _filter_vinted_profile_rows_by_category(rows: list[dict], categories: list[str]) -> list[dict]:
    expected = {label.casefold() for label in categories}
    selected: list[dict] = []
    for row in rows:
        category = normalize_whitespace(str(row.get("category", "") or ""))
        if not category:
            category = _vinted_profile_item_category(row)
        searchable = " ".join(
            normalize_whitespace(str(row.get(key, "") or ""))
            for key in ("name", "title", "brand", "category_path")
        ).casefold()
        category_key = category.casefold()
        if category_key in expected or any(label in category_key or label in searchable for label in expected):
            selected.append(row)
    return selected


def _read_vinted_profile_items(driver: Driver) -> list[dict]:
    payload = driver.run_js(
        """
const clean = (value) => (value || '').replace(/\\s+/g, ' ').trim();
const links = [...document.querySelectorAll('a[href*="/items/"]')];
return links.map((link) => {
  const root = link.closest('[data-testid^="product-item-id-"], [data-testid^="grid-item"], article')
    || link.parentElement?.parentElement?.parentElement
    || link.parentElement
    || link;
  const title = root.querySelector('[data-testid*="description-title"], [data-testid*="item-title"], [data-testid*="description-preview"]');
  const price = root.querySelector('[data-testid*="price-text"], [data-testid*="item-price"]');
  // Product-card links for text and photo can be siblings rather than one
  // containing the other. Search the card plus its near ancestors.
  const cardScopes = [root, link];
  let ancestor = link.parentElement;
  for (let index = 0; ancestor && index < 6; index += 1, ancestor = ancestor.parentElement) cardScopes.push(ancestor);
  const firstMatch = (selector) => cardScopes.map((scope) => scope.querySelector(selector)).find(Boolean);
  // Product-card image markup varies: not every image has an alt attribute
  // and lazy-loaded cards can expose only data-srcset/srcset.
  const image = firstMatch('img, picture source, source');
  const imageUrl = (node) => {
    if (!node) return '';
    const srcset = node.getAttribute('srcset') || node.getAttribute('data-srcset') || '';
    return node.currentSrc || node.src || node.getAttribute('src') || node.getAttribute('data-src') ||
      ((srcset.split(',')[0] || '').trim().split(/\\s+/)[0]) || '';
  };
  const styleNode = firstMatch('[style*="background-image"], [style*="background: url"], [style*="background:url"]')
    || cardScopes.find((scope) => /url\\(/i.test(scope.getAttribute('style') || ''));
  const styleUrl = styleNode ? ((styleNode.getAttribute('style') || '').match(/url\\(["']?([^"')]+)["']?\\)/i) || [])[1] || '' : '';
  const imageSrc = imageUrl(image) || styleUrl;
  return {
    link: link.href || link.getAttribute('href') || '',
    title: clean(title ? (title.innerText || title.textContent) : (link.getAttribute('aria-label') || link.getAttribute('title') || '')),
    price: clean(price ? (price.innerText || price.textContent) : ''),
    image_url: imageSrc,
    image_alt: clean(image ? image.getAttribute('alt') : ''),
    raw_text: clean(root.innerText || root.textContent),
  };
});
        """
    )
    if not isinstance(payload, list):
        return []
    rows: list[dict] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        link = normalize_vinted_item_url(str(item.get("link", "") or ""))
        if not link:
            continue
        item_id = extract_vinted_item_id_from_link(link)
        title = normalize_whitespace(str(item.get("title", "") or ""))
        if not title:
            title = normalize_whitespace(str(item.get("image_alt", "") or ""))
        rows.append(
            {
                "source": "vinted_profile",
                "profile_url": "",
                "member_id": "",
                "item_id": item_id,
                "name": title or link,
                "price": normalize_whitespace(str(item.get("price", "") or "")),
                "link": link,
                "image_url": normalize_vinted_image_url(str(item.get("image_url", "") or "")),
                "raw_text": normalize_whitespace(str(item.get("raw_text", "") or "")),
                "profile_item_status": "current",
                "extracted_at": datetime.now().isoformat(timespec="seconds"),
            }
        )
    return rows


def _scroll_vinted_profile(driver: Driver) -> dict[str, object]:
    try:
        payload = driver.run_js(
            """
const root = document.scrollingElement || document.documentElement || document.body;
const cards = [...document.querySelectorAll('a[href*="/items/"]')];
const lastCard = cards[cards.length - 1] || null;
const findScrollableAncestor = (element) => {
  let node = element ? element.parentElement : null;
  while (node && node !== document.body) {
    const style = window.getComputedStyle(node);
    if (node.scrollHeight > node.clientHeight + 24 && /(auto|scroll|overlay)/.test(style.overflowY || '')) return node;
    node = node.parentElement;
  }
  return null;
};
const scrollContainer = findScrollableAncestor(lastCard);
const before = scrollContainer ? scrollContainer.scrollTop : (window.scrollY || root.scrollTop || 0);
const viewport = scrollContainer ? scrollContainer.clientHeight : (window.innerHeight || root.clientHeight || 0);
const heightBefore = Math.max(
  root.scrollHeight || 0,
  document.body ? document.body.scrollHeight || 0 : 0,
  document.documentElement ? document.documentElement.scrollHeight || 0 : 0
);
// A direct jump to the bottom does not trigger every lazy-load threshold on
// Vinted profile grids.  Move roughly one viewport at a time instead.
const maxScrollBefore = scrollContainer
  ? Math.max(0, scrollContainer.scrollHeight - scrollContainer.clientHeight)
  : Math.max(0, heightBefore - viewport);
const step = Math.max(640, Math.floor(viewport * 0.85));
const target = Math.min(maxScrollBefore, before + step);
if (scrollContainer) {
  scrollContainer.scrollTo({ top: target, behavior: 'smooth' });
  scrollContainer.dispatchEvent(new Event('scroll', { bubbles: true }));
  scrollContainer.dispatchEvent(new WheelEvent('wheel', { deltaY: step, bubbles: true, cancelable: true }));
} else {
  // Chrome's minimized automation window does not advance smooth scrolling.
  // Use a deterministic move, then give the page the required three seconds.
  window.scrollTo({ top: target, behavior: 'instant' });
  root.scrollTop = target;
  window.dispatchEvent(new Event('scroll'));
  document.dispatchEvent(new Event('scroll'));
  // The observer used by some grids reacts to an actual wheel event rather
  // than to scrollTop alone.
  window.dispatchEvent(new WheelEvent('wheel', { deltaY: step, bubbles: true, cancelable: true }));
}
const after = scrollContainer ? scrollContainer.scrollTop : (window.scrollY || root.scrollTop || 0);
const heightAfter = Math.max(
  root.scrollHeight || 0,
  document.body ? document.body.scrollHeight || 0 : 0,
  document.documentElement ? document.documentElement.scrollHeight || 0 : 0
);
const maxScroll = scrollContainer
  ? Math.max(0, scrollContainer.scrollHeight - scrollContainer.clientHeight)
  : Math.max(0, heightAfter - viewport);
const atBottom = after >= maxScroll - 8;
const isVisible = (element) => {
  const style = window.getComputedStyle(element);
  return style.display !== 'none' && style.visibility !== 'hidden' && element.getClientRects().length > 0;
};
const normalize = (value) => (value || '').replace(/\\s+/g, ' ').trim().toLowerCase();
// Some profile layouts expose the next group with a button instead of an
// infinite grid.  Treat it as part of the same extraction loop, never as a
// navigation action chosen by the user.
let loadMoreClicked = false;
if (atBottom) {
  const control = [...document.querySelectorAll('button, a, [role="button"]')].find((element) => {
    if (!isVisible(element) || element.disabled || element.getAttribute('aria-disabled') === 'true') return false;
    const testId = normalize(element.getAttribute('data-testid'));
    const label = normalize(element.getAttribute('aria-label'));
    const text = normalize(element.innerText || element.textContent);
    const value = `${testId} ${label} ${text}`;
    return /load[ -]?more|show[ -]?more|mostra( altri| altro| di più| di piu)|vedi( altri| altro| di più| di piu)|carica( altri| altro| di più| di piu)|next page|pagina successiva/.test(value);
  });
  if (control) {
    try {
      control.scrollIntoView({ block: 'center', inline: 'nearest' });
      control.click();
      loadMoreClicked = true;
    } catch (error) {}
  }
}
return {
  before,
  after: window.scrollY || root.scrollTop || 0,
  height: heightAfter,
  viewport,
  max_scroll: maxScroll,
  scroll_container: scrollContainer ? 'nested' : 'document',
  moved: Math.abs(after - before) > 2,
  at_bottom: atBottom,
  load_more_clicked: loadMoreClicked,
};
            """
        )
        return payload if isinstance(payload, dict) else {"moved": False, "at_bottom": False, "height": 0}
    except Exception:
        return {"moved": False, "at_bottom": False, "height": 0}


def normalize_vinted_profile_url(profile_url: object) -> str:
    value = str(profile_url or "").strip()
    if not value:
        return ""
    if value.isdigit():
        value = f"{VINTED_BASE_URL}/member/{value}"
    parsed = urlsplit(value)
    if not parsed.netloc:
        parsed = urlsplit(f"{VINTED_BASE_URL}/{value.lstrip('/')}")
    if "/member/" not in parsed.path:
        return ""
    return urlunsplit((parsed.scheme or "https", parsed.netloc or urlsplit(VINTED_BASE_URL).netloc, parsed.path, "", ""))


def normalize_vinted_profile_urls(profile_urls: object) -> list[str]:
    def split_profile_url_text(value: object) -> list[str]:
        raw_value = str(value or "")
        raw_value = raw_value.replace("\\n", "\n").replace("\\r", "\n")
        raw_value = re.sub(r"/n(?=https?://|www\.vinted\.|vinted\.|member/|\d)", "\n", raw_value, flags=re.IGNORECASE)
        raw_value = re.sub(r"(?<!^)(?=https?://(?:www\.)?vinted\.)", "\n", raw_value, flags=re.IGNORECASE)
        return [
            part.strip()
            for chunk in raw_value.replace("\r", "\n").replace(";", "\n").split("\n")
            for part in chunk.split(",")
            if part.strip()
        ]

    if isinstance(profile_urls, (list, tuple, set)):
        raw_items = [
            part
            for item in profile_urls
            for part in split_profile_url_text(item)
        ]
    else:
        raw_value = str(profile_urls or "")
        try:
            parsed_json = json.loads(raw_value) if raw_value.strip().startswith("[") else None
        except json.JSONDecodeError:
            parsed_json = None
        if isinstance(parsed_json, list):
            raw_items = [
                part
                for item in parsed_json
                for part in split_profile_url_text(item)
            ]
        else:
            raw_items = split_profile_url_text(raw_value)
    normalized: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        profile_url = normalize_vinted_profile_url(item)
        if not profile_url or profile_url in seen:
            continue
        seen.add(profile_url)
        normalized.append(profile_url)
    return normalized


def _prioritize_profile_rows(rows: list[dict]) -> list[dict]:
    rank = {"new": 0, "gone": 1, "still_active": 2, "current": 3}
    return sorted(
        rows,
        key=lambda row: (
            rank.get(str(row.get("profile_item_status", "") or ""), 9),
            str(row.get("name", "") or "").lower(),
            str(row.get("link", "") or ""),
        ),
    )


def _nonnegative_float(value: object, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return max(float(default), 0.0)
    return max(parsed, 0.0)
