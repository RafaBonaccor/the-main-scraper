from .location_filter import apply_geo_sorting_to_outcome
from .models import ScrapeOutcome
from .sources.custom_site import run_custom_site_scraper
from .sources.google_maps import run_google_maps_scraper
from .sources.subito import run_subito_scraper


def run_scraper(source: str, **kwargs) -> ScrapeOutcome:
    if source == "google_maps":
        return run_google_maps_scraper(
            search=kwargs["search"],
            city=kwargs.get("city", ""),
            province=kwargs.get("province", ""),
            country=kwargs.get("country", ""),
            max_results=int(kwargs.get("max_results", 25)),
            browser_mode=kwargs.get("browser_mode", "isolated"),
            browser_user_data_dir=kwargs.get("browser_user_data_dir", ""),
            browser_profile_directory=kwargs.get("browser_profile_directory", "Default"),
        )

    if source == "subito":
        outcome = _run_subito_queries(**kwargs)
        filtered = apply_geo_sorting_to_outcome(
            outcome,
            anchor_place=kwargs.get("anchor_place", "Morlupo"),
            max_distance_km=float(kwargs.get("max_distance_km", 30)),
            nearby_only=bool(kwargs.get("nearby_only", False)),
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

    if len(queries) == 1:
        outcome = run_subito_scraper(
            query=queries[0],
            region=kwargs.get("region", "lazio"),
            city=kwargs.get("city", "roma"),
            category=kwargs.get("category", "offerte-lavoro"),
            max_results=max_results,
            browser_mode=kwargs.get("browser_mode", "isolated"),
            browser_user_data_dir=kwargs.get("browser_user_data_dir", ""),
            browser_profile_directory=kwargs.get("browser_profile_directory", "Default"),
        )
        outcome.meta["query_terms"] = queries
        outcome.meta["selected_job_keywords"] = _parse_job_keywords(kwargs.get("job_keywords", ""))
        return outcome

    rows_by_key: dict[str, dict] = {}
    search_urls: list[str] = []
    cookie_actions: list[str] = []

    for query in queries:
        outcome = run_subito_scraper(
            query=query,
            region=kwargs.get("region", "lazio"),
            city=kwargs.get("city", "roma"),
            category=kwargs.get("category", "offerte-lavoro"),
            max_results=max_results,
            browser_mode=kwargs.get("browser_mode", "isolated"),
            browser_user_data_dir=kwargs.get("browser_user_data_dir", ""),
            browser_profile_directory=kwargs.get("browser_profile_directory", "Default"),
        )
        search_url = str(outcome.meta.get("search_url", "") or "")
        cookie_action = str(outcome.meta.get("cookie_banner_action", "") or "")
        if search_url:
            search_urls.append(search_url)
        if cookie_action:
            cookie_actions.append(cookie_action)

        for row in outcome.rows:
            key = _row_identity(row)
            if key not in rows_by_key:
                rows_by_key[key] = row

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
            "category": kwargs.get("category", "offerte-lavoro"),
            "search_url": search_urls[0] if search_urls else "",
            "search_urls": search_urls,
            "max_results": max_results,
            "row_count": len(rows),
            "multi_query": True,
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


def _row_identity(row: dict) -> str:
    return str(row.get("link") or f"{row.get('title', '')}|{row.get('location', '')}|{row.get('company', '')}")


def _limit_outcome_rows(outcome: ScrapeOutcome, max_results: int) -> ScrapeOutcome:
    max_rows = max(int(max_results), 1)
    if len(outcome.rows) <= max_rows:
        outcome.meta["row_count"] = len(outcome.rows)
        return outcome

    trimmed_rows = outcome.rows[:max_rows]
    trimmed_counts = {"accepted": 0, "maybe": 0, "rejected": 0}
    for row in trimmed_rows:
        decision = str(row.get("geo_decision", "maybe"))
        trimmed_counts[decision] = trimmed_counts.get(decision, 0) + 1

    outcome.rows = trimmed_rows
    outcome.meta["row_count"] = len(trimmed_rows)
    if "geo_counts" in outcome.meta:
        outcome.meta["geo_counts"] = trimmed_counts
    return outcome
