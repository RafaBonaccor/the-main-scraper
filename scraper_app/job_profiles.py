from __future__ import annotations

import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
JOB_PROFILES_PATH = PROJECT_ROOT / "output" / "_job_profiles.json"
DEFAULT_JOB_PROFILES: dict[str, list[str]] = {
    "Cura e casa": [
        "pulizie",
        "colf",
        "badante",
        "assistente familiare",
        "domestica",
        "baby sitter",
        "governante",
    ],
    "Pulizie e casa": [
        "pulizie",
        "colf",
        "domestica",
        "governante",
    ],
    "Assistenza anziani": [
        "badante",
        "assistente familiare",
    ],
    "Infanzia": [
        "baby sitter",
    ],
}


def load_job_profiles() -> dict[str, list[str]]:
    profiles = {name: list(keywords) for name, keywords in DEFAULT_JOB_PROFILES.items()}
    custom_profiles = load_custom_job_profiles()
    for name, keywords in custom_profiles.items():
        profiles[name] = keywords
    return profiles


def load_custom_job_profiles() -> dict[str, list[str]]:
    if not JOB_PROFILES_PATH.exists():
        return {}
    try:
        payload = json.loads(JOB_PROFILES_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}

    profiles: dict[str, list[str]] = {}
    for raw_name, raw_keywords in payload.items():
        name = str(raw_name or "").strip()
        if not name:
            continue
        keywords = normalize_job_keywords(raw_keywords if isinstance(raw_keywords, list) else parse_job_keywords(str(raw_keywords or "")))
        if keywords:
            profiles[name] = keywords
    return profiles


def save_custom_job_profile(name: str, keywords: list[str]) -> dict[str, list[str]]:
    clean_name = str(name or "").strip()
    if not clean_name:
        raise ValueError("Nome profilo mancante.")
    normalized_keywords = normalize_job_keywords(keywords)
    if not normalized_keywords:
        raise ValueError("Il profilo deve contenere almeno una keyword.")

    profiles = load_custom_job_profiles()
    profiles[clean_name] = normalized_keywords
    _write_custom_profiles(profiles)
    return profiles


def delete_custom_job_profile(name: str) -> bool:
    clean_name = str(name or "").strip()
    if not clean_name:
        return False
    profiles = load_custom_job_profiles()
    if clean_name not in profiles:
        return False
    del profiles[clean_name]
    _write_custom_profiles(profiles)
    return True


def is_builtin_job_profile(name: str) -> bool:
    return str(name or "").strip() in DEFAULT_JOB_PROFILES


def parse_job_keywords(raw_value: str) -> list[str]:
    return normalize_job_keywords(str(raw_value or "").split(","))


def normalize_job_keywords(values: list[str] | tuple[str, ...] | set[str]) -> list[str]:
    seen: set[str] = set()
    keywords: list[str] = []
    for raw_value in values:
        value = str(raw_value or "").strip()
        lowered = value.lower()
        if not value or lowered in seen:
            continue
        seen.add(lowered)
        keywords.append(value)
    return keywords


def _write_custom_profiles(profiles: dict[str, list[str]]) -> None:
    JOB_PROFILES_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {name: normalize_job_keywords(keywords) for name, keywords in profiles.items()}
    JOB_PROFILES_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
