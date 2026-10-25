"""Public-page lifecycle checks for deals previously sent by the deal hunter."""

from __future__ import annotations

import time
from datetime import datetime, timedelta

from botasaurus.browser import Driver, Wait, browser

from ..browser_runtime import resolve_browser_arguments, resolve_browser_profile
from ..discord_notifications import build_vinted_deal_sold_discord_message, send_discord_webhook_message
from ..models import ScrapeOutcome
from ..vinted_database import (
    DEFAULT_VINTED_DB_PATH,
    delete_vinted_item_by_link,
    format_vinted_elapsed_seconds,
    load_due_vinted_deal_sale_checks,
    parse_vinted_precise_listing_age_seconds,
    save_vinted_deal_sale_check,
)
from ..vinted_tabs import navigate_vinted_status_tab, restore_vinted_primary_tab

VINTED_DEAL_SALE_NAVIGATION_TIMEOUT_SECONDS = 18


def _sold_status_from_page(driver: Driver) -> dict[str, object]:
    result = driver.run_js(
        """
const clean = (value) => (value || '').replace(/\\s+/g, ' ').trim();
const body = clean(document.body ? (document.body.innerText || document.body.textContent) : '');
const h1Text = clean(document.querySelector('h1')?.innerText || document.querySelector('h1')?.textContent || '');
const h1NotFound = /^(?:page\\s+)?not found$/i.test(h1Text) || /^pagina non trovata$/i.test(h1Text);
const notFoundMedia = document.querySelector(
  'video[poster*="error-page/notfound"], video source[src*="error-page/notfound"], img[src*="error-page/notfound"]'
);
const soldCell = [...document.querySelectorAll('.web_ui__Cell__cell.web_ui__Cell__success .web_ui__Cell__body, .web_ui__Cell__body')]
  .find((node) => /^venduto$/i.test(clean(node.innerText || node.textContent)));
let publishedText = '';
for (const node of document.querySelectorAll('span, div, p, li')) {
  const text = clean(node.innerText || node.textContent || '');
  if (!text || text.length > 60) continue;
  if (/^(?:caricato\\s+)?(?:meno di un['’]?ora|un['’]?ora|\\d+\\s+(?:minut[oi]?|or[ae]|giorn[oi]))\\s+fa$/i.test(text)) {
    publishedText = clean(text.replace(/^caricato\\s+/i, ''));
    break;
  }
}
return {
  sold: Boolean(soldCell),
  page_not_found: Boolean(notFoundMedia) || h1NotFound || /page not found|pagina non trovata/i.test(body),
  title: h1Text,
  published_text: publishedText,
  current_url: window.location.href || '',
};
        """
    )
    return result if isinstance(result, dict) else {}


@browser(
    profile=resolve_browser_profile,
    add_arguments=resolve_browser_arguments,
    wait_for_complete_page_load=False,
    close_on_crash=True,
    max_retry=2,
    retry_wait=1,
    raise_exception=True,
)
def _check_vinted_deal_sales_task(driver: Driver, config: dict) -> dict:
    deals = load_due_vinted_deal_sale_checks(
        config["db_path"],
        due_before=str(config["due_before"]),
        limit=int(config["max_items"]),
        first_seen_after=str(config.get("first_seen_after", "") or ""),
    )
    print(f"[vinted-deal-sales] check-start due={len(deals)}", flush=True)
    return _check_vinted_deal_sales_with_driver(driver, config, deals)


def _check_vinted_deal_sales_with_driver(
    driver: Driver,
    config: dict,
    deals: list[dict],
    *,
    use_status_tab: bool = False,
) -> dict:
    rows: list[dict] = []
    checked = sold_count = errors = notified = 0
    webhook_url = str(config.get("discord_webhook_url", "") or "").strip()
    for deal in deals:
        link = str(deal.get("link", "") or "").strip()
        if not link:
            continue
        try:
            if not use_status_tab or not navigate_vinted_status_tab(
                driver,
                link,
                log_prefix="[vinted-deal-sales-tabs]",
            ):
                driver.get(link, wait=Wait.SHORT, timeout=VINTED_DEAL_SALE_NAVIGATION_TIMEOUT_SECONDS)
            time.sleep(min(max(float(config.get("page_settle_seconds", 2.0) or 0), 0.25), 2.0))
            status = _sold_status_from_page(driver)
            # A missing/inaccessible page is deliberately not counted as sold: only the explicit cell is proof.
            if bool(status.get("page_not_found")):
                archived = delete_vinted_item_by_link(link, db_path=config["db_path"])
                checked += 1
                print(
                    f"[vinted-deal-sales] page-not-found archived link={link} "
                    f"archived={archived.get('archived', 0)} item_id={archived.get('item_id', '')}",
                    flush=True,
                )
                continue
            now = datetime.now()
            row = {
                **deal,
                "name": str(status.get("title") or deal.get("name", "")),
                "link": link,
                "published_at": str(status.get("published_text", "") or deal.get("published_at", "")),
            }
            is_sold = bool(status.get("sold"))
            if is_sold:
                listing_age_text = str(status.get("published_text", "") or "").strip()
                listing_age_seconds = parse_vinted_precise_listing_age_seconds(listing_age_text)
                if listing_age_seconds is not None:
                    duration_seconds = listing_age_seconds
                    duration_source = "listing_age"
                else:
                    try:
                        first_seen = datetime.fromisoformat(str(deal.get("deal_first_seen_at", "") or ""))
                        duration_seconds = max(int((now - first_seen).total_seconds()), 0)
                    except ValueError:
                        duration_seconds = 0
                    duration_source = "deal_discovery"
                row.update(
                    {
                        "deal_sale_duration_seconds": duration_seconds,
                        "deal_sale_duration_source": duration_source,
                        "deal_listing_age_text": listing_age_text,
                        "deal_sold_after_text": format_vinted_elapsed_seconds(duration_seconds),
                    }
                )
            persisted = save_vinted_deal_sale_check(row, sold=is_sold, checked_at=now.isoformat(timespec="seconds"), db_path=config["db_path"])
            checked += 1
            if is_sold:
                sold_count += 1
                row["deal_sold_at"] = str(persisted.get("sold_at", ""))
                if webhook_url:
                    result = send_discord_webhook_message(webhook_url, build_vinted_deal_sold_discord_message(row))
                    if bool(result.get("ok")):
                        notified += 1
                    else:
                        print(f"[vinted-deal-sales] discord-failed link={link} error={result.get('error', '')}", flush=True)
            rows.append({**row, "deal_sale_status": "sold" if is_sold else "active"})
            print(
                f"[vinted-deal-sales] checked sold={is_sold} "
                f"duration={row.get('deal_sold_after_text', '')} "
                f"source={row.get('deal_sale_duration_source', '')} link={link}",
                flush=True,
            )
        except Exception as exc:
            errors += 1
            print(f"[vinted-deal-sales] check-error link={link} error={exc}", flush=True)
    if use_status_tab:
        restore_vinted_primary_tab(driver, log_prefix="[vinted-deal-sales-tabs]")
    return {"rows": rows, "meta": {"due_count": len(deals), "checked_count": checked, "sold_count": sold_count, "errors": errors, "discord_sent": notified}}


def check_due_vinted_deal_sales_with_driver(
    driver: Driver,
    *,
    db_path: str,
    interval_seconds: int = 1800,
    max_items: int = 40,
    discord_webhook_url: str = "",
    page_settle_seconds: float = 2.0,
    max_tracking_age_hours: float = 72.0,
) -> dict:
    """Check due sold/active states in the monitoring task's second tab."""
    now = datetime.now()
    due_before = now - timedelta(seconds=max(int(interval_seconds or 0), 0))
    max_age_hours = max(float(max_tracking_age_hours or 0), 0.0)
    first_seen_after = (now - timedelta(hours=max_age_hours)).isoformat(timespec="seconds") if max_age_hours > 0 else ""
    normalized_db_path = str(db_path or DEFAULT_VINTED_DB_PATH)
    due_before_text = due_before.isoformat(timespec="seconds")
    deals = load_due_vinted_deal_sale_checks(
        normalized_db_path,
        due_before=due_before_text,
        limit=max(int(max_items or 0), 1),
        first_seen_after=first_seen_after,
    )
    print(f"[vinted-deal-sales] in-search-tab check-start due={len(deals)}", flush=True)
    if not deals:
        return {
            "due_count": 0,
            "checked_count": 0,
            "sold_count": 0,
            "errors": 0,
            "discord_sent": 0,
            "max_tracking_age_hours": max_age_hours,
        }
    config = {
        "db_path": normalized_db_path,
        "due_before": due_before_text,
        "first_seen_after": first_seen_after,
        "max_items": max(int(max_items or 0), 1),
        "discord_webhook_url": discord_webhook_url,
        "page_settle_seconds": page_settle_seconds,
    }
    result = _check_vinted_deal_sales_with_driver(driver, config, deals, use_status_tab=True)
    result["meta"]["max_tracking_age_hours"] = max_age_hours
    return result["meta"]


def run_vinted_deal_sale_monitor(
    *,
    db_path: str = str(DEFAULT_VINTED_DB_PATH),
    interval_seconds: int = 1800,
    max_items: int = 40,
    discord_webhook_url: str = "",
    browser_mode: str = "chrome_normale",
    browser_user_data_dir: str = "",
    browser_profile_directory: str = "Default",
    keep_browser_open: bool = True,
    close_existing_runtime_browser: bool = False,
    refresh_browser_profile: bool = False,
    page_settle_seconds: float = 2.0,
    max_tracking_age_hours: float = 72.0,
) -> ScrapeOutcome:
    interval = max(int(interval_seconds or 0), 0)
    now = datetime.now()
    due_before = now - timedelta(seconds=interval)
    max_age_hours = max(float(max_tracking_age_hours or 0), 0.0)
    first_seen_after = (now - timedelta(hours=max_age_hours)).isoformat(timespec="seconds") if max_age_hours > 0 else ""
    normalized_db_path = str(db_path or DEFAULT_VINTED_DB_PATH)
    due_before_text = due_before.isoformat(timespec="seconds")
    # Do this query before requesting a browser: most deal-hunter cycles have nothing due.
    due = load_due_vinted_deal_sale_checks(
        normalized_db_path,
        due_before=due_before_text,
        limit=max(int(max_items or 0), 1),
        first_seen_after=first_seen_after,
    )
    if not due:
        return ScrapeOutcome(
            source="vinted_deal_sales",
            rows=[],
            meta={
                "due_count": 0,
                "checked_count": 0,
                "sold_count": 0,
                "errors": 0,
                "discord_sent": 0,
                "max_tracking_age_hours": max_age_hours,
            },
        )
    payload = _check_vinted_deal_sales_task(
        {
            "db_path": normalized_db_path,
            "due_before": due_before_text,
            "first_seen_after": first_seen_after,
            "max_items": max(int(max_items or 0), 1),
            "discord_webhook_url": discord_webhook_url,
            "browser_mode": browser_mode,
            "browser_user_data_dir": browser_user_data_dir,
            "browser_profile_directory": browser_profile_directory,
            "keep_browser_open": bool(keep_browser_open),
            "close_existing_runtime_browser": bool(close_existing_runtime_browser),
            "refresh_browser_profile": bool(refresh_browser_profile),
            "page_settle_seconds": page_settle_seconds,
        },
        reuse_driver=bool(keep_browser_open),
    )
    payload["meta"]["max_tracking_age_hours"] = max_age_hours
    return ScrapeOutcome(source="vinted_deal_sales", rows=payload["rows"], meta=payload["meta"])
