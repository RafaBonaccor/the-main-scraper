from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DEFAULT_VINTED_AI_MODEL = "gpt-image-2"
DEFAULT_VINTED_AI_SIZE = "1024x1536"
DEFAULT_VINTED_AI_OUTPUT_DIRNAME = "vinted_ai"
DEFAULT_VINTED_AI_LOG_FILENAME = "generation_log.json"
DEFAULT_VINTED_AI_RUNTIME_URL = os.environ.get("AGENT_LAB_RUNTIME_URL", "http://127.0.0.1:8000").rstrip("/")
VINTED_AI_SUPPORTED_SIZES = (
    "1024x1024",
    "1024x1536",
    "1536x1024",
    "auto",
)


def generate_vinted_ai_variants(
    *,
    photo_paths: list[str],
    prompt: str,
    output_dir: str | Path,
    model: str = DEFAULT_VINTED_AI_MODEL,
    size: str = DEFAULT_VINTED_AI_SIZE,
    variants: int = 1,
    api_key: str = "",
    base_url: str = "",
) -> dict[str, object]:
    resolved_paths = _validate_photo_paths(photo_paths)
    cleaned_prompt = str(prompt or "").strip()
    if not cleaned_prompt:
        raise ValueError("Inserisci un prompt AI prima di generare le immagini.")
    variant_count = max(1, min(int(variants or 1), 4))
    target_dir = _prepare_output_dir(output_dir)
    runtime_url = str(base_url or "").strip() or DEFAULT_VINTED_AI_RUNTIME_URL
    log_payload: dict[str, object] = {
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "model": str(model or DEFAULT_VINTED_AI_MODEL).strip() or DEFAULT_VINTED_AI_MODEL,
        "size": str(size or DEFAULT_VINTED_AI_SIZE).strip() or DEFAULT_VINTED_AI_SIZE,
        "variants_requested": variant_count,
        "prompt": cleaned_prompt,
        "source_photo_paths": [str(path.resolve()) for path in resolved_paths],
        "output_dir": str(target_dir.resolve()),
        "backend": "agent_runtime",
        "runtime_url": runtime_url,
        "request_url": f"{runtime_url}/api/vinted-ai/generate",
        "steps": [
            {"at": datetime.now().isoformat(timespec="seconds"), "message": "Validated input photos and prompt."},
            {"at": datetime.now().isoformat(timespec="seconds"), "message": "Preparing runtime request for image generation."},
        ],
    }
    _write_generation_log(target_dir, log_payload)
    try:
        result = _request_runtime_vinted_ai_generation(
            runtime_url=runtime_url,
            photo_paths=[str(path.resolve()) for path in resolved_paths],
            prompt=cleaned_prompt,
            output_dir=target_dir,
            model=str(model or DEFAULT_VINTED_AI_MODEL).strip() or DEFAULT_VINTED_AI_MODEL,
            size=str(size or DEFAULT_VINTED_AI_SIZE).strip() or DEFAULT_VINTED_AI_SIZE,
            variants=variant_count,
        )
        log_payload.update(
            {
                "ok": True,
                "generated_photo_paths": list(result.get("generated_photo_paths", []) or []),
                "variants_generated": int(result.get("variants", 0) or 0),
                "completed_at": result["generated_at"],
                "runtime_result": result,
            }
        )
        log_payload["steps"] = list(log_payload.get("steps", [])) + [
            {"at": result["generated_at"], "message": f"Runtime returned {len(result.get('generated_photo_paths', []) or [])} generated image(s)."}
        ]
        _write_generation_log(target_dir, log_payload)
        return result
    except Exception as exc:
        failed_at = datetime.now().isoformat(timespec="seconds")
        log_payload.update(
            {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "completed_at": failed_at,
            }
        )
        log_payload["steps"] = list(log_payload.get("steps", [])) + [
            {"at": failed_at, "message": f"Generation failed: {type(exc).__name__}: {exc}"}
        ]
        _write_generation_log(target_dir, log_payload)
        raise


def _validate_photo_paths(photo_paths: list[str]) -> list[Path]:
    resolved_paths = [Path(path).expanduser().resolve() for path in photo_paths if str(path or "").strip()]
    if not resolved_paths:
        raise ValueError("Seleziona almeno una foto di riferimento per AI Listing Studio.")
    for path in resolved_paths:
        if not path.exists():
            raise FileNotFoundError(f"Foto AI non trovata: {path}")
        if path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
            raise ValueError(f"Formato non supportato per AI Listing Studio: {path.suffix}")
    return resolved_paths


def _prepare_output_dir(output_dir: str | Path) -> Path:
    root = Path(output_dir).expanduser().resolve()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = root / timestamp
    target.mkdir(parents=True, exist_ok=True)
    return target


def _write_generation_log(target_dir: Path, payload: dict[str, object]) -> None:
    try:
        (target_dir / DEFAULT_VINTED_AI_LOG_FILENAME).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        return


def _request_runtime_vinted_ai_generation(
    *,
    runtime_url: str,
    photo_paths: list[str],
    prompt: str,
    output_dir: Path,
    model: str,
    size: str,
    variants: int,
) -> dict[str, object]:
    payload = {
        "photo_paths": photo_paths,
        "prompt": prompt,
        "output_dir": str(output_dir.resolve()),
        "model": model,
        "size": size,
        "variants": variants,
    }
    request = Request(
        f"{runtime_url}/api/vinted-ai/generate",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=600) as response:
            raw = response.read().decode("utf-8")
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise RuntimeError("Runtime returned an invalid response payload.")
            return data
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace") if hasattr(exc, "read") else ""
        if exc.code == 404:
            raise RuntimeError(
                f"Runtime endpoint not found at {request.full_url}. "
                "The game runtime is likely running an older build. Restart the game so it loads /api/vinted-ai/generate."
            ) from exc
        raise RuntimeError(f"Runtime image generation failed ({exc.code}) at {request.full_url}: {detail or exc.reason}") from exc
    except URLError as exc:
        raise RuntimeError(f"Runtime image generation unavailable at {runtime_url}: {exc.reason}") from exc
