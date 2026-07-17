from __future__ import annotations

import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SEARCH_TERMS_PATH = PROJECT_ROOT / "output" / "_saved_search_terms.json"
MAX_SAVED_SEARCH_TERMS = 200


def load_saved_search_terms(scope: str) -> list[str]:
    clean_scope = _normalize_scope(scope)
    payload = _read_saved_search_terms_payload()
    values = payload.get(clean_scope, [])
    if not isinstance(values, list):
        return []
    return _normalize_search_terms(values)


def save_search_term(scope: str, value: str) -> list[str]:
    clean_scope = _normalize_scope(scope)
    clean_value = str(value or "").strip()
    if not clean_value:
        raise ValueError("Termine di ricerca mancante.")
    payload = _read_saved_search_terms_payload()
    current_values = _normalize_search_terms(payload.get(clean_scope, []))
    lowered_value = clean_value.lower()
    updated_values = [clean_value]
    for current_value in current_values:
        if current_value.lower() == lowered_value:
            continue
        updated_values.append(current_value)
        if len(updated_values) >= MAX_SAVED_SEARCH_TERMS:
            break
    payload[clean_scope] = updated_values
    _write_saved_search_terms_payload(payload)
    return updated_values


def delete_saved_search_terms(scope: str, values: list[str]) -> list[str]:
    clean_scope = _normalize_scope(scope)
    if not values:
        return load_saved_search_terms(clean_scope)
    payload = _read_saved_search_terms_payload()
    current_values = _normalize_search_terms(payload.get(clean_scope, []))
    values_to_remove = {str(value or "").strip().lower() for value in values if str(value or "").strip()}
    updated_values = [value for value in current_values if value.lower() not in values_to_remove]
    if updated_values:
        payload[clean_scope] = updated_values
    else:
        payload.pop(clean_scope, None)
    _write_saved_search_terms_payload(payload)
    return updated_values


def _normalize_scope(scope: str) -> str:
    clean_scope = str(scope or "").strip().lower()
    if not clean_scope:
        raise ValueError("Scope termini di ricerca mancante.")
    return clean_scope


def _normalize_search_terms(values: list[object]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw_value in values:
        clean_value = str(raw_value or "").strip()
        lowered = clean_value.lower()
        if not clean_value or lowered in seen:
            continue
        seen.add(lowered)
        normalized.append(clean_value)
        if len(normalized) >= MAX_SAVED_SEARCH_TERMS:
            break
    return normalized


def _read_saved_search_terms_payload() -> dict[str, list[str]]:
    if not SEARCH_TERMS_PATH.exists():
        return {}
    try:
        payload = json.loads(SEARCH_TERMS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    normalized_payload: dict[str, list[str]] = {}
    for raw_scope, raw_values in payload.items():
        scope = str(raw_scope or "").strip().lower()
        if not scope or not isinstance(raw_values, list):
            continue
        values = _normalize_search_terms(raw_values)
        if values:
            normalized_payload[scope] = values
    return normalized_payload


def _write_saved_search_terms_payload(payload: dict[str, list[str]]) -> None:
    normalized_payload = {
        str(scope or "").strip().lower(): _normalize_search_terms(values)
        for scope, values in payload.items()
        if str(scope or "").strip()
    }
    normalized_payload = {scope: values for scope, values in normalized_payload.items() if values}
    SEARCH_TERMS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SEARCH_TERMS_PATH.write_text(
        json.dumps(normalized_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
