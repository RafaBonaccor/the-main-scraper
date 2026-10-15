"""Explicit sold-marker checks for trend-scout cohorts."""

from __future__ import annotations

import time

from botasaurus.browser import Driver, Wait, browser

from ..browser_runtime import resolve_browser_arguments, resolve_browser_profile
from ..sources.vinted_deal_sales import _sold_status_from_page
from ..vinted_trend_scout import VintedTrendStore


@browser(
    profile=resolve_browser_profile,
    add_arguments=resolve_browser_arguments,
    wait_for_complete_page_load=False,
    close_on_crash=True,
    max_retry=2,
    retry_wait=1,
    raise_exception=True,
)
def _check_trend_items_task(driver: Driver, config: dict) -> dict:
    store = VintedTrendStore(config["db_path"])
    items = store.due_items(
        check_interval_seconds=int(config["check_interval_seconds"]),
        limit=int(config["max_items"]),
        max_age_hours=int(config["max_tracking_age_hours"]),
    )
    checked = sold = unavailable = errors = 0
    print(f"[vinted-trends] sale-check-start due={len(items)}", flush=True)
    for item in items:
        link = str(item.get("item_link", "") or "")
        try:
            driver.get(link, wait=Wait.SHORT, timeout=18)
            time.sleep(min(max(float(config.get("page_settle_seconds", 1.5)), 0.25), 2.0))
            status = _sold_status_from_page(driver)
            is_sold = bool(status.get("sold"))
            is_unavailable = bool(status.get("page_not_found"))
            store.save_item_check(link, sold=is_sold, unavailable=is_unavailable)
            checked += 1
            sold += int(is_sold)
            unavailable += int(is_unavailable)
            print(
                f"[vinted-trends] sale-check sold={is_sold} unavailable={is_unavailable} link={link}",
                flush=True,
            )
        except Exception as exc:
            errors += 1
            print(f"[vinted-trends] sale-check-error link={link} error={exc}", flush=True)
    return {"checked": checked, "sold": sold, "unavailable": unavailable, "errors": errors}


def run_vinted_trend_sale_checks(
    *, db_path: str, check_interval_seconds: int, max_items: int,
    max_tracking_age_hours: int, browser_mode: str, browser_user_data_dir: str,
    browser_profile_directory: str, refresh_browser_profile: bool,
    page_settle_seconds: float = 1.5,
) -> dict:
    store = VintedTrendStore(db_path)
    due = store.due_items(
        check_interval_seconds=check_interval_seconds,
        limit=max_items,
        max_age_hours=max_tracking_age_hours,
    )
    if not due:
        return {"checked": 0, "sold": 0, "unavailable": 0, "errors": 0}
    return _check_trend_items_task(
        {
            "db_path": db_path,
            "check_interval_seconds": check_interval_seconds,
            "max_items": max_items,
            "max_tracking_age_hours": max_tracking_age_hours,
            "browser_mode": browser_mode,
            "browser_user_data_dir": browser_user_data_dir,
            "browser_profile_directory": browser_profile_directory,
            "keep_browser_open": False,
            "close_existing_runtime_browser": True,
            "refresh_browser_profile": refresh_browser_profile,
            "page_settle_seconds": page_settle_seconds,
        },
        reuse_driver=False,
    )
