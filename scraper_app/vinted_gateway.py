from __future__ import annotations

from urllib.parse import quote_plus

from .vinted_deals import (
    VINTED_DEAL_HUNTER_DEFAULT_CATEGORY_LABEL,
    VINTED_DEAL_HUNTER_DEFAULT_MAX_RESULTS_PER_SEARCH,
    VINTED_DEAL_HUNTER_DEFAULT_TERMS,
    normalize_vinted_deal_hunter_terms,
)


VINTED_CATEGORY_PRESETS = (
    ("Nessuna categoria rapida", ""),
    ("Monitoraggio", "__all_categories__"),
    ("Accessori donna", "https://www.vinted.it/catalog/1187-accessories"),
    ("Accessori uomo", "https://www.vinted.it/catalog/82-accessories"),
    ("Elettronica", "https://www.vinted.it/catalog/2994-electronics"),
    ("Gioielli donna", "https://www.vinted.it/catalog/21-jewellery"),
    ("Scarpe donna", "https://www.vinted.it/catalog/16-shoes"),
    ("Scarpe uomo", "https://www.vinted.it/catalog/1231-shoes"),
)
VINTED_CATEGORY_PRESET_LOOKUP = {label: url for label, url in VINTED_CATEGORY_PRESETS}
VINTED_MONITORING_CATEGORY_LABEL = "Monitoraggio"
VINTED_MONITORING_DEFAULT_TERMS = (
    "nike",
    "adidas",
    "jordan",
    "new balance",
    "apple",
    "airpods",
    "magsafe",
    "pokemon",
    "pandora",
    "swarovski",
    "gucci",
    "prada",
)


def _iter_vinted_monitoring_categories() -> list[tuple[str, str]]:
    return [
        (label, url)
        for label, url in VINTED_CATEGORY_PRESETS
        if url and url != "__all_categories__"
    ]


def resolve_vinted_category_url(selection: object) -> str:
    value = str(selection or "").strip()
    if not value:
        return ""
    return str(VINTED_CATEGORY_PRESET_LOOKUP.get(value, value) or "").strip()


def build_vinted_search_target_url(raw_search: object, category_selection: object = "") -> str:
    search = str(raw_search or "").strip()
    category_url = resolve_vinted_category_url(category_selection)
    if search.lower().startswith(("http://", "https://")):
        return search
    if category_url:
        if not search:
            return category_url
        separator = "&" if "?" in category_url else "?"
        return f"{category_url}{separator}search_text={quote_plus(search)}"
    if not search:
        return "https://www.vinted.it/catalog"
    return f"https://www.vinted.it/catalog?search_text={quote_plus(search)}"


def format_vinted_search_target_label(raw_search: object, category_selection: object = "") -> str:
    search = str(raw_search or "").strip()
    category_label = str(category_selection or "").strip()
    category_url = resolve_vinted_category_url(category_selection)
    if search.lower().startswith(("http://", "https://")):
        return search
    if search and category_url:
        return f"{search} | {category_label}"
    if category_url:
        return category_label or category_url
    return search


def detect_vinted_category_label_from_url(url: object) -> str:
    value = str(url or "").strip()
    if not value:
        return VINTED_CATEGORY_PRESETS[0][0]
    for label, category_url in VINTED_CATEGORY_PRESETS[1:]:
        if category_url and value.startswith(category_url):
            return label
    return VINTED_CATEGORY_PRESETS[0][0]


def build_vinted_deal_hunter_search_specs(
    raw_terms: object,
    category_selection: object,
    max_results: object = VINTED_DEAL_HUNTER_DEFAULT_MAX_RESULTS_PER_SEARCH,
    max_price: float | None = None,
) -> list[dict]:
    try:
        normalized_max_results = max(int(max_results), 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid max results for deal hunter search.") from exc
    category_label = str(category_selection or "").strip() or VINTED_DEAL_HUNTER_DEFAULT_CATEGORY_LABEL
    normalized_terms = normalize_vinted_deal_hunter_terms(raw_terms, use_default_when_empty=False)
    if category_label == VINTED_MONITORING_CATEGORY_LABEL:
        if not normalized_terms:
            normalized_terms = list(VINTED_MONITORING_DEFAULT_TERMS)
        monitoring_specs: list[dict] = []
        for resolved_category_label, _resolved_category_url in _iter_vinted_monitoring_categories():
            for term in normalized_terms:
                resolved_search = build_vinted_search_target_url(term, resolved_category_label)
                monitoring_specs.append(
                    {
                        "search": resolved_search,
                        "display_search": f"{term} | {resolved_category_label} | {VINTED_MONITORING_CATEGORY_LABEL}",
                        "category_label": VINTED_MONITORING_CATEGORY_LABEL,
                        "max_results": normalized_max_results,
                        "max_price": max_price,
                    }
                )
        return monitoring_specs
    if not normalized_terms:
        resolved_search = build_vinted_search_target_url("", category_label)
        return [
            {
                "search": resolved_search,
                "display_search": format_vinted_search_target_label("", category_label) or resolved_search,
                "category_label": category_label if resolve_vinted_category_url(category_label) else "",
                "max_results": normalized_max_results,
                "max_price": max_price,
            }
        ]
    specs: list[dict] = []
    for term in normalized_terms:
        resolved_search = build_vinted_search_target_url(term, category_label)
        specs.append(
            {
                "search": resolved_search,
                "display_search": format_vinted_search_target_label(term, category_label) or resolved_search,
                "category_label": category_label if resolve_vinted_category_url(category_label) else "",
                "max_results": normalized_max_results,
                "max_price": max_price,
            }
        )
    return specs
