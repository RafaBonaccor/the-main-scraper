from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Any

from .models import ScrapeOutcome

if TYPE_CHECKING:
    from openai import OpenAI


DEFAULT_SCREENING_MODEL = "gpt-5.5"
DEFAULT_REASONING_EFFORT = "low"
SCREENING_DECISION_ORDER = {
    "candida": 0,
    "valuta": 1,
    "no": 2,
}
GEO_DECISION_ORDER = {
    "accepted": 0,
    "maybe": 1,
    "rejected": 2,
}
SCREENING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "fit_decision": {
            "type": "string",
            "enum": ["candida", "valuta", "no"],
        },
        "fit_score": {
            "type": "integer",
            "minimum": 0,
            "maximum": 100,
        },
        "job_family": {
            "type": "string",
        },
        "reason": {
            "type": "string",
        },
        "red_flags": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 4,
        },
    },
    "required": ["fit_decision", "fit_score", "job_family", "reason", "red_flags"],
}
SYSTEM_PROMPT = """Sei un assistente che valuta annunci di lavoro per una candidata.

Obiettivo:
- decidere se vale la pena candidarsi o no;
- dare priorita ai lavori compatibili con pulizie, colf, badante, assistente familiare, domestica, baby sitter e governante;
- tenere conto della distanza da Morlupo e della chiarezza della sede.

Regole:
- usa la distanza e il filtro geografico come vincolo forte;
- se la sede e troppo generica o incerta, non dare un "candida" forte: preferisci "valuta";
- se il ruolo e chiaramente fuori target, incompatibile o pieno di segnali negativi, restituisci "no";
- se il ruolo e in target, pratico da raggiungere e senza red flag importanti, restituisci "candida";
- la motivazione deve essere breve, concreta e orientata alla decisione.

Restituisci solo il JSON richiesto dallo schema."""


def apply_openai_screening_to_outcome(
    outcome: ScrapeOutcome,
    *,
    anchor_place: str = "Morlupo",
    max_distance_km: float = 30.0,
    target_job_keywords: list[str] | None = None,
    model: str = DEFAULT_SCREENING_MODEL,
    reasoning_effort: str = DEFAULT_REASONING_EFFORT,
    max_input_chars: int = 5000,
) -> ScrapeOutcome:
    api_key = str(os.environ.get("OPENAI_API_KEY", "") or "").strip()
    if not api_key:
        raise ValueError("OPENAI_API_KEY non trovato. Impostalo prima di attivare lo screening OpenAI.")

    base_url = str(os.environ.get("OPENAI_BASE_URL", "") or "").strip() or None
    client = _build_openai_client(api_key=api_key, base_url=base_url)

    annotated_rows: list[dict[str, Any]] = []
    counts = {"candida": 0, "valuta": 0, "no": 0}
    analyzed_count = 0
    api_error_count = 0
    job_targets = [value for value in (target_job_keywords or []) if str(value).strip()]

    for row in outcome.rows:
        annotated = dict(row)
        geo_decision = str(annotated.get("geo_decision", "") or "").strip().lower()

        if geo_decision == "rejected":
            annotated.update(
                {
                    "llm_fit_decision": "no",
                    "llm_fit_score": 0,
                    "llm_job_family": "",
                    "llm_reason": "Fuori dal raggio geografico impostato.",
                    "llm_red_flags": "",
                    "llm_model": model,
                    "screening_decision": "no",
                    "screening_score": 0,
                    "screening_reason": str(annotated.get("geo_decision_reason", "") or "Fuori dal raggio geografico."),
                    "screening_source": "geo_only",
                }
            )
            counts["no"] += 1
            annotated_rows.append(annotated)
            continue

        payload = _build_listing_payload(
            annotated,
            anchor_place=anchor_place,
            max_distance_km=max_distance_km,
            target_job_keywords=job_targets,
            max_input_chars=max_input_chars,
        )
        try:
            screening = _screen_single_listing(
                client,
                payload=payload,
                model=model,
                reasoning_effort=reasoning_effort,
            )
            analyzed_count += 1
        except Exception as exc:
            api_error_count += 1
            screening = {
                "fit_decision": "valuta",
                "fit_score": 50,
                "job_family": "",
                "reason": f"Screening OpenAI non riuscito: {exc}",
                "red_flags": [],
            }

        final_decision, final_reason = _combine_decision(annotated, screening)
        fit_score = _coerce_int(screening.get("fit_score"), default=50)
        red_flags = screening.get("red_flags", [])
        if isinstance(red_flags, list):
            red_flags_text = " | ".join(str(item).strip() for item in red_flags if str(item).strip())
        else:
            red_flags_text = str(red_flags or "").strip()

        annotated.update(
            {
                "llm_fit_decision": str(screening.get("fit_decision", "valuta") or "valuta"),
                "llm_fit_score": fit_score,
                "llm_job_family": str(screening.get("job_family", "") or "").strip(),
                "llm_reason": str(screening.get("reason", "") or "").strip(),
                "llm_red_flags": red_flags_text,
                "llm_model": model,
                "screening_decision": final_decision,
                "screening_score": fit_score,
                "screening_reason": final_reason,
                "screening_source": "openai",
            }
        )
        counts[final_decision] += 1
        annotated_rows.append(annotated)

    annotated_rows.sort(key=_screening_sort_key)
    meta = dict(outcome.meta)
    meta.update(
        {
            "screening_enabled": True,
            "screening_model": model,
            "screening_reasoning_effort": reasoning_effort,
            "screening_target_roles": job_targets,
            "screening_counts": counts,
            "screening_analyzed_count": analyzed_count,
            "screening_api_error_count": api_error_count,
        }
    )
    return ScrapeOutcome(source=outcome.source, rows=annotated_rows, meta=meta)


def _build_listing_payload(
    row: dict[str, Any],
    *,
    anchor_place: str,
    max_distance_km: float,
    target_job_keywords: list[str],
    max_input_chars: int,
) -> dict[str, Any]:
    description = str(row.get("description", "") or "").strip()
    raw_text = str(row.get("raw_text", "") or "").strip()
    detail_text = description or raw_text
    trimmed_text = detail_text[: max(int(max_input_chars), 500)]

    return {
        "target_job_keywords": target_job_keywords,
        "anchor_place": anchor_place,
        "max_distance_km": max_distance_km,
        "title": str(row.get("title", "") or "").strip(),
        "company": str(row.get("company", "") or "").strip(),
        "location": str(row.get("location", "") or "").strip(),
        "published_at": str(row.get("published_at", "") or "").strip(),
        "sector": str(row.get("sector", "") or "").strip(),
        "role_type": str(row.get("role_type", "") or "").strip(),
        "schedule": str(row.get("schedule", "") or "").strip(),
        "price": str(row.get("price", "") or "").strip(),
        "distance_km": row.get("distance_km"),
        "geo_decision": str(row.get("geo_decision", "") or "").strip(),
        "geo_confidence": str(row.get("geo_confidence", "") or "").strip(),
        "geo_reason": str(row.get("geo_decision_reason", "") or "").strip(),
        "resolved_place": str(row.get("resolved_place", "") or "").strip(),
        "listing_text": trimmed_text,
    }


def _screen_single_listing(
    client: Any,
    *,
    payload: dict[str, Any],
    model: str,
    reasoning_effort: str,
) -> dict[str, Any]:
    response = client.responses.create(
        model=model,
        store=False,
        temperature=0.2,
        reasoning={"effort": reasoning_effort},
        prompt_cache_key="subito_job_screening_v1",
        text={
            "verbosity": "low",
            "format": {
                "type": "json_schema",
                "name": "job_screening",
                "strict": True,
                "schema": SCREENING_SCHEMA,
            },
        },
        input=[
            {
                "role": "system",
                "content": [{"type": "input_text", "text": SYSTEM_PROMPT}],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": json.dumps(payload, ensure_ascii=False, indent=2),
                    }
                ],
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
    return {
        "fit_decision": str(parsed.get("fit_decision", "valuta") or "valuta").strip().lower(),
        "fit_score": _coerce_int(parsed.get("fit_score"), default=50),
        "job_family": str(parsed.get("job_family", "") or "").strip(),
        "reason": str(parsed.get("reason", "") or "").strip(),
        "red_flags": parsed.get("red_flags", []),
    }


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


def _combine_decision(row: dict[str, Any], screening: dict[str, Any]) -> tuple[str, str]:
    geo_decision = str(row.get("geo_decision", "") or "").strip().lower()
    llm_decision = str(screening.get("fit_decision", "valuta") or "valuta").strip().lower()
    llm_reason = str(screening.get("reason", "") or "").strip()
    distance_km = row.get("distance_km")

    if geo_decision == "rejected":
        return "no", str(row.get("geo_decision_reason", "") or "Fuori dal raggio geografico.")

    if geo_decision == "maybe" and distance_km in ("", None) and llm_decision == "candida":
        return "valuta", "Ruolo interessante ma la sede e troppo generica per consigliarti una candidatura diretta."

    final_decision = llm_decision if llm_decision in SCREENING_DECISION_ORDER else "valuta"
    return final_decision, llm_reason or "Screening completato."


def _screening_sort_key(row: dict[str, Any]) -> tuple[Any, ...]:
    screening_decision = str(row.get("screening_decision", "") or "").strip().lower()
    screening_score = _coerce_int(row.get("screening_score"), default=0)
    geo_decision = str(row.get("geo_decision", "maybe") or "maybe").strip().lower()
    distance = row.get("distance_km")
    return (
        SCREENING_DECISION_ORDER.get(screening_decision, 99),
        -screening_score,
        GEO_DECISION_ORDER.get(geo_decision, 99),
        distance is None,
        distance if distance is not None else 9999,
        str(row.get("title", row.get("name", "")) or "").lower(),
    )


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _build_openai_client(*, api_key: str, base_url: str | None) -> Any:
    try:
        from openai import OpenAI
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Il pacchetto 'openai' non e installato nel venv corrente. "
            "Installa la dipendenza prima di attivare lo screening OpenAI."
        ) from exc

    return OpenAI(api_key=api_key, base_url=base_url)
