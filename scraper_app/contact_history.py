from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONTACT_HISTORY_PATH = PROJECT_ROOT / "output" / "_contact_history.json"


def load_contact_history() -> dict[str, dict[str, Any]]:
    if not CONTACT_HISTORY_PATH.exists():
        return {}
    try:
        payload = json.loads(CONTACT_HISTORY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    history: dict[str, dict[str, Any]] = {}
    for raw_link, raw_entry in payload.items():
        link = str(raw_link or "").strip()
        if not link or not isinstance(raw_entry, dict):
            continue
        history[link] = dict(raw_entry)
    return history


def record_contact_result(result: dict[str, Any], source: str = "subito") -> None:
    record_contact_results([result], source=source)


def record_contact_results(results: list[dict[str, Any]], source: str = "subito") -> None:
    history = load_contact_history()
    now = _utc_now_iso()

    for result in results:
        link = str(result.get("link", "") or "").strip()
        if not link:
            continue

        entry = dict(history.get(link, {}))
        entry.setdefault("source", source)
        entry.setdefault("link", link)
        entry["attempt_count"] = int(entry.get("attempt_count", 0) or 0) + 1
        entry["prepared_count"] = int(entry.get("prepared_count", 0) or 0)
        entry["submitted_count"] = int(entry.get("submitted_count", 0) or 0)
        entry["failed_count"] = int(entry.get("failed_count", 0) or 0)
        entry["last_attempt_at"] = now
        entry["last_attachment_path"] = str(result.get("attachment_path", "") or "")

        if bool(result.get("prepared", False)):
            entry["prepared_count"] += 1
        if bool(result.get("submitted", False)):
            entry["submitted_count"] += 1
            entry["last_submitted_at"] = now
        if not bool(result.get("ok", False)):
            entry["failed_count"] += 1

        entry["last_status"] = _status_from_result(result)
        history[link] = entry

    _write_contact_history(history)


def annotate_rows_with_contact_history(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    history = load_contact_history()
    annotated_rows: list[dict[str, Any]] = []
    for row in rows:
        annotated = dict(row)
        link = str(annotated.get("link", "") or "").strip()
        entry = history.get(link, {})
        prepared_count = int(entry.get("prepared_count", 0) or 0)
        submitted_count = int(entry.get("submitted_count", 0) or 0)
        failed_count = int(entry.get("failed_count", 0) or 0)
        last_status = str(entry.get("last_status", "") or "").strip().lower()

        contact_status = "new"
        if submitted_count > 0:
            contact_status = "submitted"
        elif last_status == "prepared" or prepared_count > 0:
            contact_status = "prepared"
        elif last_status == "failed" or failed_count > 0:
            contact_status = "failed"

        annotated["contact_status"] = contact_status
        annotated["contact_attempt_count"] = int(entry.get("attempt_count", 0) or 0)
        annotated["contact_prepared_count"] = prepared_count
        annotated["contact_submitted_count"] = submitted_count
        annotated["contact_failed_count"] = failed_count
        annotated["contact_last_attempt_at"] = str(entry.get("last_attempt_at", "") or "")
        annotated["contact_last_submitted_at"] = str(entry.get("last_submitted_at", "") or "")
        annotated["contact_already_submitted"] = submitted_count > 0
        annotated_rows.append(annotated)
    return annotated_rows


def summarize_contact_status(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"new": 0, "prepared": 0, "submitted": 0, "failed": 0}
    for row in rows:
        status = str(row.get("contact_status", "new") or "new").strip().lower()
        if status not in counts:
            status = "new"
        counts[status] += 1
    return counts


def _status_from_result(result: dict[str, Any]) -> str:
    if bool(result.get("submitted", False)):
        return "submitted"
    if bool(result.get("prepared", False)):
        return "prepared"
    return "failed"


def _write_contact_history(history: dict[str, dict[str, Any]]) -> None:
    CONTACT_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONTACT_HISTORY_PATH.write_text(
        json.dumps(history, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().replace(microsecond=0).isoformat()
