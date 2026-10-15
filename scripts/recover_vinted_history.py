#!/usr/bin/env python3
"""Recover legacy unavailable-listing metadata from previously exported reports."""

from __future__ import annotations

import argparse
import html
import json
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path


ROW_PATTERN = re.compile(r"<tr data-article-row\b.*?</tr>", re.IGNORECASE | re.DOTALL)
CELL_PATTERN = re.compile(r"<td>(.*?)</td>", re.IGNORECASE | re.DOTALL)
TAG_PATTERN = re.compile(r"<[^>]+>")


def plain(value: str) -> str:
    return " ".join(html.unescape(TAG_PATTERN.sub(" ", value)).split())


def attribute(fragment: str, name: str) -> str:
    match = re.search(rf'\b{re.escape(name)}="([^"]*)"', fragment, re.IGNORECASE)
    return html.unescape(match.group(1)) if match else ""


def parse_report_rows(report: Path) -> dict[str, dict]:
    content = report.read_text(encoding="utf-8", errors="ignore")
    recovered: dict[str, dict] = {}
    for fragment in ROW_PATTERN.findall(content):
        link_match = re.search(r'<a href="([^"]*?/items/(\d+)[^"]*)"[^>]*>(.*?)</a>', fragment, re.I | re.S)
        if not link_match:
            continue
        cells = CELL_PATTERN.findall(fragment)
        if len(cells) < 7:
            continue
        item_id = link_match.group(2)
        sale_hours_text = attribute(fragment, "data-sale-hours")
        try:
            sale_hours = float(sale_hours_text)
        except ValueError:
            sale_hours = -1.0
        image_match = re.search(r'<img src="([^"]+)"', fragment, re.I)
        status = attribute(fragment, "data-status") or plain(cells[1])
        first_seen_at = plain(cells[6])
        sold_at = ""
        if status == "sold" and sale_hours >= 0:
            try:
                sold_at = (datetime.fromisoformat(first_seen_at) + timedelta(hours=sale_hours)).isoformat(timespec="seconds")
            except ValueError:
                pass
        recovered[item_id] = {
            "item_id": item_id,
            "link": html.unescape(link_match.group(1)),
            "name": plain(link_match.group(3)),
            "image_url": html.unescape(image_match.group(1)) if image_match else "",
            "price": plain(cells[2]),
            "favorite_count": plain(cells[3]),
            "search_term": plain(cells[4]),
            "deal_sold_after_text": plain(cells[5]),
            "first_seen_at": first_seen_at,
            "sold_at": sold_at,
            "status": status,
            "recovered_from_report": str(report.resolve()),
        }
    return recovered


def recover(db_path: Path, report_dirs: list[Path], *, apply: bool) -> dict[str, int]:
    reports = sorted({path.resolve() for directory in report_dirs for path in directory.glob("*.html")})
    report_rows: dict[str, dict] = {}
    for report in reports:
        report_rows.update(parse_report_rows(report))
    recovered = missing = 0
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        tombstones = connection.execute(
            "SELECT * FROM vinted_unavailable_items ORDER BY detected_at"
        ).fetchall()
        for tombstone in tombstones:
            item_id = str(tombstone["item_id"] or "")
            row = report_rows.get(item_id)
            if row is None:
                missing += 1
                continue
            recovered += 1
            if not apply:
                continue
            detected_at = str(tombstone["detected_at"] or "")
            previous_status = str(row.get("status", "") or "")
            historical_status = "sold" if previous_status == "sold" else "unavailable"
            snapshot = {
                **row,
                "unavailable_at": detected_at,
                "unavailable_reason": "page_not_found",
                "legacy_history_recovered": True,
            }
            connection.execute(
                """
                UPDATE vinted_unavailable_items
                SET previous_status = ?, first_seen_at = ?, sold_at = ?, image_url = ?, snapshot_json = ?
                WHERE item_link = ?
                """,
                (
                    previous_status,
                    str(row.get("first_seen_at", "") or ""),
                    str(row.get("sold_at", "") or ""),
                    str(row.get("image_url", "") or ""),
                    json.dumps(snapshot, ensure_ascii=False),
                    str(tombstone["item_link"] or ""),
                ),
            )
            connection.execute(
                """
                INSERT INTO vinted_deal_sale_tracking(
                    item_link, item_id, item_name, first_seen_at, last_checked_at, sold_at,
                    status, availability_status, unavailable_at, unavailable_reason, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'unavailable', ?, 'page_not_found', ?)
                ON CONFLICT(item_link) DO UPDATE SET
                    availability_status = 'unavailable',
                    unavailable_at = excluded.unavailable_at,
                    unavailable_reason = excluded.unavailable_reason
                """,
                (
                    str(tombstone["item_link"] or row["link"]),
                    item_id,
                    str(row.get("name", "") or ""),
                    str(row.get("first_seen_at", "") or detected_at),
                    detected_at,
                    str(row.get("sold_at", "") or ""),
                    historical_status,
                    detected_at,
                    json.dumps(snapshot, ensure_ascii=False),
                ),
            )
            existing_event = connection.execute(
                "SELECT 1 FROM vinted_item_events WHERE item_id = ? AND event_type = 'legacy_history_recovered'",
                (item_id,),
            ).fetchone()
            if existing_event is None:
                connection.execute(
                    """
                    INSERT INTO vinted_item_events(item_link, item_id, event_type, occurred_at, payload_json)
                    VALUES (?, ?, 'legacy_history_recovered', ?, ?)
                    """,
                    (
                        str(tombstone["item_link"] or row["link"]),
                        item_id,
                        datetime.now().isoformat(timespec="seconds"),
                        json.dumps(snapshot, ensure_ascii=False),
                    ),
                )
        if apply:
            connection.commit()
    return {"reports": len(reports), "recovered": recovered, "missing": missing}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/scraper.db")
    parser.add_argument("--reports", action="append", default=[])
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    project_root = Path(__file__).resolve().parents[1]
    root = project_root.parents[1]
    report_dirs = [Path(value).expanduser().resolve() for value in args.reports] or [
        project_root / "data" / "reports",
        root / "runtime" / "reports",
    ]
    result = recover((project_root / args.db).resolve(), report_dirs, apply=args.apply)
    print(json.dumps({**result, "applied": bool(args.apply)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
