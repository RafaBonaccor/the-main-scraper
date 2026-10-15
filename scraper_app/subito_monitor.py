from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import unicodedata
from datetime import datetime
from pathlib import Path

from .discord_notifications import send_discord_webhook_message


DEFAULT_CAREGIVER_LOCATIONS = (
    "Roma",
    "Monterotondo",
    "Guidonia Montecelio",
    "Rignano Flaminio",
    "Morlupo",
    "Capena",
    "Fiano Romano",
    "Faleria",
    "Castelnuovo di Porto",
    "Riano",
    "Sacrofano",
    "Montebello",
    "Roma Nord",
)

DEFAULT_CAREGIVER_SEARCH_TERMS = (
    "badante part time",
)

_CAREGIVER_MARKERS = (
    "badante",
    "assistente familiare",
    "assistenza anziani",
    "assistenza anziano",
    "assistenza anziana",
    "caregiver",
)

_PART_TIME_MARKERS = (
    "part time",
    "part-time",
    "parttime",
    "a ore",
    "ad ore",
    "ore settimanali",
    "mezza giornata",
    "solo mattina",
    "solo pomeriggio",
)


def parse_monitor_values(value: object, defaults: tuple[str, ...]) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        raw_values = [str(item or "") for item in value]
    else:
        raw_values = re.split(r"[,;\n]+", str(value or ""))
    values: list[str] = []
    seen: set[str] = set()
    for raw in raw_values:
        clean = " ".join(raw.split()).strip()
        key = _fold(clean)
        if not clean or key in seen:
            continue
        seen.add(key)
        values.append(clean)
    return values or list(defaults)


def build_caregiver_queries(search_terms: list[str], locations: list[str]) -> list[dict[str, str]]:
    queries: list[dict[str, str]] = []
    seen: set[str] = set()
    for location in locations:
        for search_term in search_terms:
            query = " ".join((search_term, location)).strip()
            key = _fold(query)
            if key in seen:
                continue
            seen.add(key)
            queries.append({"query": query, "location_target": location, "search_term": search_term})
    return queries


def classify_caregiver_row(row: dict, *, only_part_time: bool = True) -> tuple[bool, str]:
    text = _fold(
        " ".join(
            str(row.get(key, "") or "")
            for key in ("title", "role_type", "sector", "schedule", "description", "raw_text")
        )
    )
    if not any(marker in text for marker in _CAREGIVER_MARKERS):
        return False, "not-caregiver"
    if only_part_time and not any(marker in text for marker in _PART_TIME_MARKERS):
        return False, "not-explicitly-part-time"
    return True, "caregiver-part-time" if only_part_time else "caregiver"


def row_matches_location_target(row: dict, target: str) -> bool:
    folded_target = _fold(target)
    location = _fold(str(row.get("location", "") or ""))
    searchable = _fold(
        " ".join(
            str(row.get(key, "") or "")
            for key in ("title", "location", "description", "raw_text")
        )
    )
    aliases = {
        "guidonia montecelio": ("guidonia montecelio", "guidonia"),
        "castelnuovo di porto": ("castelnuovo di porto", "castel nuovo di porto"),
        "rignano flaminio": ("rignano flaminio", "rignano"),
        "roma nord": ("roma nord", "roma-nord"),
    }.get(folded_target, (folded_target,))
    if folded_target == "roma":
        return bool(re.search(r"\broma\b", location))
    return any(alias and alias in searchable for alias in aliases)


def persist_subito_monitor_cycle(
    db_path: str | Path,
    rows: list[dict],
    *,
    cycle_index: int,
    config: dict,
    errors: list[dict],
) -> tuple[list[dict], dict]:
    path = Path(db_path).expanduser()
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    checked_at = datetime.now().isoformat(timespec="seconds")
    new_rows: list[dict] = []

    with sqlite3.connect(path, timeout=30) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=30000")
        _ensure_schema(connection)
        run_cursor = connection.execute(
            """
            INSERT INTO subito_monitor_runs(started_at, completed_at, cycle_index, listings_count, new_count, error_count, config_json)
            VALUES (?, ?, ?, ?, 0, ?, ?)
            """,
            (
                checked_at,
                checked_at,
                int(cycle_index),
                len(rows),
                len(errors),
                json.dumps(config, ensure_ascii=False, sort_keys=True),
            ),
        )
        run_id = int(run_cursor.lastrowid)

        for source_row in rows:
            row = dict(source_row)
            identity = _row_identity(row)
            existing = connection.execute(
                "SELECT first_seen_at FROM subito_monitor_listings WHERE listing_key = ?",
                (identity,),
            ).fetchone()
            is_new = existing is None
            first_seen_at = checked_at if is_new else str(existing[0] or checked_at)
            row.update(
                {
                    "monitor_listing_key": identity,
                    "monitor_first_seen_at": first_seen_at,
                    "monitor_last_seen_at": checked_at,
                    "monitor_is_new": is_new,
                    "monitor_cycle": int(cycle_index),
                }
            )
            source_row.update(row)
            payload_json = json.dumps(row, ensure_ascii=False, sort_keys=True)
            connection.execute(
                """
                INSERT INTO subito_monitor_listings(
                    listing_key, link, title, company, location, schedule, published_at,
                    matched_target, matched_search_term, first_seen_at, last_seen_at,
                    last_cycle, seen_count, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                ON CONFLICT(listing_key) DO UPDATE SET
                    link = excluded.link,
                    title = excluded.title,
                    company = excluded.company,
                    location = excluded.location,
                    schedule = excluded.schedule,
                    published_at = excluded.published_at,
                    matched_target = excluded.matched_target,
                    matched_search_term = excluded.matched_search_term,
                    last_seen_at = excluded.last_seen_at,
                    last_cycle = excluded.last_cycle,
                    seen_count = subito_monitor_listings.seen_count + 1,
                    payload_json = excluded.payload_json
                """,
                (
                    identity,
                    str(row.get("link", "") or ""),
                    str(row.get("title", "") or ""),
                    str(row.get("company", "") or ""),
                    str(row.get("location", "") or ""),
                    str(row.get("schedule", "") or ""),
                    str(row.get("published_at", "") or ""),
                    str(row.get("monitor_location_target", "") or ""),
                    str(row.get("monitor_search_term", "") or ""),
                    first_seen_at,
                    checked_at,
                    int(cycle_index),
                    payload_json,
                ),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO subito_monitor_sightings(run_id, listing_key, seen_at, payload_json)
                VALUES (?, ?, ?, ?)
                """,
                (run_id, identity, checked_at, payload_json),
            )
            if is_new:
                new_rows.append(row)

        connection.execute(
            "UPDATE subito_monitor_runs SET new_count = ? WHERE id = ?",
            (len(new_rows), run_id),
        )
        connection.commit()

    return new_rows, {
        "db_path": str(path),
        "monitor_run_id": run_id,
        "monitor_checked_at": checked_at,
        "monitor_row_count": len(rows),
        "monitor_new_count": len(new_rows),
        "monitor_error_count": len(errors),
    }


def notify_new_caregiver_rows(webhook_url: str, rows: list[dict]) -> dict:
    url = str(webhook_url or "").strip()
    if not url or not rows:
        return {"attempted": 0, "sent": 0, "errors": []}
    sent = 0
    errors: list[str] = []
    for row in rows:
        lines = [
            "🧑‍⚕️ **Nuovo lavoro badante part-time**",
            str(row.get("title", "Annuncio") or "Annuncio"),
        ]
        for label, key in (
            ("Zona", "monitor_location_target"),
            ("Località annuncio", "location"),
            ("Orario", "schedule"),
            ("Azienda", "company"),
            ("Pubblicato", "published_at"),
        ):
            value = " ".join(str(row.get(key, "") or "").split()).strip()
            if value:
                lines.append(f"{label}: {value}")
        link = str(row.get("link", "") or "").strip()
        if link:
            lines.append(f"Annuncio: <{link}>")
        result = send_discord_webhook_message(url, "\n".join(lines))
        if bool(result.get("ok")):
            sent += 1
        else:
            errors.append(str(result.get("error", "Discord notification failed") or "Discord notification failed"))
    return {"attempted": len(rows), "sent": sent, "errors": errors}


def _ensure_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS subito_monitor_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT NOT NULL,
            completed_at TEXT NOT NULL,
            cycle_index INTEGER NOT NULL,
            listings_count INTEGER NOT NULL DEFAULT 0,
            new_count INTEGER NOT NULL DEFAULT 0,
            error_count INTEGER NOT NULL DEFAULT 0,
            config_json TEXT NOT NULL DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS subito_monitor_listings (
            listing_key TEXT PRIMARY KEY,
            link TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL DEFAULT '',
            company TEXT NOT NULL DEFAULT '',
            location TEXT NOT NULL DEFAULT '',
            schedule TEXT NOT NULL DEFAULT '',
            published_at TEXT NOT NULL DEFAULT '',
            matched_target TEXT NOT NULL DEFAULT '',
            matched_search_term TEXT NOT NULL DEFAULT '',
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            last_cycle INTEGER NOT NULL DEFAULT 0,
            seen_count INTEGER NOT NULL DEFAULT 1,
            payload_json TEXT NOT NULL DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS subito_monitor_sightings (
            run_id INTEGER NOT NULL,
            listing_key TEXT NOT NULL,
            seen_at TEXT NOT NULL,
            payload_json TEXT NOT NULL DEFAULT '{}',
            PRIMARY KEY(run_id, listing_key)
        );

        CREATE INDEX IF NOT EXISTS idx_subito_monitor_last_seen
            ON subito_monitor_listings(last_seen_at DESC);
        CREATE INDEX IF NOT EXISTS idx_subito_monitor_target
            ON subito_monitor_listings(matched_target, last_seen_at DESC);
        """
    )


def _row_identity(row: dict) -> str:
    link = str(row.get("link", "") or "").strip()
    if link:
        return link.split("?", 1)[0].rstrip("/")
    raw = "|".join(
        _fold(str(row.get(key, "") or ""))
        for key in ("title", "company", "location", "published_at")
    )
    return "fallback:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _fold(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    ascii_text = "".join(char for char in normalized if not unicodedata.combining(char))
    return " ".join(ascii_text.lower().split())
