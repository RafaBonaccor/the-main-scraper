from datetime import datetime

from .date_filter import apply_age_filter_to_outcome
from .location_filter import apply_geo_sorting_to_outcome
from .models import ScrapeOutcome
from .openai_screening import DEFAULT_REASONING_EFFORT, DEFAULT_SCREENING_MODEL, apply_openai_screening_to_outcome
from .runtime_controls import consume_skip_current_item_request, consume_stop_after_current_item_request
from .sources.custom_site import run_custom_site_scraper
from .sources.google_maps import run_google_maps_scraper
from .sources.subito import run_subito_scraper
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
            browser_mode=kwargs.get("browser_mode", "isolated"),
            browser_user_data_dir=kwargs.get("browser_user_data_dir", ""),
            browser_profile_directory=kwargs.get("browser_profile_directory", "Default"),
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
            browser_mode=kwargs.get("browser_mode", "isolated"),
            browser_user_data_dir=kwargs.get("browser_user_data_dir", ""),
            browser_profile_directory=kwargs.get("browser_profile_directory", "Default"),
        )

    raise ValueError(f"Unsupported source: {source}")


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
                    browser_mode=kwargs.get("browser_mode", "isolated"),
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
