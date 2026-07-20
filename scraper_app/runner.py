import json
from datetime import datetime
from pathlib import Path

from .date_filter import apply_age_filter_to_outcome
from .exporters import write_outcome_json
from .location_filter import apply_geo_sorting_to_outcome
from .models import ScrapeOutcome
from .openai_screening import DEFAULT_REASONING_EFFORT, DEFAULT_SCREENING_MODEL, apply_openai_screening_to_outcome
from .runtime_controls import consume_skip_current_item_request, consume_stop_after_current_item_request
from .sources.custom_site import run_custom_site_scraper
from .sources.google_maps import run_google_maps_scraper
from .sources.subito import run_subito_scraper
from .sources.vinted import run_vinted_description_extractor, run_vinted_scraper
from .utils import parse_text_list


def run_scraper(source: str, **kwargs) -> ScrapeOutcome:
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

    if source == "vinted_descriptions":
        return run_vinted_description_extractor(
            items=_resolve_vinted_items(kwargs),
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
        )

    if source == "subito":
        outcome = _run_subito_queries(**kwargs)
        max_age_hours = _parse_optional_nonnegative_int(kwargs.get("max_age_hours")) or 0
        exact_age_days = _parse_optional_nonnegative_int(kwargs.get("exact_age_days"))
        filtered = apply_geo_sorting_to_outcome(
            outcome,
            anchor_place=kwargs.get("anchor_place", "Morlupo"),
            max_distance_km=float(kwargs.get("max_distance_km", 30)),
            nearby_only=bool(kwargs.get("nearby_only", False)),
        )
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


def _run_vinted_queries(**kwargs) -> ScrapeOutcome:
    search_specs = _resolve_vinted_search_specs(kwargs)
    if not search_specs:
        raise ValueError("Inserisci una ricerca Vinted o un searches-file valido.")

    if len(search_specs) == 1:
        spec = search_specs[0]
        return run_vinted_scraper(
            search=spec["search"],
            max_results=int(spec.get("max_results", 100)),
            max_price=spec.get("max_price"),
            deal_hunter_min_favorites=int(kwargs.get("deal_hunter_min_favorites", 0) or 0),
            deal_hunter_max_age_hours=float(kwargs.get("deal_hunter_max_age_hours", 24.0) or 0),
            exclude_known_items=bool(kwargs.get("exclude_known_items", True)),
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
        deal_hunter_enabled = int(kwargs.get("deal_hunter_min_favorites", 0) or 0) > 0
        inner_keep_browser_open = bool(kwargs.get("keep_browser_open", True)) if is_last or not deal_hunter_enabled else True
        try:
            outcome = run_vinted_scraper(
                search=spec["search"],
                max_results=int(spec.get("max_results", 100)),
                max_price=spec.get("max_price"),
                deal_hunter_min_favorites=int(kwargs.get("deal_hunter_min_favorites", 0) or 0),
                deal_hunter_max_age_hours=float(kwargs.get("deal_hunter_max_age_hours", 24.0) or 0),
                exclude_known_items=bool(kwargs.get("exclude_known_items", True)),
                db_path=kwargs.get("db_path", "data/scraper.db"),
                ui_result_json=str(kwargs.get("ui_result_json", "") or "") if deal_hunter_enabled else "",
                browser_mode=kwargs.get("browser_mode", "chrome_normale"),
                browser_user_data_dir=kwargs.get("browser_user_data_dir", ""),
                browser_profile_directory=kwargs.get("browser_profile_directory", "Default"),
                keep_browser_open=inner_keep_browser_open,
                refresh_browser_profile=bool(kwargs.get("refresh_browser_profile", False)),
                keep_open_seconds=int(kwargs.get("keep_open_seconds", 0)) if is_last else 0,
                slow_mode=bool(kwargs.get("slow_mode", False)),
                action_delay_seconds=float(kwargs.get("action_delay_seconds", 1.5)),
                page_settle_seconds=float(kwargs.get("page_settle_seconds", 3.0)),
                detach_browser_on_complete=not deal_hunter_enabled and is_last,
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
            "deal_hunter_candidates": deal_hunter_candidates,
            "deal_hunter_matches": deal_hunter_matches,
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
        return []
    return [
        {
            "search": search,
            "max_results": max(int(kwargs.get("max_results", 100)), 0),
            "max_price": _parse_optional_nonnegative_float(kwargs.get("max_price")),
        }
    ]


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
