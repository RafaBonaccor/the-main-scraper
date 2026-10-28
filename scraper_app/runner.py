import json
import time
from datetime import datetime
from pathlib import Path

from .date_filter import apply_age_filter_to_outcome
from .exporters import write_outcome_json
from .location_filter import apply_geo_sorting_to_outcome
from .models import ScrapeOutcome
from .openai_screening import DEFAULT_REASONING_EFFORT, DEFAULT_SCREENING_MODEL, apply_openai_screening_to_outcome
from .runtime_controls import consume_skip_current_item_request, consume_stop_after_current_item_request
from .sources.custom_site import run_custom_site_scraper
from .sources.aliexpress_image import run_aliexpress_image_search
from .sources.google_maps import run_google_maps_scraper
from .sources.subito import run_subito_scraper
from .subito_monitor import (
    DEFAULT_CAREGIVER_LOCATIONS,
    DEFAULT_CAREGIVER_SEARCH_TERMS,
    build_caregiver_queries,
    classify_caregiver_row,
    notify_new_caregiver_rows,
    parse_monitor_values,
    persist_subito_monitor_cycle,
    row_matches_location_target,
)
from .sources.vinted import run_vinted_description_extractor, run_vinted_scraper
from .sources.vinted_deal_sales import run_vinted_deal_sale_monitor
from .sources.vinted_profile import maybe_send_vinted_profile_daily_report, run_vinted_profile_monitor
from .sources.vinted_trend_sales import run_vinted_trend_sale_checks
from .utils import build_subito_search_url, parse_text_list
from .vinted_gateway import build_vinted_deal_hunter_search_specs
from .vinted_trend_scout import TrendPolicy, VintedTrendStore, parse_trend_queries, row_matches_trend_query


def run_scraper(source: str, **kwargs) -> ScrapeOutcome:
    if source == "aliexpress_image":
        return run_aliexpress_image_search(
            image_path=kwargs.get("image_path", ""),
            image_dir=kwargs.get("image_dir", ""),
            items_file=kwargs.get("items_file", ""),
            max_results=int(kwargs.get("max_results", 100) or 0),
            max_price=kwargs.get("max_price", ""),
            db_path=kwargs.get("db_path", "data/aliexpress_sourcing.db"),
            reuse_cache_hours=float(kwargs.get("reuse_cache_hours", 24) or 0),
            force_refresh=bool(kwargs.get("force_refresh", False)),
            watch_interval_seconds=int(kwargs.get("watch_interval_seconds", 0) or 0),
            browser_mode=kwargs.get("browser_mode", "chrome_normale"),
            browser_user_data_dir=kwargs.get("browser_user_data_dir", ""),
            browser_profile_directory=kwargs.get("browser_profile_directory", "Default"),
            refresh_browser_profile=bool(kwargs.get("refresh_browser_profile", False)),
            slow_mode=bool(kwargs.get("slow_mode", False)),
            action_delay_seconds=float(kwargs.get("action_delay_seconds", 1.0)),
            page_settle_seconds=float(kwargs.get("page_settle_seconds", 3.0)),
        )

    if source == "google_maps":
        return run_google_maps_scraper(
            search=kwargs["search"],
            city=kwargs.get("city", ""),
            province=kwargs.get("province", ""),
            country=kwargs.get("country", ""),
            max_results=int(kwargs.get("max_results", 25)),
            exclude_sponsored=bool(kwargs.get("exclude_sponsored", True)),
            include_details=bool(kwargs.get("include_details", True)),
            audit_websites=bool(kwargs.get("audit_websites", True)),
            website_timeout_seconds=float(kwargs.get("website_timeout_seconds", 10.0)),
            slow_mode=bool(kwargs.get("slow_mode", False)),
            action_delay_seconds=float(kwargs.get("action_delay_seconds", 1.5)),
            page_settle_seconds=float(kwargs.get("page_settle_seconds", 3.0)),
            browser_mode=kwargs.get("browser_mode", "chrome_normale"),
            browser_user_data_dir=kwargs.get("browser_user_data_dir", ""),
            browser_profile_directory=kwargs.get("browser_profile_directory", "Default"),
            refresh_browser_profile=bool(kwargs.get("refresh_browser_profile", False)),
        )

    if source == "vinted":
        return _run_vinted_queries(**kwargs)

    if source == "vinted_competitors":
        return _run_vinted_competitor_discovery(**kwargs)

    if source == "vinted_continuous":
        return _run_vinted_continuous_monitoring(**kwargs)

    if source == "vinted_trends":
        return _run_vinted_trend_scout(**kwargs)

    if source == "vinted_descriptions":
        return run_vinted_description_extractor(
            items=_resolve_vinted_items(kwargs),
            db_path=kwargs.get("db_path", "data/scraper.db"),
            ui_result_json=kwargs.get("ui_result_json", ""),
            browser_mode=kwargs.get("browser_mode", "chrome_normale"),
            browser_user_data_dir=kwargs.get("browser_user_data_dir", ""),
            browser_profile_directory=kwargs.get("browser_profile_directory", "Default"),
            keep_browser_open=bool(kwargs.get("keep_browser_open", True)),
            close_existing_runtime_browser=bool(kwargs.get("close_existing_runtime_browser", False)),
            refresh_browser_profile=bool(kwargs.get("refresh_browser_profile", False)),
            keep_open_seconds=int(kwargs.get("keep_open_seconds", 0)),
            slow_mode=bool(kwargs.get("slow_mode", False)),
            action_delay_seconds=float(kwargs.get("action_delay_seconds", 1.5)),
            page_settle_seconds=float(kwargs.get("page_settle_seconds", 3.0)),
        )

    if source == "vinted_profile":
        return run_vinted_profile_monitor(
            profile_url=kwargs.get("profile_url", kwargs.get("url", "")),
            profile_urls=_resolve_vinted_profile_urls(kwargs),
            max_items=int(kwargs.get("max_items", kwargs.get("max_results", 0)) or 0),
            interval_seconds=int(kwargs.get("interval_seconds", 0) or 0),
            db_path=kwargs.get("db_path", "data/scraper.db"),
            ui_result_json=kwargs.get("ui_result_json", ""),
            browser_mode=kwargs.get("browser_mode", "chrome_normale"),
            browser_user_data_dir=kwargs.get("browser_user_data_dir", ""),
            browser_profile_directory=kwargs.get("browser_profile_directory", "Default"),
            keep_browser_open=bool(kwargs.get("keep_browser_open", True)),
            refresh_browser_profile=bool(kwargs.get("refresh_browser_profile", False)),
            keep_open_seconds=int(kwargs.get("keep_open_seconds", 0)),
            slow_mode=bool(kwargs.get("slow_mode", False)),
            action_delay_seconds=float(kwargs.get("action_delay_seconds", 1.5)),
            page_settle_seconds=float(kwargs.get("page_settle_seconds", 3.0)),
            discord_profile_report=bool(kwargs.get("discord_profile_report", False)),
            discord_webhook_url=str(kwargs.get("discord_webhook_url", "") or ""),
            daily_report_hour=int(kwargs.get("daily_report_hour", 10) or 10),
            check_listing_statuses=bool(kwargs.get("check_listing_statuses", True)),
            profile_categories=str(kwargs.get("profile_categories", "") or ""),
        )

    if source == "vinted_deal_sales":
        return run_vinted_deal_sale_monitor(
            db_path=kwargs.get("db_path", "data/scraper.db"),
            interval_seconds=int(kwargs.get("deal_sale_check_interval_seconds", 1800) or 0),
            max_items=int(kwargs.get("deal_sale_check_max_items", 40) or 40),
            max_tracking_age_hours=float(kwargs.get("deal_sale_max_tracking_age_hours", 72) or 0),
            discord_webhook_url=str(kwargs.get("discord_webhook_url", "") or ""),
            browser_mode=kwargs.get("browser_mode", "chrome_normale"),
            browser_user_data_dir=kwargs.get("browser_user_data_dir", ""),
            browser_profile_directory=kwargs.get("browser_profile_directory", "Default"),
            keep_browser_open=bool(kwargs.get("keep_browser_open", True)),
            refresh_browser_profile=bool(kwargs.get("refresh_browser_profile", False)),
            page_settle_seconds=float(kwargs.get("page_settle_seconds", 2.0) or 2.0),
        )

    if source == "subito":
        return _run_subito_search(**kwargs)

    if source == "subito_caregiver":
        return _run_subito_caregiver_monitor(**kwargs)

    if source == "custom_site":
        return run_custom_site_scraper(
            url=kwargs["url"],
            item_selector=kwargs["item_selector"],
            name_selector=kwargs.get("name_selector", ""),
            phone_selector=kwargs.get("phone_selector", ""),
            link_selector=kwargs.get("link_selector", ""),
            cookie_reject_texts=kwargs.get("cookie_reject_texts", ""),
            browser_mode=kwargs.get("browser_mode", "chrome_normale"),
            browser_user_data_dir=kwargs.get("browser_user_data_dir", ""),
            browser_profile_directory=kwargs.get("browser_profile_directory", "Default"),
        )

    raise ValueError(f"Unsupported source: {source}")


def _resolve_vinted_items(kwargs: dict) -> list[dict | str]:
    links_file = str(kwargs.get("links_file", "") or "").strip()
    items = kwargs.get("items", kwargs.get("links", []))
    if links_file:
        return _read_items_file(Path(links_file))
    if isinstance(items, list):
        return [item for item in items if isinstance(item, dict) or str(item).strip()]
    if items:
        return [items]
    return []


def _resolve_vinted_profile_urls(kwargs: dict) -> list[str]:
    profiles_file = str(kwargs.get("profiles_file", "") or "").strip()
    if profiles_file:
        path = Path(profiles_file).expanduser()
        if not path.exists():
            raise ValueError(f"File profili Vinted non trovato: {path}")
        content = path.read_text(encoding="utf-8").strip()
        if not content:
            return []
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, list):
            return [str(item or "").strip() for item in parsed if str(item or "").strip()]
        return [line.strip() for line in content.replace(";", "\n").splitlines() if line.strip()]
    raw_profile_urls = kwargs.get("profile_urls")
    if isinstance(raw_profile_urls, list):
        return [str(item or "").strip() for item in raw_profile_urls if str(item or "").strip()]
    if raw_profile_urls:
        return [str(raw_profile_urls)]
    single_profile = str(kwargs.get("profile_url", kwargs.get("url", "")) or "").strip()
    return [single_profile] if single_profile else []


def _run_vinted_queries(**kwargs) -> ScrapeOutcome:
    search_specs = _resolve_vinted_search_specs(kwargs)
    if not search_specs:
        raise ValueError("Inserisci una ricerca Vinted o un searches-file valido.")

    deal_hunter_loop_seconds = max(int(kwargs.get("deal_hunter_loop_seconds", 0) or 0), 0)
    deal_hunter_enabled = int(kwargs.get("deal_hunter_min_favorites", 0) or 0) > 0

    if deal_hunter_enabled and deal_hunter_loop_seconds > 0:
        latest_outcome: ScrapeOutcome | None = None
        cycle_index = 0
        while True:
            if consume_stop_after_current_item_request():
                break
            cycle_index += 1
            print(f"[deal-hunter] ciclo interno {cycle_index} avviato.", flush=True)
            latest_outcome = _run_vinted_queries_once(search_specs, **kwargs)
            _monitor_vinted_deal_sales_after_hunt(latest_outcome, **kwargs)
            if consume_stop_after_current_item_request():
                break
            print(
                f"[deal-hunter] ciclo interno {cycle_index} completato, pausa {deal_hunter_loop_seconds}s prima del prossimo giro.",
                flush=True,
            )
            deadline = time.monotonic() + deal_hunter_loop_seconds
            while time.monotonic() < deadline:
                if consume_stop_after_current_item_request():
                    break
                time.sleep(min(1.0, max(deadline - time.monotonic(), 0.0)))
            if consume_stop_after_current_item_request():
                break
        if latest_outcome is not None:
            return latest_outcome
        raise RuntimeError("Procacciatore Vinted interrotto prima di completare il primo ciclo.")

    outcome = _run_vinted_queries_once(search_specs, **kwargs)
    _monitor_vinted_deal_sales_after_hunt(outcome, **kwargs)
    return outcome


def _monitor_vinted_deal_sales_after_hunt(outcome: ScrapeOutcome, **kwargs) -> None:
    """Check previously notified deals opportunistically, without breaking the deal hunt."""
    if not bool(kwargs.get("discord_deal_notifications", False)):
        return
    sale_outcome = _run_vinted_deal_sale_monitor_safely(**kwargs)
    if sale_outcome is not None:
        outcome.meta["deal_sale_monitor"] = dict(sale_outcome.meta)


def _run_vinted_deal_sale_monitor_safely(**kwargs) -> ScrapeOutcome | None:
    try:
        return run_vinted_deal_sale_monitor(
            db_path=str(kwargs.get("db_path", "data/scraper.db") or "data/scraper.db"),
            interval_seconds=int(kwargs.get("deal_sale_check_interval_seconds", 1800) or 0),
            max_items=int(kwargs.get("deal_sale_check_max_items", 40) or 40),
            max_tracking_age_hours=float(kwargs.get("deal_sale_max_tracking_age_hours", 72) or 0),
            discord_webhook_url=str(kwargs.get("discord_webhook_url", "") or ""),
            browser_mode=str(kwargs.get("browser_mode", "chrome_normale") or "chrome_normale"),
            browser_user_data_dir=str(kwargs.get("browser_user_data_dir", "") or ""),
            browser_profile_directory=str(kwargs.get("browser_profile_directory", "Default") or "Default"),
            keep_browser_open=bool(kwargs.get("keep_browser_open", True)),
            close_existing_runtime_browser=bool(kwargs.get("close_existing_runtime_browser", False)),
            refresh_browser_profile=bool(kwargs.get("refresh_browser_profile", False)),
            page_settle_seconds=float(kwargs.get("page_settle_seconds", 2.0) or 2.0),
        )
    except Exception as exc:
        # Monitoring a historical deal must never fail the current discovery run.
        print(f"[vinted-deal-sales] monitor-skipped error={exc}", flush=True)
        return None


def _run_vinted_competitor_discovery(**kwargs) -> ScrapeOutcome:
    search_specs = _resolve_vinted_competitor_search_specs(kwargs)
    if not search_specs:
        raise ValueError("Inserisci keyword competitor o una categoria valida.")

    interval_seconds = max(int(kwargs.get("competitor_interval_seconds", 0) or 0), 0)
    run_kwargs = dict(kwargs)
    run_kwargs.update(
        {
            "deal_hunter_min_favorites": int(kwargs.get("competitor_min_likes", 15) or 0),
            "deal_hunter_max_age_hours": float(kwargs.get("competitor_max_age_hours", 24 * 30) or 0),
            "deal_hunter_min_price": kwargs.get("competitor_min_price", ""),
            "discord_deal_notifications": bool(kwargs.get("discord_deal_notifications", False)),
            "deal_hunter_url_only_discord": False,
            "skip_login_required": True,
            "auto_submit_offers": False,
            "exclude_known_items": bool(kwargs.get("exclude_known_items", True)),
            "keep_browser_open": bool(kwargs.get("keep_browser_open", True)),
            "run_kind": "competitor_discovery",
            "separate_status_tab": bool(kwargs.get("separate_status_tab", False)),
            "monitor_existing_deal_sales_in_search_task": bool(
                kwargs.get("monitor_existing_deal_sales_in_search_task", False)
            ),
        }
    )

    cycle_index = 0
    latest_outcome: ScrapeOutcome | None = None
    while True:
        if consume_stop_after_current_item_request():
            break
        cycle_index += 1
        print(f"[competitor-discovery] ciclo {cycle_index} avviato.", flush=True)
        try:
            latest_outcome = _run_vinted_queries_once(search_specs, **run_kwargs)
        except Exception as exc:
            # A dead browser/session is transient. In loop mode it must not
            # terminate the autonomous monitor: wait, then obtain a new one.
            print(f"[competitor-discovery] ciclo {cycle_index} errore transitorio: {exc}", flush=True)
            if interval_seconds <= 0:
                raise
            deadline = time.monotonic() + interval_seconds
            while time.monotonic() < deadline:
                if consume_stop_after_current_item_request():
                    break
                time.sleep(min(1.0, max(deadline - time.monotonic(), 0.0)))
            if consume_stop_after_current_item_request():
                break
            continue
        latest_outcome.source = "vinted_competitors"
        latest_outcome.meta.update(
            {
                "competitor_discovery_enabled": True,
                "competitor_cycle": cycle_index,
                "competitor_keywords": str(kwargs.get("competitor_keywords", "") or ""),
                "competitor_category": str(kwargs.get("competitor_category", "") or ""),
                "competitor_min_likes": int(run_kwargs["deal_hunter_min_favorites"]),
                "competitor_max_age_hours": float(run_kwargs["deal_hunter_max_age_hours"]),
                "competitor_interval_seconds": interval_seconds,
                "discord_deal_notifications": bool(run_kwargs["discord_deal_notifications"]),
                "competitor_sellers_found": len(
                    {
                        str(row.get("seller_member_id", "") or "").strip()
                        for row in latest_outcome.rows
                        if str(row.get("seller_member_id", "") or "").strip()
                    }
                ),
            }
        )
        if interval_seconds <= 0 or consume_stop_after_current_item_request():
            break
        print(
            f"[competitor-discovery] ciclo {cycle_index} completato, prossimo controllo tra {interval_seconds}s.",
            flush=True,
        )
        deadline = time.monotonic() + interval_seconds
        while time.monotonic() < deadline:
            if consume_stop_after_current_item_request():
                break
            time.sleep(min(1.0, max(deadline - time.monotonic(), 0.0)))
        if consume_stop_after_current_item_request():
            break

    if latest_outcome is not None:
        return latest_outcome
    raise RuntimeError("Competitor discovery interrotto prima di completare il primo ciclo.")


def _run_vinted_trend_scout(**kwargs) -> ScrapeOutcome:
    """Observe independent brand cohorts and pause searches proven to rotate poorly."""
    queries = parse_trend_queries(kwargs.get("trend_keywords", ""))
    db_path = str(kwargs.get("db_path", "data/scraper.db") or "data/scraper.db")
    scan_interval = max(int(kwargs.get("trend_interval_seconds", 900) or 0), 0)
    minimum_sell_through = min(max(float(kwargs.get("trend_min_sell_through", 8) or 0), 0.0), 100.0)
    promising_sell_through = min(
        max(float(kwargs.get("trend_promising_sell_through", 15) or 0), minimum_sell_through),
        100.0,
    )
    policy = TrendPolicy(
        evaluation_window_hours=max(int(kwargs.get("trend_evaluation_hours", 72) or 72), 1),
        evaluation_spacing_hours=max(int(kwargs.get("trend_evaluation_spacing_hours", 24) or 24), 1),
        minimum_sample_size=max(int(kwargs.get("trend_min_sample", 30) or 30), 1),
        minimum_sell_through_percent=minimum_sell_through,
        promising_sell_through_percent=promising_sell_through,
        weak_evaluations_before_pause=max(int(kwargs.get("trend_weak_evaluations", 2) or 2), 1),
        empty_scans_before_pause=max(int(kwargs.get("trend_empty_scans", 3) or 3), 1),
        error_scans_before_cooldown=max(int(kwargs.get("trend_error_scans", 3) or 3), 1),
        pause_hours=max(int(kwargs.get("trend_pause_hours", 72) or 72), 1),
    )
    store = VintedTrendStore(db_path)
    store.sync_queries(queries)
    cycle = 0
    latest_rows: list[dict] = []
    latest_meta: dict = {}
    while True:
        if consume_stop_after_current_item_request():
            break
        cycle += 1
        due_queries = store.due_queries(max(int(kwargs.get("trend_queries_per_cycle", 3) or 3), 1))
        cycle_rows: list[dict] = []
        errors: list[dict] = []
        print(f"[vinted-trends] cycle-start cycle={cycle} due_queries={len(due_queries)}", flush=True)
        for query_row in due_queries:
            query = str(query_row["query_text"])
            if consume_stop_after_current_item_request():
                break
            try:
                outcome = _run_vinted_queries_once(
                    [{"search": query, "max_results": max(int(kwargs.get("max_results", 100) or 100), 1),
                      "max_price": _parse_optional_nonnegative_float(kwargs.get("max_price"))}],
                    **{
                        **kwargs,
                        "deal_hunter_min_favorites": 0,
                        "exclude_known_items": False,
                        "skip_login_required": True,
                        "auto_submit_offers": False,
                        "discord_deal_notifications": False,
                        "keep_browser_open": False,
                        "close_existing_runtime_browser": True,
                        "run_kind": "trend_scout",
                        "ui_result_json": "",
                    },
                )
                relevant = [row for row in outcome.rows if row_matches_trend_query(row, query)]
                saved = store.record_scan(query, relevant, next_scan_seconds=max(scan_interval, 1))
                for row in relevant:
                    cycle_rows.append({
                        **row,
                        "trend_query": query,
                        "trend_segment": query,
                        "trend_query_kind": query_row.get("query_kind", "category"),
                    })
                print(
                    f"[vinted-trends] query-complete query={query!r} raw={len(outcome.rows)} relevant={len(relevant)} saved={saved}",
                    flush=True,
                )
            except Exception as exc:
                store.record_scan_error(query, exc, policy=policy)
                errors.append({"query": query, "error": f"{type(exc).__name__}: {exc}"})
                print(f"[vinted-trends] query-error query={query!r} error={exc}", flush=True)
        sale_meta = run_vinted_trend_sale_checks(
            db_path=db_path,
            check_interval_seconds=max(int(kwargs.get("trend_sale_check_interval_seconds", 3600) or 0), 0),
            max_items=max(int(kwargs.get("trend_sale_checks_per_cycle", 20) or 20), 1),
            max_tracking_age_hours=max(int(kwargs.get("trend_max_tracking_hours", 168) or 168), policy.evaluation_window_hours),
            browser_mode=str(kwargs.get("browser_mode", "chrome_normale") or "chrome_normale"),
            browser_user_data_dir=str(kwargs.get("browser_user_data_dir", "") or ""),
            browser_profile_directory=str(kwargs.get("browser_profile_directory", "Default") or "Default"),
            refresh_browser_profile=bool(kwargs.get("refresh_browser_profile", False)),
            page_settle_seconds=float(kwargs.get("page_settle_seconds", 1.5) or 1.5),
        )
        evaluations = store.evaluate_all(policy)
        latest_rows = cycle_rows
        latest_meta = {
            "trend_scout_enabled": True,
            "trend_cycle": cycle,
            "queries_configured": len(queries),
            "queries_scanned": len(due_queries),
            "rows_observed": len(cycle_rows),
            "sale_checks": sale_meta,
            "query_evaluations": evaluations,
            "query_summary": store.summary(),
            "errors": errors,
            "policy": policy.__dict__,
            "db_path": db_path,
        }
        if scan_interval <= 0 and due_queries and len(errors) == len(due_queries):
            preview = "; ".join(f"{item['query']}: {item['error']}" for item in errors[:3])
            raise RuntimeError(f"Tutte le query Trend Scout sono fallite. {preview}")
        if scan_interval <= 0 or consume_stop_after_current_item_request():
            break
        deadline = time.monotonic() + scan_interval
        while time.monotonic() < deadline and not consume_stop_after_current_item_request():
            time.sleep(min(1.0, max(deadline - time.monotonic(), 0.0)))
    return ScrapeOutcome(source="vinted_trends", rows=latest_rows, meta=latest_meta)


def _run_vinted_continuous_monitoring(**kwargs) -> ScrapeOutcome:
    profile_urls = _resolve_vinted_profile_urls(kwargs)
    competitor_specs = _resolve_vinted_competitor_search_specs(kwargs)
    if not profile_urls and not competitor_specs:
        raise ValueError("Inserisci almeno un profilo o una keyword competitor per il monitoraggio continuo.")

    article_interval_seconds = max(
        int(kwargs.get("article_interval_seconds", kwargs.get("continuous_interval_seconds", 5)) or 0),
        0,
    )
    profile_interval_seconds = max(int(kwargs.get("profile_interval_seconds", 3600) or 0), 0)
    db_path = str(kwargs.get("db_path", "data/scraper.db") or "data/scraper.db")
    cycle_index = 0
    profile_cycle_index = 0
    competitor_cycle_index = 0
    next_profile_run_at = 0.0
    next_competitor_run_at = 0.0
    profile_single_run_complete = False
    competitor_single_run_complete = False
    last_daily_report_date = ""
    latest_outcome: ScrapeOutcome | None = None
    # A continuous job must never leave a separate Chromium instance alive for
    # every component/cycle.  It deliberately uses a disposable scraper
    # browser: Botasaurus closes it when that component returns, before the
    # next profile, competitor, or deal-sale check can open one.
    automation_browser_kwargs = {
        **kwargs,
        "keep_browser_open": False,
        "keep_open_seconds": 0,
        "close_existing_runtime_browser": True,
    }

    while True:
        if consume_stop_after_current_item_request():
            break
        now = time.monotonic()
        run_profiles = bool(profile_urls) and not profile_single_run_complete and now >= next_profile_run_at
        run_competitors = bool(competitor_specs) and not competitor_single_run_complete and now >= next_competitor_run_at
        if not run_profiles and not run_competitors:
            due_times = [due_at for due_at in (next_profile_run_at, next_competitor_run_at) if due_at > now]
            wait_seconds = min(due_times) - now if due_times else 1.0
            time.sleep(min(1.0, max(wait_seconds, 0.05)))
            continue
        cycle_index += 1
        print(
            f"[continuous-monitoring] cycle-start cycle={cycle_index} profiles_due={run_profiles} competitors_due={run_competitors}",
            flush=True,
        )
        rows: list[dict] = []
        profile_meta: dict = {}
        competitor_meta: dict = {}
        errors: list[dict[str, str]] = []

        if run_profiles:
            profile_cycle_index += 1
            try:
                profile_outcome = run_vinted_profile_monitor(
                    profile_url="",
                    profile_urls=profile_urls,
                    max_items=int(kwargs.get("max_items", 0) or 0),
                    interval_seconds=0,
                    db_path=db_path,
                    ui_result_json="",
                    browser_mode=str(kwargs.get("browser_mode", "chrome_normale") or "chrome_normale"),
                    browser_user_data_dir=str(kwargs.get("browser_user_data_dir", "") or ""),
                    browser_profile_directory=str(kwargs.get("browser_profile_directory", "Default") or "Default"),
                    keep_browser_open=False,
                    close_existing_runtime_browser=True,
                    refresh_browser_profile=bool(kwargs.get("refresh_browser_profile", False)),
                    keep_open_seconds=0,
                    slow_mode=bool(kwargs.get("slow_mode", False)),
                    action_delay_seconds=float(kwargs.get("action_delay_seconds", 1.5) or 1.5),
                    page_settle_seconds=float(kwargs.get("page_settle_seconds", 3.0) or 3.0),
                    discord_profile_report=bool(kwargs.get("discord_profile_report", False)),
                    discord_webhook_url=str(kwargs.get("discord_webhook_url", "") or ""),
                    daily_report_hour=int(kwargs.get("daily_report_hour", 10) or 10),
                    send_daily_report=False,
                    check_listing_statuses=True,
                    profile_categories=str(kwargs.get("profile_categories", "") or ""),
                )
                profile_meta = dict(profile_outcome.meta)
                for row in profile_outcome.rows:
                    rows.append({**row, "continuous_component": "profiles"})
            except Exception as exc:
                errors.append({"component": "profiles", "error": str(exc)})
                print(f"[continuous-monitoring] profiles-error cycle={cycle_index} error={exc}", flush=True)
            if profile_interval_seconds <= 0:
                profile_single_run_complete = True
            else:
                next_profile_run_at = time.monotonic() + profile_interval_seconds

        if run_competitors and not consume_stop_after_current_item_request():
            competitor_cycle_index += 1
            try:
                competitor_outcome = _run_vinted_competitor_discovery(
                    **{
                        **automation_browser_kwargs,
                        "competitor_interval_seconds": 0,
                        "ui_result_json": "",
                        "db_path": db_path,
                        "separate_status_tab": True,
                        "monitor_existing_deal_sales_in_search_task": True,
                    }
                )
                competitor_meta = dict(competitor_outcome.meta)
                for row in competitor_outcome.rows:
                    rows.append({**row, "continuous_component": "competitors"})
            except Exception as exc:
                errors.append({"component": "competitors", "error": str(exc)})
                print(f"[continuous-monitoring] competitors-error cycle={cycle_index} error={exc}", flush=True)
            if article_interval_seconds <= 0:
                competitor_single_run_complete = True
            else:
                next_competitor_run_at = time.monotonic() + article_interval_seconds

        last_daily_report_date = maybe_send_vinted_profile_daily_report(
            db_path=db_path,
            profile_urls=profile_urls,
            discord_profile_report=bool(kwargs.get("discord_profile_report", False)),
            discord_webhook_url=str(kwargs.get("discord_webhook_url", "") or ""),
            daily_report_hour=int(kwargs.get("daily_report_hour", 10) or 10),
            last_daily_report_date=last_daily_report_date,
        )
        deal_sale_meta: dict = {}
        sale_status_meta = competitor_meta.get("deal_sale_status_check", {})
        sale_checks_ran_in_search_browser = bool(
            run_competitors
            and competitor_meta
            and isinstance(sale_status_meta, dict)
            and not sale_status_meta.get("error")
        )
        if bool(kwargs.get("discord_deal_notifications", False)) and not sale_checks_ran_in_search_browser:
            deal_sale_outcome = _run_vinted_deal_sale_monitor_safely(**automation_browser_kwargs)
            if deal_sale_outcome is not None:
                deal_sale_meta = dict(deal_sale_outcome.meta)
        latest_outcome = ScrapeOutcome(
            source="vinted_continuous",
            rows=rows,
            meta={
                "continuous_monitoring_enabled": True,
                "continuous_cycle": cycle_index,
                "article_interval_seconds": article_interval_seconds,
                "profile_interval_seconds": profile_interval_seconds,
                "profile_cycle": profile_cycle_index,
                "competitor_cycle": competitor_cycle_index,
                "profile_count": len(profile_urls),
                "competitor_search_count": len(competitor_specs),
                "continuous_deal_notifications": bool(kwargs.get("discord_deal_notifications", False)),
                "profile_meta": profile_meta,
                "competitor_meta": competitor_meta,
                "deal_sale_monitor": deal_sale_meta,
                "component_errors": errors,
                "row_count": len(rows),
                "db_path": db_path,
            },
        )
        print(
            f"[continuous-monitoring] cycle-finished cycle={cycle_index} rows={len(rows)} errors={len(errors)}",
            flush=True,
        )
        if errors and len(errors) == int(run_profiles) + int(run_competitors):
            # A browser/profile failure is recoverable. Keep this autonomous
            # monitor alive and let the next scheduled cycle obtain a fresh
            # isolated Chrome snapshot rather than failing the whole job.
            print(
                f"[continuous-monitoring] all-due-components-failed cycle={cycle_index}; retry scheduled, job remains active.",
                flush=True,
            )
        if (
            (not profile_urls or profile_single_run_complete)
            and (not competitor_specs or competitor_single_run_complete)
        ) or consume_stop_after_current_item_request():
            break

    if latest_outcome is not None:
        return latest_outcome
    raise RuntimeError("Monitoraggio continuo interrotto prima di completare il primo ciclo.")


def _run_vinted_queries_once(search_specs: list[dict], **kwargs) -> ScrapeOutcome:
    if not search_specs:
        raise ValueError("Inserisci una ricerca Vinted o un searches-file valido.")
    deal_hunter_enabled = int(kwargs.get("deal_hunter_min_favorites", 0) or 0) > 0

    if len(search_specs) == 1:
        spec = search_specs[0]
        return run_vinted_scraper(
            search=spec["search"],
            max_results=int(spec.get("max_results", 100)),
            min_price=_parse_optional_nonnegative_float(kwargs.get("deal_hunter_min_price")),
            max_price=spec.get("max_price"),
            deal_hunter_min_favorites=int(kwargs.get("deal_hunter_min_favorites", 0) or 0),
            deal_hunter_max_age_hours=float(kwargs.get("deal_hunter_max_age_hours", 24.0) or 0),
            exclude_known_items=bool(kwargs.get("exclude_known_items", True)),
            discord_deal_notifications=bool(kwargs.get("discord_deal_notifications", False)),
            discord_webhook_url=str(kwargs.get("discord_webhook_url", "") or ""),
            deal_hunter_url_only_discord=bool(kwargs.get("deal_hunter_url_only_discord", False)),
            skip_login_required=bool(kwargs.get("skip_login_required", False)),
            auto_submit_offers=bool(kwargs.get("auto_submit_offers", True)),
            db_path=kwargs.get("db_path", "data/scraper.db"),
            ui_result_json=kwargs.get("ui_result_json", ""),
            browser_mode=kwargs.get("browser_mode", "chrome_normale"),
            browser_user_data_dir=kwargs.get("browser_user_data_dir", ""),
            browser_profile_directory=kwargs.get("browser_profile_directory", "Default"),
            keep_browser_open=bool(kwargs.get("keep_browser_open", True)),
            close_existing_runtime_browser=bool(kwargs.get("close_existing_runtime_browser", False)),
            refresh_browser_profile=bool(kwargs.get("refresh_browser_profile", False)),
            keep_open_seconds=int(kwargs.get("keep_open_seconds", 0)),
            slow_mode=bool(kwargs.get("slow_mode", False)),
            action_delay_seconds=float(kwargs.get("action_delay_seconds", 1.5)),
            page_settle_seconds=float(kwargs.get("page_settle_seconds", 3.0)),
            run_kind=str(kwargs.get("run_kind", "") or "search"),
            separate_status_tab=bool(kwargs.get("separate_status_tab", False)),
            monitor_existing_deal_sales_in_search_task=bool(
                kwargs.get("monitor_existing_deal_sales_in_search_task", False)
            ),
            deal_sale_check_interval_seconds=int(kwargs.get("deal_sale_check_interval_seconds", 1800) or 0),
            deal_sale_check_max_items=int(kwargs.get("deal_sale_check_max_items", 40) or 40),
            deal_sale_max_tracking_age_hours=float(kwargs.get("deal_sale_max_tracking_age_hours", 72) or 0),
        )

    rows: list[dict] = []
    search_urls: list[str] = []
    pages_visited: list[int] = []
    search_errors: list[dict[str, str]] = []
    new_items = 0
    updated_items = 0
    new_search_hits = 0
    updated_search_hits = 0
    filtered_out_known_items = 0
    filtered_out_by_price = 0
    priority_rows_enriched = 0
    priority_rows_demoted_by_age = 0
    priority_rows_cached = 0
    deal_hunter_candidates = 0
    deal_hunter_matches = 0
    last_meta: dict = {}
    stopped_early = False

    for index, spec in enumerate(search_specs):
        if consume_stop_after_current_item_request():
            stopped_early = True
            break
        is_last = index == len(search_specs) - 1
        inner_keep_browser_open = bool(kwargs.get("keep_browser_open", True)) if is_last or not deal_hunter_enabled else True
        try:
            outcome = run_vinted_scraper(
                search=spec["search"],
                max_results=int(spec.get("max_results", 100)),
                min_price=_parse_optional_nonnegative_float(kwargs.get("deal_hunter_min_price")),
                max_price=spec.get("max_price"),
                deal_hunter_min_favorites=int(kwargs.get("deal_hunter_min_favorites", 0) or 0),
                deal_hunter_max_age_hours=float(kwargs.get("deal_hunter_max_age_hours", 24.0) or 0),
                exclude_known_items=bool(kwargs.get("exclude_known_items", True)),
                discord_deal_notifications=bool(kwargs.get("discord_deal_notifications", False)),
                discord_webhook_url=str(kwargs.get("discord_webhook_url", "") or ""),
                deal_hunter_url_only_discord=bool(kwargs.get("deal_hunter_url_only_discord", False)),
                skip_login_required=bool(kwargs.get("skip_login_required", False)),
                auto_submit_offers=bool(kwargs.get("auto_submit_offers", True)),
                db_path=kwargs.get("db_path", "data/scraper.db"),
                ui_result_json=str(kwargs.get("ui_result_json", "") or "") if deal_hunter_enabled else "",
                browser_mode=kwargs.get("browser_mode", "chrome_normale"),
                browser_user_data_dir=kwargs.get("browser_user_data_dir", ""),
                browser_profile_directory=kwargs.get("browser_profile_directory", "Default"),
                keep_browser_open=inner_keep_browser_open,
                close_existing_runtime_browser=bool(kwargs.get("close_existing_runtime_browser", False)),
                refresh_browser_profile=bool(kwargs.get("refresh_browser_profile", False)),
                keep_open_seconds=int(kwargs.get("keep_open_seconds", 0)) if is_last else 0,
                slow_mode=bool(kwargs.get("slow_mode", False)),
                action_delay_seconds=float(kwargs.get("action_delay_seconds", 1.5)),
                page_settle_seconds=float(kwargs.get("page_settle_seconds", 3.0)),
                detach_browser_on_complete=not deal_hunter_enabled and is_last,
                run_kind=str(kwargs.get("run_kind", "") or "search"),
                separate_status_tab=bool(kwargs.get("separate_status_tab", False)),
                monitor_existing_deal_sales_in_search_task=bool(
                    kwargs.get("monitor_existing_deal_sales_in_search_task", False)
                ),
                deal_sale_check_interval_seconds=int(kwargs.get("deal_sale_check_interval_seconds", 1800) or 0),
                deal_sale_check_max_items=int(kwargs.get("deal_sale_check_max_items", 40) or 40),
                deal_sale_max_tracking_age_hours=float(kwargs.get("deal_sale_max_tracking_age_hours", 72) or 0),
            )
        except Exception as exc:
            search_errors.append(
                {
                    "search": str(spec.get("search", "") or ""),
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            continue
        last_meta = dict(outcome.meta)
        search_url = str(outcome.meta.get("search_url", "") or "")
        if search_url:
            search_urls.append(search_url)
        pages_visited.extend(list(outcome.meta.get("pages_visited", []) or []))
        new_items += int(outcome.meta.get("new_items", 0) or 0)
        updated_items += int(outcome.meta.get("updated_items", 0) or 0)
        new_search_hits += int(outcome.meta.get("new_search_hits", 0) or 0)
        updated_search_hits += int(outcome.meta.get("updated_search_hits", 0) or 0)
        filtered_out_known_items += int(outcome.meta.get("filtered_out_known_items", 0) or 0)
        filtered_out_by_price += int(outcome.meta.get("filtered_out_by_price", 0) or 0)
        priority_rows_enriched += int(outcome.meta.get("priority_rows_enriched", 0) or 0)
        priority_rows_demoted_by_age += int(outcome.meta.get("priority_rows_demoted_by_age", 0) or 0)
        priority_rows_cached += int(outcome.meta.get("priority_rows_cached", 0) or 0)
        deal_hunter_candidates += int(outcome.meta.get("deal_hunter_candidates", 0) or 0)
        deal_hunter_matches += int(outcome.meta.get("deal_hunter_matches", 0) or 0)
        for row in outcome.rows:
            annotated_row = dict(row)
            annotated_row["batch_search_index"] = index + 1
            annotated_row["batch_search_count"] = len(search_specs)
            annotated_row["extracted_order"] = len(rows) + 1
            rows.append(annotated_row)
        partial_outcome = _build_vinted_batch_outcome(
            rows=rows,
            search_specs=search_specs,
            search_urls=search_urls,
            pages_visited=pages_visited,
            search_errors=search_errors,
            new_items=new_items,
            updated_items=updated_items,
            new_search_hits=new_search_hits,
            updated_search_hits=updated_search_hits,
            filtered_out_known_items=filtered_out_known_items,
            filtered_out_by_price=filtered_out_by_price,
            priority_rows_enriched=priority_rows_enriched,
            priority_rows_demoted_by_age=priority_rows_demoted_by_age,
            priority_rows_cached=priority_rows_cached,
            deal_hunter_candidates=deal_hunter_candidates,
            deal_hunter_matches=deal_hunter_matches,
            last_meta=last_meta,
            kwargs=kwargs,
            stopped_early=False,
        )
        _persist_partial_vinted_batch_outcome(
            outcome=partial_outcome,
            ui_result_json=str(kwargs.get("ui_result_json", "") or ""),
        )

    if not rows and search_errors:
        error_preview = "; ".join(f"{item['search']}: {item['error']}" for item in search_errors[:3])
        raise RuntimeError(f"Nessuna ricerca Vinted completata. {error_preview}")

    return _build_vinted_batch_outcome(
        rows=rows,
        search_specs=search_specs,
        search_urls=search_urls,
        pages_visited=pages_visited,
        search_errors=search_errors,
        new_items=new_items,
        updated_items=updated_items,
        new_search_hits=new_search_hits,
        updated_search_hits=updated_search_hits,
        filtered_out_known_items=filtered_out_known_items,
        filtered_out_by_price=filtered_out_by_price,
        priority_rows_enriched=priority_rows_enriched,
        priority_rows_demoted_by_age=priority_rows_demoted_by_age,
        priority_rows_cached=priority_rows_cached,
        deal_hunter_candidates=deal_hunter_candidates,
        deal_hunter_matches=deal_hunter_matches,
        last_meta=last_meta,
        kwargs=kwargs,
        stopped_early=stopped_early,
    )


def _build_vinted_batch_outcome(
    *,
    rows: list[dict],
    search_specs: list[dict],
    search_urls: list[str],
    pages_visited: list[int],
    search_errors: list[dict[str, str]],
    new_items: int,
    updated_items: int,
    new_search_hits: int,
    updated_search_hits: int,
    filtered_out_known_items: int,
    filtered_out_by_price: int,
    priority_rows_enriched: int,
    priority_rows_demoted_by_age: int,
    priority_rows_cached: int,
    deal_hunter_candidates: int,
    deal_hunter_matches: int,
    last_meta: dict,
    kwargs: dict,
    stopped_early: bool,
) -> ScrapeOutcome:
    search_labels = [str(spec.get("search", "") or "") for spec in search_specs]
    return ScrapeOutcome(
        source="vinted",
        rows=rows,
        meta={
            "search_term": f"{len(search_specs)} ricerche batch",
            "search_terms": search_labels,
            "search_count": len(search_specs),
            "search_specs": search_specs,
            "multi_search": True,
            "db_path": kwargs.get("db_path", "data/scraper.db"),
            "search_url": search_urls[0] if search_urls else "",
            "search_urls": search_urls,
            "pages_visited": pages_visited,
            "pages_visited_count": len(pages_visited),
            "row_count": len(rows),
            "new_items": new_items,
            "updated_items": updated_items,
            "new_search_hits": new_search_hits,
            "updated_search_hits": updated_search_hits,
            "exclude_known_items": bool(kwargs.get("exclude_known_items", True)),
            "filtered_out_known_items": filtered_out_known_items,
            "filtered_out_by_price": filtered_out_by_price,
            "priority_rows_enriched": priority_rows_enriched,
            "priority_rows_demoted_by_age": priority_rows_demoted_by_age,
            "priority_rows_cached": priority_rows_cached,
            "deal_hunter_enabled": bool(last_meta.get("deal_hunter_enabled", False)),
            "deal_hunter_min_favorites": int(kwargs.get("deal_hunter_min_favorites", 0) or 0),
            "deal_hunter_max_age_hours": float(kwargs.get("deal_hunter_max_age_hours", 24.0) or 0),
            "deal_hunter_min_price": _parse_optional_nonnegative_float(kwargs.get("deal_hunter_min_price")),
            "deal_hunter_candidates": deal_hunter_candidates,
            "deal_hunter_matches": deal_hunter_matches,
            "discord_deal_notifications": bool(kwargs.get("discord_deal_notifications", False)),
            "discord_webhook_url_configured": bool(str(kwargs.get("discord_webhook_url", "") or "").strip()),
            "deal_hunter_url_only_discord": bool(kwargs.get("deal_hunter_url_only_discord", False)),
            "search_errors": search_errors,
            "keep_browser_open": bool(kwargs.get("keep_browser_open", True)),
            "keep_open_seconds": int(kwargs.get("keep_open_seconds", 0)),
            "slow_mode": bool(kwargs.get("slow_mode", False)),
            "action_delay_seconds": float(kwargs.get("action_delay_seconds", 1.5)),
            "page_settle_seconds": float(kwargs.get("page_settle_seconds", 3.0)),
            "vinted_access_marker_present": bool(last_meta.get("vinted_access_marker_present", False)),
            "vinted_access_expected_alt": str(last_meta.get("vinted_access_expected_alt", "") or ""),
            "vinted_access_current_url": str(last_meta.get("vinted_access_current_url", "") or ""),
            "vinted_access_checked_at": str(last_meta.get("vinted_access_checked_at", "") or ""),
            "stopped_early": bool(stopped_early),
            "db_saved_live": False,
        },
    )


def _persist_partial_vinted_batch_outcome(outcome: ScrapeOutcome, ui_result_json: str) -> None:
    target = str(ui_result_json or "").strip()
    if not target:
        return
    path = Path(target).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    write_outcome_json(path, outcome)


def _resolve_vinted_search_specs(kwargs: dict) -> list[dict]:
    searches_file = str(kwargs.get("searches_file", "") or "").strip()
    raw_searches = kwargs.get("searches", [])
    if searches_file:
        return _read_vinted_search_specs_file(
            Path(searches_file),
            default_max_results=int(kwargs.get("max_results", 100)),
            default_max_price=_parse_optional_nonnegative_float(kwargs.get("max_price")),
        )
    if isinstance(raw_searches, list) and raw_searches:
        return _normalize_vinted_search_specs(
            raw_searches,
            default_max_results=int(kwargs.get("max_results", 100)),
            default_max_price=_parse_optional_nonnegative_float(kwargs.get("max_price")),
        )
    search = str(kwargs.get("search", "") or "").strip()
    if not search:
        deal_hunter_terms = kwargs.get("deal_hunter_terms", "")
        deal_hunter_category = kwargs.get("deal_hunter_category", "")
        if str(deal_hunter_terms or "").strip() or str(deal_hunter_category or "").strip():
            return build_vinted_deal_hunter_search_specs(
                raw_terms=deal_hunter_terms,
                category_selection=deal_hunter_category,
                max_results=max(int(kwargs.get("max_results", 100)), 0),
                max_price=_parse_optional_nonnegative_float(kwargs.get("max_price")),
            )
        return []
    return [
        {
            "search": search,
            "max_results": max(int(kwargs.get("max_results", 100)), 0),
            "max_price": _parse_optional_nonnegative_float(kwargs.get("max_price")),
        }
    ]


def _resolve_vinted_competitor_search_specs(kwargs: dict) -> list[dict]:
    searches_file = str(kwargs.get("searches_file", "") or "").strip()
    raw_search = str(kwargs.get("search", "") or "").strip()
    if searches_file or raw_search:
        return _resolve_vinted_search_specs(kwargs)
    return build_vinted_deal_hunter_search_specs(
        raw_terms=kwargs.get("competitor_keywords", kwargs.get("deal_hunter_terms", "charm, pandora")),
        category_selection=kwargs.get("competitor_category", kwargs.get("deal_hunter_category", "Gioielli donna")),
        max_results=max(int(kwargs.get("max_results", 100) or 0), 0),
        max_price=_parse_optional_nonnegative_float(kwargs.get("max_price")),
    )


def _read_vinted_search_specs_file(path: Path, default_max_results: int, default_max_price: float | None) -> list[dict]:
    if not path.exists():
        raise ValueError(f"Searches file non trovato: {path}")
    content = path.read_text(encoding="utf-8").strip()
    if not content:
        raise ValueError(f"Searches file vuoto: {path}")
    raw_items = json.loads(content)
    if not isinstance(raw_items, list):
        raise ValueError(f"Searches file non valido: {path}")
    return _normalize_vinted_search_specs(raw_items, default_max_results=default_max_results, default_max_price=default_max_price)


def _normalize_vinted_search_specs(raw_items: list[object], default_max_results: int, default_max_price: float | None) -> list[dict]:
    specs: list[dict] = []
    for raw_item in raw_items:
        if isinstance(raw_item, dict):
            search = str(raw_item.get("search", raw_item.get("query", "")) or "").strip()
            max_results = raw_item.get("max_results", default_max_results)
            max_price = raw_item.get("max_price", default_max_price)
        else:
            search = str(raw_item or "").strip()
            max_results = default_max_results
            max_price = default_max_price
        if not search:
            continue
        try:
            normalized_max_results = max(int(max_results), 0)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Max risultati Vinted non valido per la ricerca '{search}'.") from exc
        normalized_max_price = _parse_optional_nonnegative_float(max_price)
        specs.append(
            {
                "search": search,
                "max_results": normalized_max_results,
                "max_price": normalized_max_price,
            }
        )
    return specs


def _run_subito_queries(**kwargs) -> ScrapeOutcome:
    queries = _build_subito_queries(kwargs.get("query", ""), kwargs.get("job_keywords", ""))
    max_results = max(int(kwargs.get("max_results", 25)), 1)
    max_age_hours = _parse_optional_nonnegative_int(kwargs.get("max_age_hours")) or 0
    exact_age_days = _parse_optional_nonnegative_int(kwargs.get("exact_age_days"))
    requested_cities = _build_subito_cities(kwargs.get("city", "roma"))
    raw_query = str(kwargs.get("query", "") or "").strip()
    is_direct_url = raw_query.lower().startswith(("http://", "https://"))
    target_cities = [""] if is_direct_url else requested_cities
    include_details = bool(
        kwargs.get("include_details", False)
        or kwargs.get("llm_screening", False)
        or exact_age_days is not None
        or max_age_hours > 0
    )
    query_errors: list[dict[str, str]] = []

    rows_by_key: dict[str, dict] = {}
    search_urls: list[str] = []
    cookie_actions: list[str] = []
    visited_cities: list[str] = []

    for city in target_cities:
        if city and city not in visited_cities:
            visited_cities.append(city)
        for query in queries:
            if consume_stop_after_current_item_request():
                break
            if consume_skip_current_item_request():
                query_errors.append(
                    {
                        "query": query,
                        "city": city or "",
                        "error": "Query saltata su richiesta dell utente.",
                    }
                )
                continue
            try:
                outcome = run_subito_scraper(
                    query=query,
                    region=kwargs.get("region", "lazio"),
                    city=city,
                    category=kwargs.get("category", "offerte-lavoro"),
                    include_details=include_details,
                    max_results=max_results,
                    slow_mode=bool(kwargs.get("slow_mode", False)),
                    action_delay_seconds=float(kwargs.get("action_delay_seconds", 1.5)),
                    page_settle_seconds=float(kwargs.get("page_settle_seconds", 3.0)),
                    browser_mode=kwargs.get("browser_mode", "chrome_normale"),
                    browser_user_data_dir=kwargs.get("browser_user_data_dir", ""),
                    browser_profile_directory=kwargs.get("browser_profile_directory", "Default"),
                )
            except Exception as exc:
                query_errors.append(
                    {
                        "query": query,
                        "city": city or "",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
                continue
            search_url = str(outcome.meta.get("search_url", "") or "")
            cookie_action = str(outcome.meta.get("cookie_banner_action", "") or "")
            if search_url:
                search_urls.append(search_url)
            if cookie_action:
                cookie_actions.append(cookie_action)

            for row in outcome.rows:
                key = _row_identity(row)
                if key not in rows_by_key:
                    annotated_row = dict(row)
                    annotated_row.setdefault("extracted_at", datetime.now().isoformat(timespec="seconds"))
                    annotated_row["extracted_order"] = len(rows_by_key) + 1
                    rows_by_key[key] = annotated_row
        if consume_stop_after_current_item_request():
            break

    rows = list(rows_by_key.values())
    return ScrapeOutcome(
        source="subito",
        rows=rows,
        meta={
            "cookie_banner_action": cookie_actions[-1] if cookie_actions else "",
            "query": kwargs.get("query", ""),
            "query_terms": queries,
            "selected_job_keywords": _parse_job_keywords(kwargs.get("job_keywords", "")),
            "region": kwargs.get("region", "lazio"),
            "city": kwargs.get("city", "roma"),
            "cities": requested_cities,
            "search_cities": visited_cities,
            "category": kwargs.get("category", "offerte-lavoro"),
            "search_url": search_urls[0] if search_urls else "",
            "search_urls": search_urls,
            "include_details": include_details,
            "slow_mode": bool(kwargs.get("slow_mode", False)),
            "action_delay_seconds": float(kwargs.get("action_delay_seconds", 1.5)),
            "page_settle_seconds": float(kwargs.get("page_settle_seconds", 3.0)),
            "max_age_hours": max_age_hours,
            "max_age_days": int(kwargs.get("max_age_days", 14)),
            "exact_age_days": exact_age_days,
            "max_results": max_results,
            "row_count": len(rows),
            "multi_query": len(queries) > 1,
            "multi_city": len(requested_cities) > 1,
            "query_errors": query_errors,
        },
    )


def _run_subito_search(**kwargs) -> ScrapeOutcome:
    outcome = _run_subito_queries(**kwargs)
    max_age_hours = _parse_optional_nonnegative_int(kwargs.get("max_age_hours")) or 0
    exact_age_days = _parse_optional_nonnegative_int(kwargs.get("exact_age_days"))
    if bool(kwargs.get("apply_geo_sorting", True)):
        filtered = apply_geo_sorting_to_outcome(
            outcome,
            anchor_place=kwargs.get("anchor_place", "Morlupo"),
            max_distance_km=float(kwargs.get("max_distance_km", 30)),
            nearby_only=bool(kwargs.get("nearby_only", False)),
        )
    else:
        filtered = outcome
    filtered = apply_age_filter_to_outcome(
        filtered,
        max_age_hours=max_age_hours,
        max_age_days=int(kwargs.get("max_age_days", 14)),
        exact_age_days=exact_age_days,
        keep_unknown_dates=not (max_age_hours > 0 or exact_age_days is not None),
    )
    if bool(kwargs.get("llm_screening", False)):
        filtered = apply_openai_screening_to_outcome(
            filtered,
            anchor_place=kwargs.get("anchor_place", "Morlupo"),
            max_distance_km=float(kwargs.get("max_distance_km", 30)),
            target_job_keywords=_build_screening_target_roles(kwargs.get("query", ""), kwargs.get("job_keywords", "")),
            model=str(kwargs.get("openai_model", DEFAULT_SCREENING_MODEL) or DEFAULT_SCREENING_MODEL),
            reasoning_effort=str(kwargs.get("openai_reasoning_effort", DEFAULT_REASONING_EFFORT) or DEFAULT_REASONING_EFFORT),
        )
    return _limit_outcome_rows(filtered, int(kwargs.get("max_results", 25)))


def _run_subito_caregiver_monitor(**kwargs) -> ScrapeOutcome:
    locations = parse_monitor_values(kwargs.get("locations", ""), DEFAULT_CAREGIVER_LOCATIONS)
    search_terms = parse_monitor_values(kwargs.get("search_terms", ""), DEFAULT_CAREGIVER_SEARCH_TERMS)
    query_specs = build_caregiver_queries(search_terms, locations)
    interval_seconds = max(int(kwargs.get("interval_seconds", 1800) or 0), 0)
    max_results = max(int(kwargs.get("max_results", 25) or 25), 1)
    max_age_days = max(int(kwargs.get("max_age_days", 14) or 0), 0)
    only_part_time = bool(kwargs.get("only_part_time", True))
    db_path = str(kwargs.get("db_path", "data/subito_jobs.db") or "data/subito_jobs.db")
    ui_result_json = str(kwargs.get("ui_result_json", "") or "").strip()
    discord_enabled = bool(kwargs.get("discord_notifications", False))
    discord_webhook_url = str(kwargs.get("discord_webhook_url", "") or "").strip()
    cycle_index = 0

    print(
        "[subito-caregiver] monitor-start "
        f"locations={len(locations)} search_terms={len(search_terms)} "
        f"interval_seconds={interval_seconds} db_path={db_path}",
        flush=True,
    )
    while True:
        cycle_index += 1
        cycle_started_at = datetime.now().isoformat(timespec="seconds")
        print(f"[subito-caregiver] cycle-start cycle={cycle_index} searches={len(query_specs)}", flush=True)
        rows_by_key: dict[str, dict] = {}
        errors: list[dict[str, str]] = []
        rejected_counts: dict[str, int] = {}

        for search_index, spec in enumerate(query_specs, start=1):
            query = spec["query"]
            target = spec["location_target"]
            search_url = build_subito_search_url(
                query_value=query,
                region=str(kwargs.get("region", "lazio") or "lazio"),
                category=str(kwargs.get("category", "offerte-lavoro") or "offerte-lavoro"),
                city="",
            )
            print(
                f"[subito-caregiver] search-start cycle={cycle_index} "
                f"search={search_index}/{len(query_specs)} target={target!r} query={query!r}",
                flush=True,
            )
            try:
                outcome = _run_subito_search(
                    query=search_url,
                    job_keywords="",
                    region=kwargs.get("region", "lazio"),
                    city="",
                    category=kwargs.get("category", "offerte-lavoro"),
                    include_details=bool(kwargs.get("include_details", False)),
                    llm_screening=False,
                    max_results=max_results,
                    max_age_hours=0,
                    max_age_days=max_age_days,
                    exact_age_days=-1,
                    anchor_place="",
                    max_distance_km=0,
                    nearby_only=False,
                    apply_geo_sorting=False,
                    slow_mode=bool(kwargs.get("slow_mode", False)),
                    action_delay_seconds=float(kwargs.get("action_delay_seconds", 0.5)),
                    page_settle_seconds=float(kwargs.get("page_settle_seconds", 1.5)),
                    browser_mode=kwargs.get("browser_mode", "isolated"),
                    browser_user_data_dir=kwargs.get("browser_user_data_dir", ""),
                    browser_profile_directory=kwargs.get("browser_profile_directory", "SubitoMonitor"),
                )
            except Exception as exc:
                error = {"query": query, "target": target, "error": f"{type(exc).__name__}: {exc}"}
                errors.append(error)
                print(f"[subito-caregiver] search-error {error}", flush=True)
                continue

            accepted_for_search = 0
            for source_row in outcome.rows:
                if not row_matches_location_target(source_row, target):
                    rejected_counts["outside-target"] = rejected_counts.get("outside-target", 0) + 1
                    continue
                accepted, reason = classify_caregiver_row(source_row, only_part_time=only_part_time)
                if not accepted:
                    rejected_counts[reason] = rejected_counts.get(reason, 0) + 1
                    continue
                row = dict(source_row)
                row.update(
                    {
                        "source": "subito_caregiver",
                        "monitor_location_target": target,
                        "monitor_search_term": spec["search_term"],
                        "monitor_match_reason": reason,
                    }
                )
                identity = _row_identity(row)
                if identity not in rows_by_key:
                    rows_by_key[identity] = row
                    accepted_for_search += 1
            print(
                f"[subito-caregiver] search-complete cycle={cycle_index} target={target!r} "
                f"scraped={len(outcome.rows)} accepted={accepted_for_search}",
                flush=True,
            )

        rows = list(rows_by_key.values())
        config = {
            "locations": locations,
            "search_terms": search_terms,
            "only_part_time": only_part_time,
            "max_results_per_search": max_results,
            "max_age_days": max_age_days,
            "interval_seconds": interval_seconds,
        }
        new_rows, persistence_meta = persist_subito_monitor_cycle(
            db_path, rows, cycle_index=cycle_index, config=config, errors=errors
        )
        notification_meta = {"attempted": 0, "sent": 0, "errors": []}
        if discord_enabled:
            notification_meta = notify_new_caregiver_rows(discord_webhook_url, new_rows)
        meta = {
            **persistence_meta,
            "cycle_index": cycle_index,
            "cycle_started_at": cycle_started_at,
            "interval_seconds": interval_seconds,
            "locations": locations,
            "search_terms": search_terms,
            "search_count": len(query_specs),
            "row_count": len(rows),
            "new_count": len(new_rows),
            "query_errors": errors,
            "rejected_counts": rejected_counts,
            "discord_notifications": discord_enabled,
            "discord_notification_result": notification_meta,
        }
        last_outcome = ScrapeOutcome(source="subito_caregiver", rows=rows, meta=meta)
        if ui_result_json:
            write_outcome_json(Path(ui_result_json), last_outcome)
        for row in new_rows:
            print(
                "[subito-caregiver] new-listing "
                f"target={row.get('monitor_location_target', '')!r} "
                f"title={row.get('title', '')!r} link={row.get('link', '')}",
                flush=True,
            )
        print(
            f"[subito-caregiver] cycle-complete cycle={cycle_index} "
            f"matched={len(rows)} new={len(new_rows)} errors={len(errors)} "
            f"discord_sent={notification_meta.get('sent', 0)}",
            flush=True,
        )
        if interval_seconds <= 0:
            return last_outcome
        print(f"[subito-caregiver] sleeping seconds={interval_seconds}", flush=True)
        deadline = time.monotonic() + interval_seconds
        while time.monotonic() < deadline:
            time.sleep(min(1.0, max(deadline - time.monotonic(), 0.0)))


def _build_subito_queries(raw_query: str, raw_job_keywords: str) -> list[str]:
    query = str(raw_query or "").strip()
    if query.lower().startswith(("http://", "https://")):
        return [query]

    queries: list[str] = []
    seen: set[str] = set()

    if query:
        lowered = query.lower()
        seen.add(lowered)
        queries.append(query)

    for keyword in _parse_job_keywords(raw_job_keywords):
        lowered = keyword.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        queries.append(keyword)

    return queries or [query]


def _build_screening_target_roles(raw_query: str, raw_job_keywords: str) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()

    query = str(raw_query or "").strip()
    if query and not query.lower().startswith(("http://", "https://")):
        seen.add(query.lower())
        values.append(query)

    for keyword in _parse_job_keywords(raw_job_keywords):
        lowered = keyword.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        values.append(keyword)

    return values


def _parse_job_keywords(raw_job_keywords: str) -> list[str]:
    seen: set[str] = set()
    keywords: list[str] = []
    for part in str(raw_job_keywords or "").split(","):
        value = part.strip()
        lowered = value.lower()
        if not value or lowered in seen:
            continue
        seen.add(lowered)
        keywords.append(value)
    return keywords


def _build_subito_cities(raw_city: str) -> list[str]:
    values = parse_text_list(str(raw_city or ""))
    if not values:
        values = ["roma"]

    cities: list[str] = []
    seen: set[str] = set()
    for value in values:
        lowered = value.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        cities.append(value)
    return cities


def _row_identity(row: dict) -> str:
    return str(row.get("link") or f"{row.get('title', '')}|{row.get('location', '')}|{row.get('company', '')}")


def _parse_optional_nonnegative_int(value: object) -> int | None:
    if value in (None, "", False):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    if parsed < 0:
        return None
    return parsed


def _parse_optional_nonnegative_float(value: object) -> float | None:
    if value in (None, "", False):
        return None
    text = str(value).strip().replace("€", "").replace(" ", "")
    if not text:
        return None
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    try:
        parsed = float(text)
    except (TypeError, ValueError):
        return None
    if parsed < 0:
        return None
    return parsed


def _limit_outcome_rows(outcome: ScrapeOutcome, max_results: int) -> ScrapeOutcome:
    max_rows = max(int(max_results), 1)
    if len(outcome.rows) <= max_rows:
        outcome.meta["row_count"] = len(outcome.rows)
        return outcome

    trimmed_rows = outcome.rows[:max_rows]
    trimmed_counts = {"accepted": 0, "maybe": 0, "rejected": 0}
    trimmed_screening_counts = {"candida": 0, "valuta": 0, "no": 0}
    trimmed_age_counts = {"fresh": 0, "stale": 0, "unknown": 0}
    for row in trimmed_rows:
        decision = str(row.get("geo_decision", "maybe"))
        trimmed_counts[decision] = trimmed_counts.get(decision, 0) + 1
        screening_decision = str(row.get("screening_decision", "") or "").strip().lower()
        if screening_decision in trimmed_screening_counts:
            trimmed_screening_counts[screening_decision] += 1
        age_decision = str(row.get("age_filter_decision", "") or "").strip().lower()
        if age_decision in trimmed_age_counts:
            trimmed_age_counts[age_decision] += 1

    outcome.rows = trimmed_rows
    outcome.meta["row_count"] = len(trimmed_rows)
    if "geo_counts" in outcome.meta:
        outcome.meta["geo_counts"] = trimmed_counts
    if "screening_counts" in outcome.meta:
        outcome.meta["screening_counts"] = trimmed_screening_counts
    if "age_filter_counts" in outcome.meta:
        outcome.meta["age_filter_counts"] = trimmed_age_counts
    return outcome


def _read_items_file(path: Path) -> list[dict | str]:
    if not path.exists():
        raise ValueError(f"Links file non trovato: {path}")

    content = path.read_text(encoding="utf-8").strip()
    if not content:
        raise ValueError(f"Links file vuoto: {path}")

    if content.startswith("["):
        raw_items = json.loads(content)
        if not isinstance(raw_items, list):
            raise ValueError(f"Links file non valido: {path}")
        return [item for item in raw_items if isinstance(item, dict) or str(item).strip()]

    items: list[dict | str] = []
    for line in content.splitlines():
        value = line.strip()
        if value:
            items.append(value)
    return items
