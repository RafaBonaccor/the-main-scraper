"""Public-page lifecycle checks for deals previously sent by the deal hunter."""

from __future__ import annotations

import time
import hashlib
import random
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.request import Request, urlopen

from botasaurus.browser import Driver, Wait, browser

from ..browser_runtime import resolve_browser_arguments, resolve_browser_profile
from ..discord_notifications import build_vinted_deal_sold_discord_message, send_discord_webhook_message
from ..models import ScrapeOutcome
from ..vinted_database import (
    DEFAULT_VINTED_DB_PATH,
    delete_vinted_item_by_link,
    format_vinted_elapsed_seconds,
    load_due_vinted_deal_sale_checks,
    load_vinted_sold_deals_missing_dom_image,
    parse_vinted_precise_listing_age_seconds,
    save_vinted_deal_sale_check,
    save_vinted_sold_dom_image_metadata,
)
from ..vinted_tabs import navigate_vinted_status_tab, restore_vinted_primary_tab

VINTED_DEAL_SALE_NAVIGATION_TIMEOUT_SECONDS = 18


def _archive_sold_dom_image(image_url: str, item_id: str, db_path: str | Path) -> str:
    """Keep the actual product image seen on the sold detail page, without replacing older captures."""
    image_url = str(image_url or "").strip()
    if not image_url:
        return ""
    request = Request(image_url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(request, timeout=20) as response:
        content = response.read(30 * 1024 * 1024 + 1)
    if not content or len(content) > 30 * 1024 * 1024:
        raise ValueError("immagine DOM vuota o oltre 30 MB")
    if content.startswith(b"\xff\xd8\xff"):
        suffix = ".jpg"
    elif content.startswith(b"\x89PNG\r\n\x1a\n"):
        suffix = ".png"
    elif content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        suffix = ".webp"
    else:
        raise ValueError("formato immagine DOM non riconosciuto")
    digest = hashlib.sha256(content).hexdigest()
    archive_root = Path(db_path).expanduser().resolve().parent / "deal_archive" / "sold_dom"
    image_dir = archive_root / datetime.now().strftime("%Y/%m/%d")
    image_dir.mkdir(parents=True, exist_ok=True)
    safe_item_id = "".join(character for character in str(item_id or "item") if character.isalnum()) or "item"
    target = image_dir / f"{safe_item_id}-{digest[:16]}{suffix}"
    if not target.exists():
        target.write_bytes(content)
    return str(target)


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
const imageCandidates = [];
const addCandidate = (node) => {
  if (!node) return;
  const urls = [];
  let largestSrcset = '';
  let largestSrcsetSize = -1;
  for (const key of ['srcset', 'data-srcset']) {
    const value = node.getAttribute?.(key) || '';
    for (const part of value.split(',')) {
      const match = part.trim().match(/^(\\S+)\\s+(\\d+(?:\\.\\d+)?)(w|x)$/);
      if (match && Number(match[2]) > largestSrcsetSize) {
        largestSrcset = match[1];
        largestSrcsetSize = Number(match[2]);
      }
    }
  }
  // Prefer the largest image explicitly offered by the page, then the
  // browser-selected and ordinary source URLs as fallbacks.
  if (largestSrcset) urls.push(largestSrcset);
  for (const key of ['currentSrc', 'src', 'data-src']) {
    const value = node[key] || node.getAttribute?.(key) || '';
    if (value) urls.push(value);
  }
  const uniqueUrls = [...new Set(urls.map((url) => { try { return new URL(url, window.location.href).href; } catch (_) { return ''; } }))]
    .filter((url) => url && /images\\d*\\.vinted\\.(?:net|com)/i.test(new URL(url).hostname));
  if (!uniqueUrls.length) return;
  const alt = clean(node.getAttribute?.('alt') || '');
  const scope = node.closest?.('[data-testid], [class*="photo"], [class*="Photo"]');
  const context = `${alt} ${scope?.getAttribute?.('data-testid') || ''} ${scope?.className || ''}`.toLowerCase();
  const isProductPhoto = /foto|photo|item-photo|item_photo|product-photo/.test(context);
  const naturalArea = Math.max(0, Number(node.naturalWidth || 0) * Number(node.naturalHeight || 0));
  imageCandidates.push({url: uniqueUrls[0], score: (isProductPhoto ? 1e12 : 0) + naturalArea, isProductPhoto, natural_width: Number(node.naturalWidth || 0), natural_height: Number(node.naturalHeight || 0)});
};
document.querySelectorAll('img').forEach(addCandidate);
document.querySelectorAll('picture source').forEach(addCandidate);
imageCandidates.sort((a, b) => b.score - a.score);
const productImage = imageCandidates.find((candidate) => candidate.isProductPhoto) || imageCandidates[0] || {};
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
  rate_limited: /too many requests|rate limit|troppi accessi|verifica che sei un umano|verify you are human|captcha/i.test(body),
  title: h1Text,
  published_text: publishedText,
  product_image_url: productImage.url || '',
  product_image_width: productImage.natural_width || 0,
  product_image_height: productImage.natural_height || 0,
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


@browser(
    profile=resolve_browser_profile,
    add_arguments=resolve_browser_arguments,
    wait_for_complete_page_load=False,
    close_on_crash=True,
    max_retry=1,
    retry_wait=2,
    raise_exception=True,
)
def _backfill_vinted_sold_dom_images_task(driver: Driver, config: dict) -> dict:
    captured = checked = errors = no_image = not_found = classified = 0
    rate_limited = False
    rows = list(config.get("items", []) or [])
    delay = max(float(config.get("delay_seconds", 3.0) or 3.0), 2.0)
    for index, item in enumerate(rows, start=1):
        link = str(item.get("link", "") or "").strip()
        if not link:
            continue
        try:
            driver.get(link, wait=Wait.SHORT, timeout=VINTED_DEAL_SALE_NAVIGATION_TIMEOUT_SECONDS)
            time.sleep(max(float(config.get("page_settle_seconds", 1.5) or 0), 0.5))
            status = _sold_status_from_page(driver)
            checked += 1
            if bool(status.get("rate_limited")):
                rate_limited = True
                print(f"[vinted-image-backfill] rate-limit detected; stopping safely at item={index}/{len(rows)}", flush=True)
                break
            if bool(status.get("page_not_found")):
                marked = save_vinted_sold_dom_image_metadata(
                    link,
                    {
                        "sold_dom_image_check_status": "page_not_found",
                        "sold_dom_image_checked_at": datetime.now().isoformat(timespec="seconds"),
                        "sold_dom_image_check_source": "historical_sold_page_dom",
                        "sold_dom_image_last_url": str(status.get("current_url", "") or ""),
                    },
                    db_path=config["db_path"],
                )
                if marked:
                    not_found += 1
                    classified += 1
                    print(f"[vinted-image-backfill] page-not-found item={index}/{len(rows)} id={item.get('item_id', '')}", flush=True)
                else:
                    errors += 1
                    print(f"[vinted-image-backfill] page-not-found-state-save-failed id={item.get('item_id', '')}", flush=True)
            else:
                image_url = str(status.get("product_image_url", "") or "").strip()
                if not image_url:
                    marked = save_vinted_sold_dom_image_metadata(
                        link,
                        {
                            "sold_dom_image_check_status": "no_product_image",
                            "sold_dom_image_checked_at": datetime.now().isoformat(timespec="seconds"),
                            "sold_dom_image_check_source": "historical_sold_page_dom",
                            "sold_dom_image_last_url": str(status.get("current_url", "") or ""),
                        },
                        db_path=config["db_path"],
                    )
                    if marked:
                        no_image += 1
                        classified += 1
                        print(f"[vinted-image-backfill] no-product-image item={index}/{len(rows)} id={item.get('item_id', '')}", flush=True)
                    else:
                        errors += 1
                        print(f"[vinted-image-backfill] no-product-image-state-save-failed id={item.get('item_id', '')}", flush=True)
                else:
                    image_path = _archive_sold_dom_image(image_url, str(item.get("item_id", "") or ""), config["db_path"])
                    metadata = {
                        "sold_dom_image_url": image_url,
                        "sold_dom_image_path": image_path,
                        "sold_dom_image_width": int(status.get("product_image_width", 0) or 0),
                        "sold_dom_image_height": int(status.get("product_image_height", 0) or 0),
                        "sold_dom_image_captured_at": datetime.now().isoformat(timespec="seconds"),
                        "sold_dom_image_capture_source": "historical_sold_page_dom",
                        "image_url": image_url,
                        "archive_image_path": image_path,
                    }
                    if save_vinted_sold_dom_image_metadata(link, metadata, db_path=config["db_path"]):
                        captured += 1
                        classified += 1
                        print(
                            f"[vinted-image-backfill] saved item={index}/{len(rows)} id={item.get('item_id', '')} "
                            f"size={metadata['sold_dom_image_width']}x{metadata['sold_dom_image_height']} path={image_path}",
                            flush=True,
                        )
                    else:
                        errors += 1
                        print(f"[vinted-image-backfill] database-record-missing item={index}/{len(rows)} id={item.get('item_id', '')}", flush=True)
        except Exception as error:
            errors += 1
            try:
                if save_vinted_sold_dom_image_metadata(
                    link,
                    {
                        "sold_dom_image_check_status": "error",
                        "sold_dom_image_checked_at": datetime.now().isoformat(timespec="seconds"),
                        "sold_dom_image_check_source": "historical_sold_page_dom",
                        "sold_dom_image_last_error": f"{type(error).__name__}: {error}"[:1000],
                    },
                    db_path=config["db_path"],
                ):
                    classified += 1
            except Exception as state_error:
                print(
                    f"[vinted-image-backfill] error-state-save-failed id={item.get('item_id', '')} "
                    f"error={type(state_error).__name__}: {state_error}",
                    flush=True,
                )
            print(f"[vinted-image-backfill] item-error item={index}/{len(rows)} id={item.get('item_id', '')} error={error}", flush=True)
        if index < len(rows):
            time.sleep(delay + random.uniform(0.0, 1.0))
    return {
        "rows": [],
        "meta": {
            "requested_count": len(rows),
            "checked_count": checked,
            "captured_count": captured,
            "no_image_count": no_image,
            "not_found_count": not_found,
            "error_count": errors,
            "classified_count": classified,
            "remaining_count": max(len(rows) - captured, 0),
            "rate_limited": rate_limited,
        },
    }


def run_vinted_sold_image_backfill(
    *,
    db_path: str = str(DEFAULT_VINTED_DB_PATH),
    limit: int = 0,
    batch_size: int = 40,
    delay_seconds: float = 3.0,
    cooldown_seconds: float = 180.0,
    rate_limit_cooldown_seconds: float = 900.0,
    max_rate_limit_retries: int = 2,
    browser_mode: str = "chrome_normale",
    browser_user_data_dir: str = "",
    browser_profile_directory: str = "Default",
    refresh_browser_profile: bool = False,
    page_settle_seconds: float = 1.5,
) -> ScrapeOutcome:
    """Safely and resumably capture detail-page images for already confirmed sales."""
    normalized_db_path = str(Path(db_path).expanduser().resolve())
    batch_limit = max(int(batch_size or 0), 1)
    total_limit = max(int(limit or 0), 0)
    captured = checked = errors = no_image = not_found = classified = batches = rate_limit_events = 0
    rate_limit_streak = 0
    stop_reason = "all_candidates_classified"
    while total_limit <= 0 or checked < total_limit:
        remaining_budget = total_limit - checked if total_limit > 0 else batch_limit
        items = load_vinted_sold_deals_missing_dom_image(
            normalized_db_path,
            limit=min(batch_limit, remaining_budget),
        )
        if not items:
            break
        batches += 1
        print(
            f"[vinted-image-backfill] batch-start batch={batches} size={len(items)} "
            f"captured_so_far={captured} pending_limit={total_limit or 'all'}",
            flush=True,
        )
        payload = _backfill_vinted_sold_dom_images_task(
            {
                "db_path": normalized_db_path,
                "items": items,
                "delay_seconds": max(float(delay_seconds or 0), 2.0),
                "browser_mode": browser_mode,
                "browser_user_data_dir": browser_user_data_dir,
                "browser_profile_directory": browser_profile_directory,
                "refresh_browser_profile": bool(refresh_browser_profile),
                "page_settle_seconds": page_settle_seconds,
            },
            # Each batch gets a fresh Chrome process. The historical backfill can
            # run for hours; reusing one driver across batches leaves a stale
            # DevTools connection if Chrome exits during a long run.
            reuse_driver=False,
        )
        batch_meta = dict(payload.get("meta", {}) or {})
        batch_captured = int(batch_meta.get("captured_count", 0) or 0)
        batch_classified = int(batch_meta.get("classified_count", batch_captured) or 0)
        batch_checked = int(batch_meta.get("checked_count", 0) or 0)
        captured += batch_captured
        classified += batch_classified
        checked += batch_checked
        errors += int(batch_meta.get("error_count", 0) or 0)
        no_image += int(batch_meta.get("no_image_count", 0) or 0)
        not_found += int(batch_meta.get("not_found_count", 0) or 0)
        if bool(batch_meta.get("rate_limited")):
            rate_limit_events += 1
            rate_limit_streak += 1
            if rate_limit_streak > max(int(max_rate_limit_retries or 0), 0):
                stop_reason = "repeated_rate_limit_protection"
                print("[vinted-image-backfill] repeated rate limit; stopping to protect the session", flush=True)
                break
            cooldown = max(float(rate_limit_cooldown_seconds or 0), 60.0) * rate_limit_streak
            print(
                f"[vinted-image-backfill] rate-limit-cooldown seconds={cooldown:.0f} "
                f"retry={rate_limit_streak}/{max_rate_limit_retries}",
                flush=True,
            )
            time.sleep(cooldown)
            continue
        rate_limit_streak = 0
        if batch_classified == 0:
            stop_reason = "batch_made_no_progress"
            print("[vinted-image-backfill] batch made no progress; stopping for review", flush=True)
            break
        if total_limit > 0 and checked >= total_limit:
            stop_reason = "configured_limit_reached"
            break
        # The next query skips every file committed in this batch, so interruptions are resumable.
        time.sleep(max(float(cooldown_seconds or 0), 0.0))
    pending_count = len(load_vinted_sold_deals_missing_dom_image(normalized_db_path))
    return ScrapeOutcome(
        source="vinted_sold_image_backfill",
        rows=[],
        meta={
            "requested_limit": total_limit,
            "batch_size": batch_limit,
            "batches_completed": batches,
            "checked_count": checked,
            "captured_count": captured,
            "classified_count": classified,
            "no_image_count": no_image,
            "not_found_count": not_found,
            "error_count": errors,
            "rate_limit_events": rate_limit_events,
            "pending_count": pending_count,
            "stop_reason": stop_reason,
        },
    )


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
                dom_image_url = str(status.get("product_image_url", "") or "").strip()
                row["sold_dom_image_url"] = dom_image_url
                row["sold_dom_image_width"] = int(status.get("product_image_width", 0) or 0)
                row["sold_dom_image_height"] = int(status.get("product_image_height", 0) or 0)
                if dom_image_url:
                    try:
                        row["sold_dom_image_path"] = _archive_sold_dom_image(
                            dom_image_url,
                            str(row.get("item_id", "") or ""),
                            config["db_path"],
                        )
                        # The embedding indexer follows archive_image_path; retain the old
                        # capture on disk and point this sold observation at the DOM image.
                        row["archive_image_path"] = row["sold_dom_image_path"]
                        row["image_url"] = dom_image_url
                        print(
                            f"[vinted-deal-sales] sold-dom-image-saved item_id={row.get('item_id', '')} "
                            f"width={row['sold_dom_image_width']} height={row['sold_dom_image_height']} "
                            f"path={row['sold_dom_image_path']}",
                            flush=True,
                        )
                    except Exception as image_error:
                        print(
                            f"[vinted-deal-sales] sold-dom-image-error item_id={row.get('item_id', '')} "
                            f"error={image_error}",
                            flush=True,
                        )
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
