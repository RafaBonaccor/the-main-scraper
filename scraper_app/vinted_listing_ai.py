from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from agent_runtime.secrets import SecretStore

from .openai_screening import _build_openai_client
from .utils import normalize_whitespace


DEFAULT_VINTED_LISTING_MODEL = "gpt-4.1-nano"
VINTED_LISTING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "title": {"type": "string"},
        "description": {"type": "string"},
        "price": {"type": "string"},
        "category": {"type": "string"},
        "brand": {"type": "string"},
        "condition": {"type": "string"},
        "material": {"type": "string"},
    },
    "required": ["title", "description", "price", "category", "brand", "condition", "material"],
}
VINTED_LISTING_SYSTEM_PROMPT = """Sei un assistente che trasforma un testo libero in un payload strutturato per un annuncio Vinted.

Regole:
- restituisci solo JSON valido e niente testo extra;
- il risultato deve essere utile per compilare il form di Vinted;
- usa italiano naturale e conciso;
- se il brand non e chiaro, usa "No Label";
- se la condizione non e chiara, usa "Ottime";
- se il materiale non e chiaro, usa "Altro";
- se il prezzo non e presente, lascia una stringa vuota;
- la categoria deve essere una categoria plausibile e pratica per Vinted;
- la descrizione deve essere piu completa del titolo e non ripetere solo il titolo.

Non inventare dettagli importanti che non siano supportati dal testo."""


def generate_vinted_listing_payload_from_text(
    text: str,
    *,
    model: str = DEFAULT_VINTED_LISTING_MODEL,
    api_key: str = "",
    base_url: str = "",
    photo_paths: list[str] | None = None,
) -> dict[str, object]:
    source_text = normalize_whitespace(str(text or ""))
    if not source_text:
        raise ValueError("Inserisci un testo di partenza prima di generare il JSON Vinted.")

    api_key_value = _resolve_openai_api_key(api_key)
    if not api_key_value:
        raise ValueError(
            "OPENAI_API_KEY non trovato nell'ambiente o nel secret project store. "
            "Salva la chiave nel gioco oppure impostala come variabile d'ambiente prima di generare il JSON Vinted."
        )

    normalized_photos = _normalize_photo_paths(photo_paths or [])
    client = _build_openai_client(api_key=api_key_value, base_url=str(base_url or "").strip() or None)
    response = client.responses.create(
        model=str(model or DEFAULT_VINTED_LISTING_MODEL).strip() or DEFAULT_VINTED_LISTING_MODEL,
        store=False,
        temperature=0.2,
        prompt_cache_key="vinted_listing_structuring_v1",
        text={
            "verbosity": "low",
            "format": {
                "type": "json_schema",
                "name": "vinted_listing_payload",
                "strict": True,
                "schema": VINTED_LISTING_SCHEMA,
            },
        },
        input=[
            {
                "role": "system",
                "content": [{"type": "input_text", "text": VINTED_LISTING_SYSTEM_PROMPT}],
            },
            {
                "role": "user",
                "content": [{"type": "input_text", "text": source_text}],
            },
        ],
    )

    output_text = _extract_output_text(response)
    if not output_text:
        refusal = _extract_refusal_text(response)
        if refusal:
            raise ValueError(f"Il modello ha rifiutato la richiesta: {refusal}")
        raise ValueError("Risposta OpenAI vuota o non parsabile.")

    parsed = json.loads(output_text)
    payload = {
        "title": normalize_whitespace(str(parsed.get("title", "") or "")),
        "description": normalize_whitespace(str(parsed.get("description", "") or "")),
        "price": normalize_whitespace(str(parsed.get("price", "") or "")),
        "category": normalize_whitespace(str(parsed.get("category", "") or "")),
        "brand": normalize_whitespace(str(parsed.get("brand", "") or "")),
        "condition": normalize_whitespace(str(parsed.get("condition", "") or "")),
        "material": normalize_whitespace(str(parsed.get("material", "") or "")),
        "photo_paths": normalized_photos,
        "source_text": source_text,
        "model": str(model or DEFAULT_VINTED_LISTING_MODEL).strip() or DEFAULT_VINTED_LISTING_MODEL,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "raw_json": parsed,
    }
    return payload


def _resolve_openai_api_key(explicit_api_key: str = "") -> str:
    value = str(explicit_api_key or "").strip()
    if value:
        return value
    env_value = str(os.environ.get("OPENAI_API_KEY", "") or "").strip()
    if env_value:
        return env_value
    project_key = _load_project_secret_key("project")
    if project_key:
        return project_key
    return ""


def _load_project_secret_key(kind: str) -> str:
    secrets_path = _project_secrets_path()
    if not secrets_path.exists():
        return ""
    try:
        store = SecretStore(secrets_path)
        if kind == "project":
            return str(store.get_project() or "").strip()
    except Exception:
        return ""
    return ""


def _project_secrets_path() -> Path:
    for candidate in Path(__file__).resolve().parents:
        path = candidate / "data" / "secrets.json"
        if path.exists():
            return path
    return Path(__file__).resolve().parents[3] / "data" / "secrets.json"


def _normalize_photo_paths(photo_paths: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for path in photo_paths:
        value = str(path or "").strip()
        if not value:
            continue
        resolved = str(Path(value).expanduser().resolve())
        if resolved in seen:
            continue
        seen.add(resolved)
        normalized.append(resolved)
    return normalized


def _extract_output_text(response: Any) -> str:
    direct_text = str(getattr(response, "output_text", "") or "").strip()
    if direct_text:
        return direct_text
    payload = _response_to_dict(response)
    for item in payload.get("output", []) or []:
        if str(item.get("type", "") or "") != "message":
            continue
        for content in item.get("content", []) or []:
            if str(content.get("type", "") or "") == "output_text":
                text = str(content.get("text", "") or "").strip()
                if text:
                    return text
    return ""


def _extract_refusal_text(response: Any) -> str:
    payload = _response_to_dict(response)
    for item in payload.get("output", []) or []:
        if str(item.get("type", "") or "") != "message":
            continue
        for content in item.get("content", []) or []:
            refusal = str(content.get("refusal", "") or "").strip()
            if refusal:
                return refusal
    return ""


def _response_to_dict(response: Any) -> dict[str, Any]:
    if hasattr(response, "model_dump"):
        dumped = response.model_dump()
        if isinstance(dumped, dict):
            return dumped
    if isinstance(response, dict):
        return response
    return {}
