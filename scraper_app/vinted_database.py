import json
import os
import re
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


DEFAULT_VINTED_DB_PATH = Path("data") / "scraper.db"
VINTED_ITEM_ID_PATTERN = re.compile(r"/items/(\d+)")


def _decode_json_string_list(value: object) -> list[str]:
    try:
        decoded = json.loads(str(value or "[]"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    if not isinstance(decoded, list):
        return []
    return [str(item).strip() for item in decoded if str(item).strip()]


def ensure_vinted_database(db_path: str | Path = DEFAULT_VINTED_DB_PATH) -> Path:
    path = Path(db_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    backup_vinted_database(path)
    try:
        with closing(sqlite3.connect(path)) as connection:
            _create_schema(connection)
            connection.commit()
    except sqlite3.Error as exc:
        raise ValueError(f"Impossibile creare il database Vinted: {exc}") from exc
    return path


def backup_vinted_database(db_path: str | Path = DEFAULT_VINTED_DB_PATH) -> Path | None:
    """Create one verified, immutable SQLite backup per day."""
    path = Path(db_path).expanduser().resolve()
    if path.name != "scraper.db" or not path.is_file() or path.stat().st_size <= 0:
        return None
    backup_dir = path.parent / "backups" / "daily"
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / f"{path.stem}-{datetime.now().strftime('%Y%m%d')}{path.suffix}"
    if target.exists():
        return target
    lock_path = target.with_suffix(f"{target.suffix}.lock")
    try:
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return target if target.exists() else None
    temp_path = target.with_suffix(f"{target.suffix}.{os.getpid()}.tmp")
    try:
        os.close(lock_fd)
        with closing(sqlite3.connect(path)) as source, closing(sqlite3.connect(temp_path)) as destination:
            source.backup(destination)
            integrity = str(destination.execute("PRAGMA integrity_check").fetchone()[0] or "")
            if integrity.lower() != "ok":
                raise sqlite3.DatabaseError(f"backup integrity check failed: {integrity}")
        temp_path.replace(target)
        print(f"[vinted-db] daily backup ready path={target}", flush=True)
        return target
    except (OSError, sqlite3.Error) as exc:
        print(f"[vinted-db] daily backup failed path={target} error={exc}", flush=True)
        return None
    finally:
        temp_path.unlink(missing_ok=True)
        lock_path.unlink(missing_ok=True)


def extract_vinted_item_id_from_link(link: object) -> str:
    raw_link = str(link or "").strip()
    if not raw_link:
        return ""
    try:
        match = VINTED_ITEM_ID_PATTERN.search(urlsplit(raw_link).path)
    except ValueError:
        return ""
    return str(match.group(1) if match else "")


def build_vinted_item_identity_keys(item_id: object = "", link: object = "") -> tuple[str, ...]:
    keys: list[str] = []
    normalized_item_id = str(item_id or "").strip() or extract_vinted_item_id_from_link(link)
    normalized_link = str(link or "").strip()
    if normalized_item_id:
        keys.append(f"id:{normalized_item_id}")
    if normalized_link:
        keys.append(f"link:{normalized_link}")
    return tuple(keys)


def build_vinted_item_identity_key(item_id: object = "", link: object = "") -> str:
    keys = build_vinted_item_identity_keys(item_id=item_id, link=link)
    return keys[0] if keys else ""


def normalize_vinted_member_id(profile_url: object) -> str:
    raw_url = str(profile_url or "").strip()
    if not raw_url:
        return ""
    try:
        parsed = urlsplit(raw_url)
    except ValueError:
        return ""
    match = re.search(r"/member/(\d+)", parsed.path)
    if match:
        return match.group(1)
    match = re.search(r"\b(\d{4,})\b", raw_url)
    return match.group(1) if match else ""


def classify_vinted_competitor_status(
    *,
    interesting_item_count: int,
    distinct_search_terms: int,
    gone_item_count: int,
    max_favorite_count: int,
    same_keyword_deal_count: int = 0,
) -> str:
    if gone_item_count >= 2 and (
        interesting_item_count >= 3
        or same_keyword_deal_count >= 3
        or max_favorite_count >= 70
    ):
        return "priority"
    if gone_item_count >= 1 or same_keyword_deal_count >= 3:
        return "confirmed"
    if interesting_item_count >= 3 or distinct_search_terms >= 2:
        return "active"
    if interesting_item_count >= 1:
        return "candidate"
    return ""


def load_vinted_known_item_keys(db_path: str | Path = DEFAULT_VINTED_DB_PATH) -> set[str]:
    path = Path(db_path).expanduser().resolve()
    ensure_vinted_database(path)
    try:
        with closing(sqlite3.connect(path)) as connection:
            records = connection.execute("SELECT item_id, link FROM vinted_items").fetchall()
            unavailable_records = connection.execute(
                "SELECT item_id, item_link FROM vinted_unavailable_items"
            ).fetchall()
    except sqlite3.Error as exc:
        raise ValueError(f"Database Vinted non valido: {exc}") from exc

    keys: set[str] = set()
    for item_id, link in records:
        keys.update(build_vinted_item_identity_keys(item_id=item_id, link=link))
    for item_id, link in unavailable_records:
        keys.update(build_vinted_item_identity_keys(item_id=item_id, link=link))
    return keys


def load_vinted_completed_detail_rows(db_path: str | Path = DEFAULT_VINTED_DB_PATH) -> dict[str, dict]:
    path = Path(db_path).expanduser().resolve()
    ensure_vinted_database(path)
    try:
        with closing(sqlite3.connect(path)) as connection:
            connection.row_factory = sqlite3.Row
            records = connection.execute(
                """
                SELECT
                    link,
                    item_id,
                    name,
                    description,
                    published_at,
                    price_text,
                    price_value,
                    shipping_price_text,
                    shipping_price_value,
                    total_price_text,
                    total_price_value,
                    offer_available,
                    offer_text,
                    favorite_count,
                    seller_name,
                    seller_profile_url,
                    seller_member_id,
                    detail_category_path,
                    detail_catalog_ids,
                    evaluation_label,
                    currency,
                    raw_text,
                    first_seen_at,
                    last_seen_at
                FROM vinted_items
                WHERE
                    description IS NOT NULL AND description <> ''
                    AND (
                        price_text IS NOT NULL AND price_text <> ''
                        OR price_value IS NOT NULL
                        OR total_price_text IS NOT NULL AND total_price_text <> ''
                        OR total_price_value IS NOT NULL
                    )
                """
            ).fetchall()
    except sqlite3.Error as exc:
        raise ValueError(f"Database Vinted non valido: {exc}") from exc

    rows_by_key: dict[str, dict] = {}
    for record in records:
        row = {
            "source": "vinted",
            "item_id": record["item_id"],
            "name": record["name"],
            "description": record["description"],
            "published_at": record["published_at"],
            "price": record["price_text"],
            "price_value": record["price_value"],
            "shipping_price": record["shipping_price_text"],
            "shipping_price_value": record["shipping_price_value"],
            "shipping_alert": "sped > 2,99"
            if record["shipping_price_value"] not in ("", None) and float(record["shipping_price_value"]) > 2.99
            else "",
            "total_price": record["total_price_text"],
            "total_price_value": record["total_price_value"],
            "offer_available": bool(record["offer_available"]),
            "offer_text": record["offer_text"],
            "favorite_count": record["favorite_count"],
            "seller_name": record["seller_name"],
            "seller_profile_url": record["seller_profile_url"],
            "seller_member_id": record["seller_member_id"],
            "detail_category_path": record["detail_category_path"],
            "detail_catalog_ids": _decode_json_string_list(record["detail_catalog_ids"]),
            "evaluation_label": record["evaluation_label"] or "",
            "currency": record["currency"],
            "link": record["link"],
            "raw_text": record["raw_text"],
            "first_seen_at": record["first_seen_at"],
            "last_seen_at": record["last_seen_at"],
            "extracted_at": record["last_seen_at"],
            "db_path": str(path),
            "db_saved": True,
            "detail_cached": True,
        }
        for key in build_vinted_item_identity_keys(item_id=record["item_id"], link=record["link"]):
            rows_by_key.setdefault(key, row)
    return rows_by_key


def load_vinted_submitted_offer_keys(db_path: str | Path = DEFAULT_VINTED_DB_PATH) -> set[str]:
    path = Path(db_path).expanduser().resolve()
    ensure_vinted_database(path)
    try:
        with closing(sqlite3.connect(path)) as connection:
            records = connection.execute(
                "SELECT item_id, item_link FROM vinted_offer_history"
            ).fetchall()
    except sqlite3.Error as exc:
        raise ValueError(f"Database Vinted non valido: {exc}") from exc

    keys: set[str] = set()
    for item_id, link in records:
        keys.update(build_vinted_item_identity_keys(item_id=item_id, link=link))
    return keys


def load_vinted_notified_deal_keys(
    db_path: str | Path = DEFAULT_VINTED_DB_PATH,
    webhook_target: str = "",
) -> set[str]:
    path = Path(db_path).expanduser().resolve()
    ensure_vinted_database(path)
    normalized_target = str(webhook_target or "").strip()
    try:
        with closing(sqlite3.connect(path)) as connection:
            if normalized_target:
                records = connection.execute(
                    "SELECT item_id, item_link FROM vinted_deal_notifications WHERE webhook_target = ?",
                    (normalized_target,),
                ).fetchall()
            else:
                records = connection.execute(
                    "SELECT item_id, item_link FROM vinted_deal_notifications"
                ).fetchall()
    except sqlite3.Error as exc:
        raise ValueError(f"Database Vinted non valido: {exc}") from exc

    keys: set[str] = set()
    for item_id, link in records:
        keys.update(build_vinted_item_identity_keys(item_id=item_id, link=link))
    return keys


def save_vinted_deal_notifications(
    rows: list[dict],
    db_path: str | Path = DEFAULT_VINTED_DB_PATH,
    webhook_target: str = "",
) -> dict[str, object]:
    path = Path(db_path).expanduser().resolve()
    ensure_vinted_database(path)
    normalized_target = str(webhook_target or "").strip()
    valid_rows = [
        row
        for row in rows
        if isinstance(row, dict) and str(row.get("link", "") or "").strip()
    ]
    if not valid_rows:
        return {
            "db_path": str(path),
            "deal_notifications_saved_count": 0,
            "new_deal_notifications": 0,
            "updated_deal_notifications": 0,
        }

    new_entries = 0
    updated_entries = 0
    affected_seller_ids: set[str] = set()
    with closing(sqlite3.connect(path)) as connection:
        _create_schema(connection)
        for row in valid_rows:
            link = str(row.get("link", "") or "").strip()
            item_id = str(row.get("item_id", "") or "").strip() or extract_vinted_item_id_from_link(link)
            already_exists = connection.execute(
                "SELECT 1 FROM vinted_deal_notifications WHERE item_link = ? AND webhook_target = ?",
                (link, normalized_target),
            ).fetchone() is not None
            sent_at = str(row.get("notification_sent_at", "") or "").strip() or datetime.now().isoformat(timespec="seconds")
            connection.execute(
                """
                INSERT INTO vinted_deal_notifications (
                    item_link, webhook_target, item_id, item_name, search_term, sent_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(item_link, webhook_target) DO UPDATE SET
                    item_id = excluded.item_id,
                    item_name = excluded.item_name,
                    search_term = excluded.search_term,
                    sent_at = excluded.sent_at,
                    payload_json = excluded.payload_json
                """,
                (
                    link,
                    normalized_target,
                    item_id,
                    str(row.get("name", "") or ""),
                    str(row.get("search_term", "") or ""),
                    sent_at,
                    json.dumps(row, ensure_ascii=False),
                ),
            )
            if already_exists:
                updated_entries += 1
            else:
                new_entries += 1
            _upsert_vinted_deal_sale_tracking(connection, row=row, first_seen_at=sent_at)
            seller_record = connection.execute(
                "SELECT seller_member_id FROM vinted_items WHERE link = ?",
                (link,),
            ).fetchone()
            if seller_record is not None and str(seller_record[0] or "").strip():
                affected_seller_ids.add(str(seller_record[0]).strip())
        connection.commit()

    competitor_meta = (
        update_vinted_competitors_for_sellers(affected_seller_ids, db_path=path)
        if affected_seller_ids
        else {
            "competitor_sellers_updated": 0,
            "competitor_status_counts": {},
        }
    )
    return {
        "db_path": str(path),
        "deal_notifications_saved_count": len(valid_rows),
        "new_deal_notifications": new_entries,
        "updated_deal_notifications": updated_entries,
        **competitor_meta,
    }


def track_vinted_deal_sales(rows: list[dict], db_path: str | Path = DEFAULT_VINTED_DB_PATH) -> int:
    """Start lifecycle tracking for qualified deals, even when Discord is disabled."""
    path = Path(db_path).expanduser().resolve()
    ensure_vinted_database(path)
    valid_rows = [row for row in rows if isinstance(row, dict) and str(row.get("link", "") or "").strip()]
    if not valid_rows:
        return 0
    now = datetime.now().isoformat(timespec="seconds")
    with closing(sqlite3.connect(path)) as connection:
        _create_schema(connection)
        for row in valid_rows:
            _upsert_vinted_deal_sale_tracking(connection, row=row, first_seen_at=now)
        connection.commit()
    return len(valid_rows)


def load_vinted_sold_deal_identity_keys(
    db_path: str | Path = DEFAULT_VINTED_DB_PATH,
) -> set[str]:
    """Return stable identities for deals already confirmed as sold.

    The search runtime uses these keys before opening item detail pages, so a
    deal that has already completed its sale lifecycle is not needlessly
    revisited when it reappears in a catalog response.
    """
    path = Path(db_path).expanduser().resolve()
    ensure_vinted_database(path)
    try:
        with closing(sqlite3.connect(path)) as connection:
            records = connection.execute(
                """
                SELECT item_id, item_link
                FROM vinted_deal_sale_tracking
                WHERE status = 'sold' AND sold_at <> ''
                """
            ).fetchall()
    except sqlite3.Error as exc:
        raise ValueError(f"Database Vinted non valido: {exc}") from exc
    keys: set[str] = set()
    for item_id, link in records:
        keys.update(build_vinted_item_identity_keys(item_id=item_id, link=link))
    return keys


def delete_vinted_item_by_link(
    link: str,
    db_path: str | Path = DEFAULT_VINTED_DB_PATH,
) -> dict[str, int | str]:
    """Archive an unavailable listing without destroying its historical data.

    The public name is retained for compatibility with existing scraper call
    sites.  Older versions physically deleted notifications, sale tracking,
    offers, item rows and profile snapshots here.  Marketplace history is now
    append-only: we store a tombstone plus an event and mark the live tracking
    record unavailable while preserving confirmed sales.
    """
    normalized_link = str(link or "").strip()
    if not normalized_link:
        return {"link": "", "deleted": 0}
    item_id_match = VINTED_ITEM_ID_PATTERN.search(normalized_link)
    item_id = item_id_match.group(1) if item_id_match else ""
    path = Path(db_path).expanduser().resolve()
    ensure_vinted_database(path)
    try:
        with closing(sqlite3.connect(path)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            _create_schema(connection)
            detected_at = datetime.now().isoformat(timespec="seconds")
            clauses = ["item_link = ?"]
            values: list[str] = [normalized_link]
            if item_id:
                clauses.append("item_id = ?")
                values.append(item_id)
            identity_where = " OR ".join(clauses)

            item_record = connection.execute(
                f"SELECT * FROM vinted_items WHERE link = ? OR item_id = ? LIMIT 1",
                (normalized_link, item_id),
            ).fetchone() if item_id else connection.execute(
                "SELECT * FROM vinted_items WHERE link = ? LIMIT 1", (normalized_link,)
            ).fetchone()
            tracking_record = connection.execute(
                f"SELECT * FROM vinted_deal_sale_tracking WHERE {identity_where} LIMIT 1",
                tuple(values),
            ).fetchone()
            notification_record = connection.execute(
                f"SELECT * FROM vinted_deal_notifications WHERE {identity_where} ORDER BY sent_at DESC LIMIT 1",
                tuple(values),
            ).fetchone()

            snapshot: dict = {}
            if notification_record is not None:
                try:
                    snapshot.update(json.loads(str(notification_record["payload_json"] or "{}")))
                except (TypeError, ValueError):
                    pass
            if item_record is not None:
                snapshot.update({key: item_record[key] for key in item_record.keys()})
            if tracking_record is not None:
                try:
                    snapshot.update(json.loads(str(tracking_record["payload_json"] or "{}")))
                except (TypeError, ValueError):
                    pass
                snapshot.update(
                    {
                        "deal_first_seen_at": str(tracking_record["first_seen_at"] or ""),
                        "deal_last_checked_at": str(tracking_record["last_checked_at"] or ""),
                        "deal_sold_at": str(tracking_record["sold_at"] or ""),
                        "deal_status_before_unavailable": str(tracking_record["status"] or ""),
                    }
                )
            snapshot.update(
                {
                    "item_id": item_id or str(snapshot.get("item_id", "") or ""),
                    "link": normalized_link,
                    "unavailable_at": detected_at,
                    "unavailable_reason": "page_not_found",
                }
            )
            previous_status = str(tracking_record["status"] or "") if tracking_record is not None else ""
            first_seen_at = str(tracking_record["first_seen_at"] or "") if tracking_record is not None else ""
            sold_at = str(tracking_record["sold_at"] or "") if tracking_record is not None else ""
            connection.execute(
                """
                INSERT INTO vinted_unavailable_items(
                    item_link, item_id, detected_at, reason, previous_status,
                    first_seen_at, sold_at, image_url, local_image_path,
                    screenshot_path, html_path, snapshot_json
                )
                VALUES (?, ?, ?, 'page_not_found', ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(item_link) DO UPDATE SET
                    item_id = excluded.item_id,
                    detected_at = excluded.detected_at,
                    reason = excluded.reason,
                    previous_status = CASE
                        WHEN vinted_unavailable_items.previous_status <> '' THEN vinted_unavailable_items.previous_status
                        ELSE excluded.previous_status
                    END,
                    first_seen_at = CASE
                        WHEN vinted_unavailable_items.first_seen_at <> '' THEN vinted_unavailable_items.first_seen_at
                        ELSE excluded.first_seen_at
                    END,
                    sold_at = CASE
                        WHEN vinted_unavailable_items.sold_at <> '' THEN vinted_unavailable_items.sold_at
                        ELSE excluded.sold_at
                    END,
                    image_url = CASE
                        WHEN vinted_unavailable_items.image_url <> '' THEN vinted_unavailable_items.image_url
                        ELSE excluded.image_url
                    END,
                    local_image_path = CASE
                        WHEN vinted_unavailable_items.local_image_path <> '' THEN vinted_unavailable_items.local_image_path
                        ELSE excluded.local_image_path
                    END,
                    screenshot_path = CASE
                        WHEN vinted_unavailable_items.screenshot_path <> '' THEN vinted_unavailable_items.screenshot_path
                        ELSE excluded.screenshot_path
                    END,
                    html_path = CASE
                        WHEN vinted_unavailable_items.html_path <> '' THEN vinted_unavailable_items.html_path
                        ELSE excluded.html_path
                    END,
                    snapshot_json = excluded.snapshot_json
                """,
                (
                    normalized_link,
                    item_id,
                    detected_at,
                    previous_status,
                    first_seen_at,
                    sold_at,
                    str(snapshot.get("image_url", "") or ""),
                    str(snapshot.get("archive_image_path", "") or ""),
                    str(snapshot.get("archive_screenshot_path", "") or ""),
                    str(snapshot.get("archive_html_path", "") or ""),
                    json.dumps(snapshot, ensure_ascii=False),
                ),
            )
            if tracking_record is not None:
                historical_status = "sold" if previous_status == "sold" else "unavailable"
                connection.execute(
                    f"""
                    UPDATE vinted_deal_sale_tracking
                    SET status = ?, availability_status = 'unavailable', unavailable_at = ?,
                        unavailable_reason = 'page_not_found', payload_json = ?
                    WHERE {identity_where}
                    """,
                    (historical_status, detected_at, json.dumps(snapshot, ensure_ascii=False), *values),
                )
            connection.execute(
                """
                INSERT INTO vinted_item_events(
                    item_link, item_id, event_type, occurred_at, payload_json
                ) VALUES (?, ?, 'page_not_found', ?, ?)
                """,
                (normalized_link, item_id, detected_at, json.dumps(snapshot, ensure_ascii=False)),
            )
            connection.commit()
    except sqlite3.Error as exc:
        raise ValueError(f"Database Vinted non valido: {exc}") from exc
    return {
        "link": normalized_link,
        "item_id": item_id,
        "deleted": 0,
        "archived": 1,
        "previous_status": previous_status,
    }


def load_due_vinted_deal_sale_checks(
    db_path: str | Path = DEFAULT_VINTED_DB_PATH,
    *,
    due_before: str,
    limit: int = 40,
    first_seen_after: str = "",
) -> list[dict]:
    """Return active deals due for a check, optionally excluding older lifecycle records."""
    path = Path(db_path).expanduser().resolve()
    ensure_vinted_database(path)
    try:
        with closing(sqlite3.connect(path)) as connection:
            connection.row_factory = sqlite3.Row
            query = """
                SELECT item_link, item_id, item_name, first_seen_at, last_checked_at, payload_json
                FROM vinted_deal_sale_tracking
                WHERE status = 'active'
                  AND (last_checked_at = '' OR last_checked_at <= ?)
            """
            values: list[object] = [str(due_before or "")]
            if str(first_seen_after or "").strip():
                query += " AND first_seen_at >= ?"
                values.append(str(first_seen_after))
            query += " ORDER BY first_seen_at ASC LIMIT ?"
            values.append(max(int(limit or 0), 1))
            records = connection.execute(query, values).fetchall()
    except sqlite3.Error as exc:
        raise ValueError(f"Database Vinted non valido: {exc}") from exc
    rows: list[dict] = []
    for record in records:
        try:
            payload = json.loads(str(record["payload_json"] or "{}"))
        except (TypeError, ValueError):
            payload = {}
        row = payload if isinstance(payload, dict) else {}
        row.update({
            "link": str(record["item_link"] or row.get("link", "")),
            "item_id": str(record["item_id"] or row.get("item_id", "")),
            "name": str(record["item_name"] or row.get("name", "")),
            "deal_first_seen_at": str(record["first_seen_at"] or ""),
            "deal_last_checked_at": str(record["last_checked_at"] or ""),
        })
        rows.append(row)
    return rows


def save_vinted_deal_sale_check(
    row: dict,
    *,
    sold: bool,
    checked_at: str = "",
    db_path: str | Path = DEFAULT_VINTED_DB_PATH,
) -> dict:
    """Persist an availability check without treating an inaccessible page as sold."""
    path = Path(db_path).expanduser().resolve()
    ensure_vinted_database(path)
    link = str(row.get("link", "") or "").strip()
    if not link:
        raise ValueError("Manca il link dell'affare da aggiornare.")
    now = str(checked_at or datetime.now().isoformat(timespec="seconds"))
    with closing(sqlite3.connect(path)) as connection:
        _create_schema(connection)
        record = connection.execute(
            "SELECT first_seen_at, sold_at FROM vinted_deal_sale_tracking WHERE item_link = ?",
            (link,),
        ).fetchone()
        if record is None:
            _upsert_vinted_deal_sale_tracking(connection, row=row, first_seen_at=now)
            record = (now, "")
        first_seen_at = str(record[0] or now)
        sold_at = now if sold else str(record[1] or "")
        status = "sold" if sold else "active"
        connection.execute(
            """
            UPDATE vinted_deal_sale_tracking
            SET status = ?, last_checked_at = ?, sold_at = ?, payload_json = ?
            WHERE item_link = ?
            """,
            (status, now, sold_at, json.dumps(row, ensure_ascii=False), link),
        )
        connection.commit()
    return {"item_link": link, "first_seen_at": first_seen_at, "checked_at": now, "sold_at": sold_at, "status": status}


def load_vinted_deal_sales(
    db_path: str | Path = DEFAULT_VINTED_DB_PATH,
    *,
    since_at: str = "",
    limit: int = 250,
) -> list[dict]:
    """Historical deals confirmed by the explicit marketplace `Venduto` marker."""
    path = Path(db_path).expanduser().resolve()
    ensure_vinted_database(path)
    query = """
        SELECT item_link, item_id, item_name, first_seen_at, last_checked_at, sold_at, payload_json
        FROM vinted_deal_sale_tracking
        WHERE status = 'sold' AND sold_at <> ''
    """
    values: list[object] = []
    if str(since_at or "").strip():
        query += " AND sold_at >= ?"
        values.append(str(since_at))
    query += " ORDER BY sold_at DESC"
    if int(limit or 0) > 0:
        query += " LIMIT ?"
        values.append(int(limit))
    try:
        with closing(sqlite3.connect(path)) as connection:
            connection.row_factory = sqlite3.Row
            records = connection.execute(query, values).fetchall()
    except sqlite3.Error as exc:
        raise ValueError(f"Database Vinted non valido: {exc}") from exc
    rows: list[dict] = []
    for record in records:
        try:
            payload = json.loads(str(record["payload_json"] or "{}"))
        except (TypeError, ValueError):
            payload = {}
        row = payload if isinstance(payload, dict) else {}
        first_seen_at = str(record["first_seen_at"] or "")
        sold_at = str(record["sold_at"] or "")
        stored_seconds = row.get("deal_sale_duration_seconds")
        seconds = (
            max(int(float(stored_seconds)), 0)
            if isinstance(stored_seconds, (int, float))
            else _vinted_elapsed_seconds(first_seen_at, sold_at)
        )
        duration_source = str(row.get("deal_sale_duration_source", "") or "deal_discovery")
        row.update(
            {
                "link": str(record["item_link"] or row.get("link", "")),
                "item_id": str(record["item_id"] or row.get("item_id", "")),
                "name": str(record["item_name"] or row.get("name", "")),
                "deal_first_seen_at": first_seen_at,
                "deal_last_checked_at": str(record["last_checked_at"] or ""),
                "deal_sold_at": sold_at,
                "deal_sold_after_seconds": seconds,
                "deal_sold_after_text": format_vinted_elapsed_seconds(seconds),
                "deal_sale_duration_source": duration_source,
                "deal_listing_age_text": str(row.get("deal_listing_age_text", "") or ""),
            }
        )
        rows.append(row)
    return rows


def summarize_vinted_deal_sales(rows: list[dict]) -> dict[str, object]:
    elapsed = sorted(
        float(row["deal_sold_after_seconds"])
        for row in rows
        if isinstance(row.get("deal_sold_after_seconds"), (int, float))
    )
    average = sum(elapsed) / len(elapsed) if elapsed else None
    median = elapsed[len(elapsed) // 2] if elapsed else None
    return {
        "sold_count": len(rows),
        "average_seconds": average,
        "average_text": format_vinted_elapsed_seconds(average),
        "median_seconds": median,
        "median_text": format_vinted_elapsed_seconds(median),
    }


def format_vinted_elapsed_seconds(seconds: object) -> str:
    try:
        total = max(int(float(seconds)), 0)
    except (TypeError, ValueError):
        return ""
    days, remainder = divmod(total, 86_400)
    hours, remainder = divmod(remainder, 3_600)
    minutes = remainder // 60
    chunks: list[str] = []
    if days:
        chunks.append(f"{days}g")
    if hours or days:
        chunks.append(f"{hours}h")
    chunks.append(f"{minutes}m")
    return " ".join(chunks)


def parse_vinted_precise_listing_age_seconds(value: object) -> int | None:
    """Parse precise minute/hour ages; day-based labels are intentionally too coarse."""
    text = re.sub(r"\s+", " ", str(value or "").strip().lower())
    if not text or re.search(r"\b(?:giorn[oi]|day|days|jour|jours|dag|dagen)\b", text):
        return None
    text = re.sub(r"^(?:caricato|pubblicato|uploaded|listed)\s+", "", text)
    if re.search(r"\b(?:meno di un['’]?ora|less than an hour)\b", text):
        return 30 * 60
    if re.search(r"\b(?:un['’]?ora|one hour|une heure|een uur)\b", text):
        return 60 * 60
    minute_match = re.search(r"\b(\d{1,3})\s*(?:minut[oi]?|minutes?|mins?)\b", text)
    if minute_match:
        return min(int(minute_match.group(1)) * 60, 24 * 60 * 60)
    hour_match = re.search(r"\b(\d{1,2})\s*(?:or[ae]|hours?|heures?|uur)\b", text)
    if hour_match:
        hours = int(hour_match.group(1))
        return hours * 60 * 60 if hours <= 24 else None
    return None


def _vinted_elapsed_seconds(start_at: str, end_at: str) -> int | None:
    try:
        return max(int((datetime.fromisoformat(end_at) - datetime.fromisoformat(start_at)).total_seconds()), 0)
    except (TypeError, ValueError):
        return None


def _upsert_vinted_deal_sale_tracking(connection: sqlite3.Connection, *, row: dict, first_seen_at: str) -> None:
    link = str(row.get("link", "") or "").strip()
    if not link:
        return
    item_id = str(row.get("item_id", "") or "").strip() or extract_vinted_item_id_from_link(link)
    connection.execute(
        """
        INSERT INTO vinted_deal_sale_tracking (
            item_link, item_id, item_name, first_seen_at, last_checked_at, sold_at, status, payload_json
        ) VALUES (?, ?, ?, ?, '', '', 'active', ?)
        ON CONFLICT(item_link) DO UPDATE SET
            item_id = excluded.item_id,
            item_name = excluded.item_name,
            payload_json = excluded.payload_json
        """,
        (link, item_id, str(row.get("name", "") or ""), str(first_seen_at or datetime.now().isoformat(timespec="seconds")), json.dumps(row, ensure_ascii=False)),
    )


def annotate_rows_with_vinted_offer_history(
    rows: list[dict],
    db_path: str | Path = DEFAULT_VINTED_DB_PATH,
) -> list[dict]:
    path = Path(db_path).expanduser().resolve()
    ensure_vinted_database(path)
    try:
        with closing(sqlite3.connect(path)) as connection:
            connection.row_factory = sqlite3.Row
            records = connection.execute(
                """
                SELECT item_id, item_link, item_name, submitted_at, offer_value, offer_input_value, offer_discount_percent
                FROM vinted_offer_history
                ORDER BY submitted_at DESC
                """
            ).fetchall()
    except sqlite3.Error as exc:
        raise ValueError(f"Database Vinted non valido: {exc}") from exc

    history_map: dict[str, dict[str, object]] = {}
    for record in records:
        entry = {
            "offer_last_submitted_at": str(record["submitted_at"] or ""),
            "offer_last_value": record["offer_value"],
            "offer_last_input_value": str(record["offer_input_value"] or ""),
            "offer_last_discount_percent": record["offer_discount_percent"],
            "offer_last_item_name": str(record["item_name"] or ""),
        }
        for key in build_vinted_item_identity_keys(item_id=record["item_id"], link=record["item_link"]):
            history_map.setdefault(key, entry)

    annotated_rows: list[dict] = []
    for row in rows:
        annotated = dict(row)
        history_entry: dict[str, object] | None = None
        for key in build_vinted_item_identity_keys(
            item_id=annotated.get("item_id", ""),
            link=annotated.get("link", ""),
        ):
            history_entry = history_map.get(key)
            if history_entry is not None:
                break
        annotated["offer_already_submitted"] = history_entry is not None
        annotated["offer_last_submitted_at"] = str((history_entry or {}).get("offer_last_submitted_at", "") or "")
        annotated["offer_last_value"] = (history_entry or {}).get("offer_last_value")
        annotated["offer_last_input_value"] = str((history_entry or {}).get("offer_last_input_value", "") or "")
        annotated["offer_last_discount_percent"] = (history_entry or {}).get("offer_last_discount_percent")
        annotated_rows.append(annotated)
    return annotated_rows


def save_vinted_offer_results(
    results: list[dict],
    db_path: str | Path = DEFAULT_VINTED_DB_PATH,
) -> dict[str, int | str]:
    path = Path(db_path).expanduser().resolve()
    ensure_vinted_database(path)
    submitted_results = [
        result
        for result in results
        if isinstance(result, dict)
        and bool(result.get("submitted"))
        and str(result.get("link", "") or "").strip()
    ]
    if not submitted_results:
        return {
            "db_path": str(path),
            "offer_history_saved_count": 0,
            "new_offer_history_entries": 0,
            "updated_offer_history_entries": 0,
        }

    new_entries = 0
    updated_entries = 0
    with closing(sqlite3.connect(path)) as connection:
        _create_schema(connection)
        for result in submitted_results:
            link = str(result.get("link", "") or "").strip()
            item_id = str(result.get("item_id", "") or "").strip() or extract_vinted_item_id_from_link(link)
            submitted_at = str(result.get("submitted_at", "") or "").strip() or datetime.now().isoformat(timespec="seconds")
            already_exists = connection.execute(
                "SELECT 1 FROM vinted_offer_history WHERE item_link = ?",
                (link,),
            ).fetchone() is not None
            connection.execute(
                """
                INSERT INTO vinted_offer_history (
                    item_link, item_id, item_name, submitted_at, offer_value,
                    offer_input_value, offer_discount_percent, source_price_value,
                    current_url, result_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(item_link) DO UPDATE SET
                    item_id = excluded.item_id,
                    item_name = excluded.item_name,
                    submitted_at = excluded.submitted_at,
                    offer_value = excluded.offer_value,
                    offer_input_value = excluded.offer_input_value,
                    offer_discount_percent = excluded.offer_discount_percent,
                    source_price_value = excluded.source_price_value,
                    current_url = excluded.current_url,
                    result_json = excluded.result_json
                """,
                (
                    link,
                    item_id,
                    str(result.get("item_name", "") or ""),
                    submitted_at,
                    result.get("offer_value"),
                    str(result.get("offer_input_value", "") or ""),
                    result.get("offer_discount_percent"),
                    result.get("source_price"),
                    str(result.get("current_url", "") or ""),
                    json.dumps(result, ensure_ascii=False),
                ),
            )
            if already_exists:
                updated_entries += 1
            else:
                new_entries += 1
        connection.commit()

    return {
        "db_path": str(path),
        "offer_history_saved_count": len(submitted_results),
        "new_offer_history_entries": new_entries,
        "updated_offer_history_entries": updated_entries,
    }


def save_vinted_rows(
    rows: list[dict],
    db_path: str | Path = DEFAULT_VINTED_DB_PATH,
    run_kind: str = "search",
) -> dict[str, int | str]:
    path = Path(db_path).expanduser().resolve()
    ensure_vinted_database(path)
    counts = {
        "new_items": 0,
        "updated_items": 0,
        "new_search_hits": 0,
        "updated_search_hits": 0,
    }
    normalized_run_kind = str(run_kind or "search").strip() or "search"
    valid_rows = [row for row in rows if str(row.get("link", "") or "").strip()]
    run_created_at = datetime.now().isoformat(timespec="seconds")
    run_key = ""
    run_label = ""
    query_label = ""
    run_id: int | None = None

    competitor_seller_ids: set[str] = set()

    with closing(sqlite3.connect(path)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        _create_schema(connection)
        if valid_rows:
            run_key = _build_vinted_search_run_key()
            query_label = _derive_vinted_run_query_label(valid_rows)
            primary_search_term = _derive_vinted_run_primary_search_term(valid_rows)
            primary_search_url = _derive_vinted_run_primary_search_url(valid_rows)
            cursor = connection.execute(
                """
                INSERT INTO vinted_search_runs (
                    run_key, run_kind, title, notes, query_label, search_term, search_url, created_at, item_count
                ) VALUES (?, ?, '', '', ?, ?, ?, ?, ?)
                """,
                (
                    run_key,
                    normalized_run_kind,
                    query_label,
                    primary_search_term,
                    primary_search_url,
                    run_created_at,
                    len(valid_rows),
                ),
            )
            run_id = int(cursor.lastrowid)
            run_label = _format_vinted_search_run_label(
                created_at=run_created_at,
                title="",
                query_label=query_label,
                item_count=len(valid_rows),
                run_id=run_id,
            )

        for row_index, row in enumerate(valid_rows, start=1):
            link = str(row.get("link", "") or "").strip()
            search_term = str(row.get("search_term", "") or "").strip()
            tag = str(row.get("tag", "") or "").strip()
            observed_at = str(row.get("extracted_at", "") or datetime.now().isoformat(timespec="seconds"))
            seller_member_id = str(row.get("seller_member_id", "") or "").strip()
            if seller_member_id:
                competitor_seller_ids.add(seller_member_id)

            item_exists = connection.execute(
                "SELECT 1 FROM vinted_items WHERE link = ?",
                (link,),
            ).fetchone() is not None
            connection.execute(
                """
                INSERT INTO vinted_items (
                    link, item_id, name, description, published_at, price_text, price_value,
                    shipping_price_text, shipping_price_value, total_price_text, total_price_value,
                    offer_available, offer_text, favorite_count, seller_name, seller_profile_url,
                    seller_member_id, detail_category_path, detail_catalog_ids, evaluation_label, currency,
                    raw_text, first_seen_at, last_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(link) DO UPDATE SET
                    item_id = excluded.item_id,
                    name = excluded.name,
                    description = CASE
                        WHEN excluded.description IS NOT NULL AND excluded.description <> ''
                            THEN excluded.description
                        ELSE vinted_items.description
                    END,
                    published_at = CASE
                        WHEN excluded.published_at IS NOT NULL AND excluded.published_at <> ''
                            THEN excluded.published_at
                        ELSE vinted_items.published_at
                    END,
                    price_text = excluded.price_text,
                    price_value = excluded.price_value,
                    shipping_price_text = CASE
                        WHEN excluded.shipping_price_text IS NOT NULL AND excluded.shipping_price_text <> ''
                            THEN excluded.shipping_price_text
                        ELSE vinted_items.shipping_price_text
                    END,
                    shipping_price_value = COALESCE(excluded.shipping_price_value, vinted_items.shipping_price_value),
                    total_price_text = CASE
                        WHEN excluded.total_price_text IS NOT NULL AND excluded.total_price_text <> ''
                            THEN excluded.total_price_text
                        ELSE vinted_items.total_price_text
                    END,
                    total_price_value = COALESCE(excluded.total_price_value, vinted_items.total_price_value),
                    offer_available = excluded.offer_available,
                    offer_text = CASE
                        WHEN excluded.offer_text IS NOT NULL AND excluded.offer_text <> ''
                            THEN excluded.offer_text
                        ELSE vinted_items.offer_text
                    END,
                    favorite_count = COALESCE(excluded.favorite_count, vinted_items.favorite_count),
                    seller_name = CASE
                        WHEN excluded.seller_name IS NOT NULL AND excluded.seller_name <> ''
                            THEN excluded.seller_name
                        ELSE vinted_items.seller_name
                    END,
                    seller_profile_url = CASE
                        WHEN excluded.seller_profile_url IS NOT NULL AND excluded.seller_profile_url <> ''
                            THEN excluded.seller_profile_url
                        ELSE vinted_items.seller_profile_url
                    END,
                    seller_member_id = CASE
                        WHEN excluded.seller_member_id IS NOT NULL AND excluded.seller_member_id <> ''
                            THEN excluded.seller_member_id
                        ELSE vinted_items.seller_member_id
                    END,
                    detail_category_path = CASE
                        WHEN excluded.detail_category_path IS NOT NULL AND excluded.detail_category_path <> ''
                            THEN excluded.detail_category_path
                        ELSE vinted_items.detail_category_path
                    END,
                    detail_catalog_ids = CASE
                        WHEN excluded.detail_catalog_ids IS NOT NULL AND excluded.detail_catalog_ids <> ''
                            THEN excluded.detail_catalog_ids
                        ELSE vinted_items.detail_catalog_ids
                    END,
                    evaluation_label = CASE
                        WHEN excluded.evaluation_label IS NOT NULL AND excluded.evaluation_label <> ''
                            THEN excluded.evaluation_label
                        ELSE vinted_items.evaluation_label
                    END,
                    currency = excluded.currency,
                    raw_text = excluded.raw_text,
                    last_seen_at = excluded.last_seen_at
                """,
                (
                    link,
                    str(row.get("item_id", "") or ""),
                    str(row.get("name", "") or ""),
                    str(row.get("description", "") or ""),
                    str(row.get("published_at", "") or ""),
                    str(row.get("price", "") or ""),
                    row.get("price_value"),
                    str(row.get("shipping_price", "") or ""),
                    row.get("shipping_price_value"),
                    str(row.get("total_price", "") or ""),
                    row.get("total_price_value"),
                    1 if row.get("offer_available") else 0,
                    str(row.get("offer_text", "") or ""),
                    row.get("favorite_count"),
                    str(row.get("seller_name", "") or ""),
                    str(row.get("seller_profile_url", "") or ""),
                    seller_member_id,
                    str(row.get("detail_category_path", "") or ""),
                    json.dumps(row.get("detail_catalog_ids", []) or [], ensure_ascii=False),
                    str(row.get("evaluation_label", "") or ""),
                    str(row.get("currency", "") or ""),
                    str(row.get("raw_text", "") or ""),
                    observed_at,
                    observed_at,
                ),
            )
            counts["updated_items" if item_exists else "new_items"] += 1

            hit_exists = connection.execute(
                "SELECT 1 FROM vinted_search_hits WHERE item_link = ? AND search_term = ?",
                (link, search_term),
            ).fetchone() is not None
            connection.execute(
                """
                INSERT INTO vinted_search_hits (
                    item_link, search_term, tag, search_url, first_seen_at, last_seen_at, times_seen
                ) VALUES (?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(item_link, search_term) DO UPDATE SET
                    tag = excluded.tag,
                    search_url = excluded.search_url,
                    last_seen_at = excluded.last_seen_at,
                    times_seen = vinted_search_hits.times_seen + 1
                """,
                (
                    link,
                    search_term,
                    tag,
                    str(row.get("search_url", "") or ""),
                    observed_at,
                    observed_at,
                ),
            )
            counts["updated_search_hits" if hit_exists else "new_search_hits"] += 1

            if run_id is not None:
                snapshot_row = dict(row)
                snapshot_row["db_path"] = str(path)
                snapshot_row["db_saved"] = True
                snapshot_row["search_run_key"] = run_key
                snapshot_row["search_run_label"] = run_label
                snapshot_row["search_run_created_at"] = run_created_at
                snapshot_row["search_run_kind"] = normalized_run_kind
                snapshot_row.setdefault("source", "vinted")
                if not str(snapshot_row.get("extracted_at", "") or "").strip():
                    snapshot_row["extracted_at"] = observed_at
                connection.execute(
                    """
                    INSERT INTO vinted_search_run_rows (
                        run_id, row_index, item_link, snapshot_json
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        row_index,
                        link,
                        json.dumps(snapshot_row, ensure_ascii=False),
                    ),
                )

        total_search_runs = int(
            connection.execute(
                "SELECT COUNT(*) FROM vinted_search_runs WHERE run_kind = 'search'"
            ).fetchone()[0]
        )
        connection.commit()

    competitor_meta = update_vinted_competitors_for_sellers(
        competitor_seller_ids,
        db_path=path,
    ) if competitor_seller_ids else {
        "competitor_sellers_updated": 0,
        "competitor_status_counts": {},
    }

    return {
        "db_path": str(path),
        "db_total_search_runs": total_search_runs,
        "search_run_key": run_key,
        "search_run_label": run_label,
        "search_run_query": query_label,
        "search_run_kind": normalized_run_kind,
        "search_run_item_count": len(valid_rows),
        **competitor_meta,
        **counts,
    }


def update_vinted_competitors_for_sellers(
    seller_member_ids: set[str] | list[str] | tuple[str, ...],
    db_path: str | Path = DEFAULT_VINTED_DB_PATH,
) -> dict[str, object]:
    normalized_ids = sorted({str(item or "").strip() for item in seller_member_ids if str(item or "").strip()})
    path = Path(db_path).expanduser().resolve()
    ensure_vinted_database(path)
    if not normalized_ids:
        return {
            "db_path": str(path),
            "competitor_sellers_updated": 0,
            "competitor_status_counts": {},
        }

    status_counts: dict[str, int] = {}
    updated = 0
    now = datetime.now().isoformat(timespec="seconds")
    with closing(sqlite3.connect(path)) as connection:
        connection.row_factory = sqlite3.Row
        _create_schema(connection)
        for seller_id in normalized_ids:
            stats = connection.execute(
                """
                SELECT
                    i.seller_member_id,
                    MAX(i.seller_name) AS seller_name,
                    MAX(i.seller_profile_url) AS seller_profile_url,
                    MIN(i.first_seen_at) AS first_seen_at,
                    MAX(i.last_seen_at) AS last_seen_at,
                    COUNT(DISTINCT i.link) AS interesting_item_count,
                    COUNT(DISTINCT NULLIF(h.search_term, '')) AS distinct_search_terms,
                    COALESCE(MAX(i.favorite_count), 0) AS max_favorite_count,
                    COALESCE(AVG(i.favorite_count), 0) AS avg_favorite_count
                FROM vinted_items i
                LEFT JOIN vinted_search_hits h ON h.item_link = i.link
                WHERE i.seller_member_id = ?
                GROUP BY i.seller_member_id
                """,
                (seller_id,),
            ).fetchone()
            if stats is None:
                continue
            gone_count = int(
                connection.execute(
                    """
                    SELECT COUNT(DISTINCT psi.item_link)
                    FROM vinted_profile_snapshot_items psi
                    JOIN vinted_items i ON i.link = psi.item_link
                    WHERE i.seller_member_id = ?
                      AND LOWER(psi.status) IN ('gone', 'sold', 'removed')
                    """,
                    (seller_id,),
                ).fetchone()[0]
                or 0
            )
            top_deal_keyword_row = connection.execute(
                """
                SELECT
                    LOWER(TRIM(n.search_term)) AS normalized_search_term,
                    COUNT(DISTINCT n.item_link) AS deal_count
                FROM vinted_deal_notifications n
                JOIN vinted_items i ON i.link = n.item_link
                WHERE i.seller_member_id = ?
                  AND TRIM(n.search_term) <> ''
                GROUP BY LOWER(TRIM(n.search_term))
                ORDER BY deal_count DESC, normalized_search_term ASC
                LIMIT 1
                """,
                (seller_id,),
            ).fetchone()
            top_deal_keyword = (
                str(top_deal_keyword_row["normalized_search_term"] or "")
                if top_deal_keyword_row is not None
                else ""
            )
            same_keyword_deal_count = (
                int(top_deal_keyword_row["deal_count"] or 0)
                if top_deal_keyword_row is not None
                else 0
            )
            interesting_count = int(stats["interesting_item_count"] or 0)
            distinct_terms = int(stats["distinct_search_terms"] or 0)
            max_favorites = int(stats["max_favorite_count"] or 0)
            avg_favorites = float(stats["avg_favorite_count"] or 0.0)
            status = classify_vinted_competitor_status(
                interesting_item_count=interesting_count,
                distinct_search_terms=distinct_terms,
                gone_item_count=gone_count,
                max_favorite_count=max_favorites,
                same_keyword_deal_count=same_keyword_deal_count,
            )
            if not status:
                continue
            score = (
                interesting_count * 10
                + distinct_terms * 15
                + gone_count * 35
                + same_keyword_deal_count * 20
                + min(max_favorites, 100)
                + min(int(avg_favorites), 100)
            )
            payload = {
                "seller_member_id": seller_id,
                "seller_name": str(stats["seller_name"] or ""),
                "seller_profile_url": str(stats["seller_profile_url"] or ""),
                "interesting_item_count": interesting_count,
                "distinct_search_terms": distinct_terms,
                "gone_item_count": gone_count,
                "top_deal_keyword": top_deal_keyword,
                "same_keyword_deal_count": same_keyword_deal_count,
                "max_favorite_count": max_favorites,
                "avg_favorite_count": round(avg_favorites, 2),
                "status": status,
                "score": score,
                "updated_at": now,
            }
            connection.execute(
                """
                INSERT INTO vinted_competitors (
                    seller_member_id, seller_name, seller_profile_url, first_seen_at, last_seen_at,
                    interesting_item_count, distinct_search_terms, gone_item_count,
                    top_deal_keyword, same_keyword_deal_count, max_favorite_count,
                    avg_favorite_count, status, score, updated_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(seller_member_id) DO UPDATE SET
                    seller_name = CASE
                        WHEN excluded.seller_name IS NOT NULL AND excluded.seller_name <> ''
                            THEN excluded.seller_name
                        ELSE vinted_competitors.seller_name
                    END,
                    seller_profile_url = CASE
                        WHEN excluded.seller_profile_url IS NOT NULL AND excluded.seller_profile_url <> ''
                            THEN excluded.seller_profile_url
                        ELSE vinted_competitors.seller_profile_url
                    END,
                    first_seen_at = CASE
                        WHEN vinted_competitors.first_seen_at IS NULL OR vinted_competitors.first_seen_at = ''
                            THEN excluded.first_seen_at
                        WHEN excluded.first_seen_at < vinted_competitors.first_seen_at
                            THEN excluded.first_seen_at
                        ELSE vinted_competitors.first_seen_at
                    END,
                    last_seen_at = excluded.last_seen_at,
                    interesting_item_count = excluded.interesting_item_count,
                    distinct_search_terms = excluded.distinct_search_terms,
                    gone_item_count = excluded.gone_item_count,
                    top_deal_keyword = excluded.top_deal_keyword,
                    same_keyword_deal_count = excluded.same_keyword_deal_count,
                    max_favorite_count = excluded.max_favorite_count,
                    avg_favorite_count = excluded.avg_favorite_count,
                    status = CASE
                        WHEN vinted_competitors.manual_priority = 1 THEN 'priority'
                        ELSE excluded.status
                    END,
                    score = excluded.score,
                    updated_at = excluded.updated_at,
                    payload_json = excluded.payload_json
                """,
                (
                    seller_id,
                    str(stats["seller_name"] or ""),
                    str(stats["seller_profile_url"] or ""),
                    str(stats["first_seen_at"] or now),
                    str(stats["last_seen_at"] or now),
                    interesting_count,
                    distinct_terms,
                    gone_count,
                    top_deal_keyword,
                    same_keyword_deal_count,
                    max_favorites,
                    avg_favorites,
                    status,
                    score,
                    now,
                    json.dumps(payload, ensure_ascii=False),
                ),
            )
            status_counts[status] = status_counts.get(status, 0) + 1
            updated += 1
        connection.commit()

    return {
        "db_path": str(path),
        "competitor_sellers_updated": updated,
        "competitor_status_counts": status_counts,
    }


def load_vinted_rows(
    db_path: str | Path = DEFAULT_VINTED_DB_PATH,
    search_term: str = "",
    tag_filter: str = "",
    limit: int = 500,
    search_run_key: str = "",
) -> tuple[list[dict], dict[str, int | str | bool]]:
    path = Path(db_path).expanduser().resolve()
    database_created = not path.exists()
    ensure_vinted_database(path)

    normalized_limit = max(int(limit), 0)
    filter_value = str(search_term or "").strip()
    tag_value = str(tag_filter or "").strip()
    run_key_value = str(search_run_key or "").strip()
    conditions: list[str] = []
    base_parameters: list[object] = []
    if filter_value:
        conditions.append("h.search_term LIKE ?")
        base_parameters.append(f"%{filter_value}%")
    if tag_value:
        conditions.append("h.tag = ?")
        base_parameters.append(tag_value)
    where_sql = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    parameters = list(base_parameters)
    limit_sql = "LIMIT ?" if normalized_limit > 0 else ""
    if normalized_limit > 0:
        parameters.append(normalized_limit)

    try:
        with closing(sqlite3.connect(path)) as connection:
            connection.row_factory = sqlite3.Row
            total_items = int(connection.execute("SELECT COUNT(*) FROM vinted_items").fetchone()[0])
            total_hits = int(connection.execute("SELECT COUNT(*) FROM vinted_search_hits").fetchone()[0])
            total_runs = int(
                connection.execute(
                    "SELECT COUNT(*) FROM vinted_search_runs WHERE run_kind = 'search'"
                ).fetchone()[0]
            )
            if run_key_value:
                run_record = connection.execute(
                    """
                    SELECT id, run_key, run_kind, title, notes, query_label, search_term, search_url, created_at, item_count
                    FROM vinted_search_runs
                    WHERE run_key = ?
                    """,
                    (run_key_value,),
                ).fetchone()
                if run_record is None:
                    raise ValueError("Ricerca salvata non trovata nel database Vinted.")
                snapshot_records = connection.execute(
                    """
                    SELECT row_index, snapshot_json
                    FROM vinted_search_run_rows
                    WHERE run_id = ?
                    ORDER BY row_index ASC
                    """,
                    (run_record["id"],),
                ).fetchall()
                run_rows = [_deserialize_vinted_run_row(record["snapshot_json"], path) for record in snapshot_records]
                run_label = _format_vinted_search_run_label(
                    created_at=str(run_record["created_at"]),
                    title=str(run_record["title"] or ""),
                    query_label=str(run_record["query_label"] or ""),
                    item_count=int(run_record["item_count"] or 0),
                    run_id=int(run_record["id"]),
                )
                for row in run_rows:
                    row["search_run_key"] = str(run_record["run_key"])
                    row["search_run_label"] = run_label
                    row["search_run_created_at"] = str(run_record["created_at"] or "")
                    row["search_run_kind"] = str(run_record["run_kind"] or "")
                    row["search_run_title"] = str(run_record["title"] or "")
                    row["search_run_notes"] = str(run_record["notes"] or "")
                if filter_value:
                    run_rows = [
                        row for row in run_rows if filter_value.lower() in str(row.get("search_term", "") or "").lower()
                    ]
                if tag_value:
                    run_rows = [
                        row for row in run_rows if str(row.get("tag", "") or "").strip().lower() == tag_value.lower()
                    ]
                filtered_hits = len(run_rows)
                if normalized_limit > 0:
                    run_rows = run_rows[:normalized_limit]
                return run_rows, {
                    "db_path": str(path),
                    "loaded_from_db": True,
                    "db_created": database_created,
                    "db_search_filter": filter_value,
                    "db_tag_filter": tag_value,
                    "db_limit": normalized_limit,
                    "db_total_items": total_items,
                    "db_total_search_hits": total_hits,
                    "db_total_search_runs": total_runs,
                    "db_filtered_search_hits": filtered_hits,
                    "db_search_run_key": str(run_record["run_key"]),
                    "db_search_run_label": run_label,
                    "db_search_run_created_at": str(run_record["created_at"]),
                    "db_search_run_title": str(run_record["title"] or ""),
                    "db_search_run_notes": str(run_record["notes"] or ""),
                    "db_search_run_query": str(run_record["query_label"] or ""),
                    "db_search_run_kind": str(run_record["run_kind"] or ""),
                    "search_term": str(run_record["search_term"] or ""),
                    "search_url": str(run_record["search_url"] or ""),
                    "row_count": len(run_rows),
                }
            filtered_hits = int(
                connection.execute(
                    f"SELECT COUNT(*) FROM vinted_search_hits h {where_sql}",
                    base_parameters,
                ).fetchone()[0]
            )
            records = connection.execute(
                f"""
                SELECT
                    h.search_term,
                    h.tag,
                    h.search_url,
                    h.first_seen_at,
                    h.last_seen_at,
                    h.times_seen,
                    i.link,
                    i.item_id,
                    i.name,
                    i.description,
                    i.published_at,
                    i.price_text,
                    i.price_value,
                    i.shipping_price_text,
                    i.shipping_price_value,
                    i.total_price_text,
                    i.total_price_value,
                    i.offer_available,
                    i.offer_text,
                    i.favorite_count,
                    i.seller_name,
                    i.seller_profile_url,
                    i.seller_member_id,
                    i.evaluation_label,
                    i.currency,
                    i.raw_text
                FROM vinted_search_hits h
                JOIN vinted_items i ON i.link = h.item_link
                {where_sql}
                ORDER BY h.last_seen_at DESC, i.name COLLATE NOCASE
                {limit_sql}
                """,
                parameters,
            ).fetchall()
    except sqlite3.Error as exc:
        raise ValueError(f"Database Vinted non valido: {exc}") from exc

    rows = [
        {
            "source": "vinted",
            "search_term": record["search_term"],
            "tag": record["tag"] or "",
            "search_url": record["search_url"],
            "item_id": record["item_id"],
            "name": record["name"],
            "description": record["description"],
            "published_at": record["published_at"],
            "price": record["price_text"],
            "price_value": record["price_value"],
            "shipping_price": record["shipping_price_text"],
            "shipping_price_value": record["shipping_price_value"],
            "shipping_alert": "sped > 2,99"
            if record["shipping_price_value"] not in ("", None) and float(record["shipping_price_value"]) > 2.99
            else "",
            "total_price": record["total_price_text"],
            "total_price_value": record["total_price_value"],
            "offer_available": bool(record["offer_available"]),
            "offer_text": record["offer_text"],
            "favorite_count": record["favorite_count"],
            "seller_name": record["seller_name"],
            "seller_profile_url": record["seller_profile_url"],
            "seller_member_id": record["seller_member_id"],
            "evaluation_label": record["evaluation_label"] or "",
            "currency": record["currency"],
            "link": record["link"],
            "raw_text": record["raw_text"],
            "first_seen_at": record["first_seen_at"],
            "last_seen_at": record["last_seen_at"],
            "times_seen": record["times_seen"],
            "extracted_at": record["last_seen_at"],
            "db_path": str(path),
            "db_saved": True,
        }
        for record in records
    ]
    rows = annotate_rows_with_vinted_offer_history(rows, db_path=path)
    return rows, {
        "db_path": str(path),
        "loaded_from_db": True,
        "db_created": database_created,
        "db_search_filter": filter_value,
        "db_tag_filter": tag_value,
        "db_limit": normalized_limit,
        "db_total_items": total_items,
        "db_total_search_hits": total_hits,
        "db_total_search_runs": total_runs,
        "db_filtered_search_hits": filtered_hits,
        "row_count": len(rows),
    }


def load_vinted_item_image_refs(
    rows: list[dict],
    db_path: str | Path = DEFAULT_VINTED_DB_PATH,
) -> dict[str, dict[str, str]]:
    """Recover the newest archived/image URL fields for selected listing rows."""
    links = list(
        dict.fromkeys(
            str(row.get("link", "") or "").strip()
            for row in rows
            if str(row.get("link", "") or "").strip()
        )
    )
    path = Path(db_path).expanduser().resolve()
    if not links or not path.is_file():
        return {}
    refs: dict[str, dict[str, str]] = {}
    try:
        with closing(sqlite3.connect(path)) as connection:
            for offset in range(0, len(links), 400):
                chunk = links[offset : offset + 400]
                placeholders = ",".join("?" for _ in chunk)
                records = connection.execute(
                    f"""
                    SELECT item_link, snapshot_json
                    FROM vinted_search_run_rows
                    WHERE item_link IN ({placeholders})
                    ORDER BY id DESC
                    """,
                    chunk,
                ).fetchall()
                for link, raw_json in records:
                    if link in refs and refs[link].get("image_url") and refs[link].get("archive_image_path"):
                        continue
                    try:
                        payload = json.loads(raw_json)
                    except (TypeError, json.JSONDecodeError):
                        continue
                    if not isinstance(payload, dict):
                        continue
                    current = refs.setdefault(str(link), {})
                    for key in ("image_url", "archive_image_path", "local_image_path"):
                        value = str(payload.get(key, "") or "").strip()
                        if value and not current.get(key):
                            current[key] = value
    except sqlite3.Error:
        return {}
    return refs


def list_vinted_search_runs(
    db_path: str | Path = DEFAULT_VINTED_DB_PATH,
    limit: int = 100,
    run_kind: str = "search",
    text_filter: str = "",
) -> list[dict[str, int | str]]:
    path = Path(db_path).expanduser().resolve()
    ensure_vinted_database(path)
    normalized_limit = max(int(limit), 0)
    normalized_run_kind = str(run_kind or "").strip()
    filter_value = str(text_filter or "").strip().lower()
    conditions: list[str] = []
    parameters: list[object] = []
    if normalized_run_kind:
        conditions.append("run_kind = ?")
        parameters.append(normalized_run_kind)
    if filter_value:
        like_value = f"%{filter_value}%"
        conditions.append(
            "(LOWER(title) LIKE ? OR LOWER(notes) LIKE ? OR LOWER(query_label) LIKE ? OR LOWER(search_term) LIKE ? OR LOWER(created_at) LIKE ?)"
        )
        parameters.extend([like_value, like_value, like_value, like_value, like_value])
    where_sql = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    limit_sql = "LIMIT ?" if normalized_limit > 0 else ""
    if normalized_limit > 0:
        parameters.append(normalized_limit)
    try:
        with closing(sqlite3.connect(path)) as connection:
            connection.row_factory = sqlite3.Row
            records = connection.execute(
                f"""
                SELECT id, run_key, run_kind, title, notes, query_label, search_term, search_url, created_at, item_count
                FROM vinted_search_runs
                {where_sql}
                ORDER BY created_at DESC, id DESC
                {limit_sql}
                """,
                parameters,
            ).fetchall()
    except sqlite3.Error as exc:
        raise ValueError(f"Database Vinted non valido: {exc}") from exc

    return [
        {
            "id": int(record["id"]),
            "run_key": str(record["run_key"]),
            "run_kind": str(record["run_kind"] or ""),
            "title": str(record["title"] or ""),
            "notes": str(record["notes"] or ""),
            "query_label": str(record["query_label"] or ""),
            "search_term": str(record["search_term"] or ""),
            "search_url": str(record["search_url"] or ""),
            "created_at": str(record["created_at"] or ""),
            "item_count": int(record["item_count"] or 0),
            "label": _format_vinted_search_run_label(
                created_at=str(record["created_at"] or ""),
                title=str(record["title"] or ""),
                query_label=str(record["query_label"] or ""),
                item_count=int(record["item_count"] or 0),
                run_id=int(record["id"]),
            ),
        }
        for record in records
    ]


def update_vinted_search_run(
    run_key: str,
    db_path: str | Path = DEFAULT_VINTED_DB_PATH,
    title: str | None = None,
    notes: str | None = None,
) -> dict[str, int | str]:
    path = Path(db_path).expanduser().resolve()
    ensure_vinted_database(path)
    normalized_run_key = str(run_key or "").strip()
    if not normalized_run_key:
        raise ValueError("Chiave ricerca salvata mancante.")
    assignments: list[str] = []
    parameters: list[object] = []
    if title is not None:
        assignments.append("title = ?")
        parameters.append(str(title or "").strip())
    if notes is not None:
        assignments.append("notes = ?")
        parameters.append(str(notes or "").strip())
    if not assignments:
        raise ValueError("Nessun aggiornamento richiesto per la ricerca salvata.")
    parameters.append(normalized_run_key)
    try:
        with closing(sqlite3.connect(path)) as connection:
            _create_schema(connection)
            cursor = connection.execute(
                f"UPDATE vinted_search_runs SET {', '.join(assignments)} WHERE run_key = ?",
                parameters,
            )
            if cursor.rowcount <= 0:
                raise ValueError("Ricerca salvata non trovata nel database Vinted.")
            connection.commit()
            record = connection.execute(
                """
                SELECT id, run_key, run_kind, title, notes, query_label, search_term, search_url, created_at, item_count
                FROM vinted_search_runs
                WHERE run_key = ?
                """,
                (normalized_run_key,),
            ).fetchone()
    except sqlite3.Error as exc:
        raise ValueError(f"Database Vinted non valido: {exc}") from exc
    if record is None:
        raise ValueError("Ricerca salvata non trovata nel database Vinted.")
    return {
        "id": int(record[0]),
        "run_key": str(record[1]),
        "run_kind": str(record[2] or ""),
        "title": str(record[3] or ""),
        "notes": str(record[4] or ""),
        "query_label": str(record[5] or ""),
        "search_term": str(record[6] or ""),
        "search_url": str(record[7] or ""),
        "created_at": str(record[8] or ""),
        "item_count": int(record[9] or 0),
    }


def delete_vinted_search_run(
    run_key: str,
    db_path: str | Path = DEFAULT_VINTED_DB_PATH,
) -> dict[str, int | str]:
    path = Path(db_path).expanduser().resolve()
    ensure_vinted_database(path)
    normalized_run_key = str(run_key or "").strip()
    if not normalized_run_key:
        raise ValueError("Chiave ricerca salvata mancante.")
    try:
        with closing(sqlite3.connect(path)) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            _create_schema(connection)
            record = connection.execute(
                "SELECT id, run_kind FROM vinted_search_runs WHERE run_key = ?",
                (normalized_run_key,),
            ).fetchone()
            if record is None:
                raise ValueError("Ricerca salvata non trovata nel database Vinted.")
            connection.execute(
                "DELETE FROM vinted_search_runs WHERE run_key = ?",
                (normalized_run_key,),
            )
            remaining_runs = int(
                connection.execute(
                    "SELECT COUNT(*) FROM vinted_search_runs WHERE run_kind = 'search'"
                ).fetchone()[0]
            )
            connection.commit()
    except sqlite3.Error as exc:
        raise ValueError(f"Database Vinted non valido: {exc}") from exc
    return {
        "run_key": normalized_run_key,
        "run_kind": str(record[1] or ""),
        "db_total_search_runs": remaining_runs,
    }


def save_vinted_profile_snapshot(
    profile_url: str,
    rows: list[dict],
    db_path: str | Path = DEFAULT_VINTED_DB_PATH,
) -> dict[str, object]:
    path = Path(db_path).expanduser().resolve()
    ensure_vinted_database(path)
    normalized_profile_url = str(profile_url or "").strip()
    member_id = normalize_vinted_member_id(normalized_profile_url)
    snapshot_at = datetime.now().isoformat(timespec="seconds")
    normalized_rows: list[dict] = []
    seen_links: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        link = str(row.get("link", "") or "").strip()
        if not link or link in seen_links:
            continue
        seen_links.add(link)
        item_id = str(row.get("item_id", "") or "").strip() or extract_vinted_item_id_from_link(link)
        normalized = dict(row)
        normalized["link"] = link
        normalized["item_id"] = item_id
        normalized["profile_url"] = normalized_profile_url
        normalized["member_id"] = member_id
        normalized_rows.append(normalized)
    sold_links = {
        str(row.get("link", "") or "")
        for row in normalized_rows
        if str(row.get("profile_item_status", "") or "").lower() == "sold"
    }
    unavailable_links = {
        str(row.get("link", "") or "")
        for row in normalized_rows
        if str(row.get("profile_item_status", "") or "").lower() == "unavailable"
    }
    current_links = {
        str(row.get("link", "") or "")
        for row in normalized_rows
        if str(row.get("profile_item_status", "") or "").lower() not in {"sold", "unavailable", "gone"}
    }
    active_links = {
        str(row.get("link", "") or "")
        for row in normalized_rows
        if str(row.get("profile_item_status", "") or "").lower() not in {"sold", "unavailable", "unknown", "gone"}
    }
    current_links.discard("")
    sold_links.discard("")
    unavailable_links.discard("")

    try:
        with closing(sqlite3.connect(path)) as connection:
            connection.row_factory = sqlite3.Row
            _create_schema(connection)
            previous_snapshot = connection.execute(
                """
                SELECT id, item_count
                FROM vinted_profile_snapshots
                WHERE member_id = ? AND profile_url = ? AND item_count > 0
                ORDER BY checked_at DESC, id DESC
                LIMIT 1
                """,
                (member_id, normalized_profile_url),
            ).fetchone()
            previous_links: set[str] = set()
            if previous_snapshot is not None:
                previous_links = {
                    str(record[0] or "")
                    for record in connection.execute(
                        """
                        SELECT item_link
                        FROM vinted_profile_snapshot_items
                        WHERE snapshot_id = ? AND present = 1
                        """,
                        (int(previous_snapshot["id"]),),
                    ).fetchall()
                    if str(record[0] or "")
                }

            previous_count = len(previous_links)
            current_count = len(current_links)
            confirmed_terminal_links = previous_links & (sold_links | unavailable_links)
            # Count missing IDs, not just the difference in total inventory
            # sizes. A bad page/API response can return 330 unrelated IDs while
            # still reporting 330 items, which must not be saved as 330 gone / 330 new.
            missing_count = len(previous_links - current_links - confirmed_terminal_links)
            missing_ratio = (missing_count / previous_count) if previous_count else 0.0
            # A blank grid/API response is never proof that an established
            # seller removed their entire inventory. This can happen during a
            # transient platform response, rate limit, category filter, or
            # incomplete browser load. Preserve the prior snapshot instead.
            empty_after_nonempty = previous_count > 0 and current_count == 0
            incomplete_snapshot = bool(
                empty_after_nonempty
                or (
                    previous_count >= 12
                    and current_count > 0
                    and missing_count >= max(8, int(previous_count * 0.25))
                    and missing_ratio >= 0.35
                )
            )
            if incomplete_snapshot:
                preserved_rows: list[dict] = []
                if previous_snapshot is not None:
                    previous_records = connection.execute(
                        """
                        SELECT item_link, item_id, item_name, price_text, image_url, status, snapshot_json
                        FROM vinted_profile_snapshot_items
                        WHERE snapshot_id = ? AND present = 1
                        ORDER BY id
                        """,
                        (int(previous_snapshot["id"]),),
                    ).fetchall()
                    for record in previous_records:
                        try:
                            payload = json.loads(str(record["snapshot_json"] or "{}"))
                        except (TypeError, json.JSONDecodeError):
                            payload = {}
                        row = dict(payload) if isinstance(payload, dict) else {}
                        row.update(
                            {
                                "source": "vinted_profile",
                                "profile_url": normalized_profile_url,
                                "member_id": member_id,
                                "link": str(record["item_link"] or ""),
                                "item_id": str(record["item_id"] or extract_vinted_item_id_from_link(record["item_link"])),
                                "name": str(record["item_name"] or row.get("name", "")),
                                "price": str(record["price_text"] or row.get("price", "")),
                                "image_url": str(record["image_url"] or row.get("image_url", "")),
                                "profile_item_status": "still_active",
                            }
                        )
                        preserved_rows.append(row)
                history_by_link = _load_vinted_profile_item_history(
                    connection,
                    member_id=member_id,
                    profile_url=normalized_profile_url,
                    links=previous_links,
                    sold_at=snapshot_at,
                )
                for row in preserved_rows:
                    link = str(row.get("link", "") or "")
                    history = history_by_link.get(link, {})
                    row["profile_first_seen_at"] = str(history.get("first_seen_at", "") or snapshot_at)
                    row["profile_last_seen_at"] = str(history.get("last_seen_at", "") or row.get("profile_last_seen_at", "") or snapshot_at)
                return {
                    "db_path": str(path),
                    "profile_snapshot_id": "",
                    "profile_checked_at": snapshot_at,
                    "profile_member_id": member_id,
                    "profile_item_count": len(preserved_rows) if previous_snapshot is not None else len(active_links),
                    "profile_new_count": 0,
                    "profile_gone_count": 0,
                    "profile_sold_count": sum(1 for row in normalized_rows if row.get("profile_item_status") == "sold"),
                    "profile_still_count": len(preserved_rows) if previous_snapshot is not None else len(current_links & previous_links),
                    "profile_current_rows": preserved_rows if previous_snapshot is not None else normalized_rows,
                    "profile_gone_rows": [],
                    "profile_snapshot_incomplete": True,
                    "profile_snapshot_saved": False,
                    "profile_previous_item_count": previous_count,
                    "profile_missing_item_count": missing_count,
                    "profile_missing_ratio": round(missing_ratio, 4),
                    "profile_empty_response_guard": empty_after_nonempty,
                    "db_saved_live": False,
                }

            new_links = current_links - previous_links
            explicit_gone_links = {
                str(row.get("link", "") or "")
                for row in normalized_rows
                if str(row.get("profile_item_status", "") or "").lower() == "gone"
            }
            gone_links = (previous_links - current_links - sold_links - unavailable_links) | explicit_gone_links
            still_links = current_links & previous_links
            history_by_link = _load_vinted_profile_item_history(
                connection,
                member_id=member_id,
                profile_url=normalized_profile_url,
                links=current_links | gone_links | sold_links | unavailable_links,
                sold_at=snapshot_at,
            )

            snapshot = connection.execute(
                """
                INSERT INTO vinted_profile_snapshots (
                    member_id, profile_url, checked_at, item_count,
                    new_count, gone_count, still_count
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    member_id,
                    normalized_profile_url,
                    snapshot_at,
                    len(current_links),
                    len(new_links),
                    len(gone_links),
                    len(still_links),
                ),
            )
            snapshot_id = int(snapshot.lastrowid)

            current_by_link = {str(row.get("link", "") or ""): row for row in normalized_rows}
            all_links = sorted(current_links | gone_links | sold_links | unavailable_links)
            for link in all_links:
                row = current_by_link.get(link, {})
                if link in sold_links:
                    status = "sold"
                elif link in unavailable_links:
                    status = "unavailable"
                else:
                    detected_status = str(row.get("profile_item_status", "") or "").lower()
                    if detected_status == "unknown":
                        status = "unknown"
                    else:
                        status = "new" if link in new_links else "gone" if link in gone_links else "still_active"
                history = history_by_link.get(link, {})
                if status in {"gone", "sold", "unavailable"}:
                    row = {
                        **row,
                        "source": "vinted_profile",
                        "profile_url": normalized_profile_url,
                        "member_id": member_id,
                        "link": link,
                        "item_id": extract_vinted_item_id_from_link(link),
                        "name": str(history.get("item_name", "") or ""),
                        "price": str(history.get("price_text", "") or ""),
                        "image_url": str(history.get("image_url", "") or ""),
                        "profile_item_status": status,
                        "profile_first_seen_at": str(history.get("first_seen_at", "") or ""),
                        "profile_last_seen_at": str(history.get("last_seen_at", "") or ""),
                        "profile_sold_after_seconds": row.get("profile_sold_after_seconds", history.get("sold_after_seconds")),
                        "profile_sold_after_text": str(row.get("profile_sold_after_text", "") or history.get("sold_after_text", "") or ""),
                        "profile_sold_at": str(row.get("profile_sold_at", "") or (snapshot_at if status == "sold" else "")),
                        "extracted_at": snapshot_at,
                    }
                connection.execute(
                    """
                    INSERT INTO vinted_profile_snapshot_items (
                        snapshot_id, member_id, profile_url, item_link, item_id,
                        item_name, price_text, image_url, status, present, snapshot_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        snapshot_id,
                        member_id,
                        normalized_profile_url,
                        link,
                        str(row.get("item_id", "") or extract_vinted_item_id_from_link(link)),
                        str(row.get("name", "") or ""),
                        str(row.get("price", "") or ""),
                        str(row.get("image_url", "") or ""),
                        status,
                        0 if status in {"gone", "sold", "unavailable"} else 1,
                        json.dumps(row, ensure_ascii=False),
                    ),
                )
            connection.commit()
    except sqlite3.Error as exc:
        raise ValueError(f"Database Vinted non valido: {exc}") from exc

    for row in normalized_rows:
        link = str(row.get("link", "") or "")
        history = history_by_link.get(link, {})
        detected_status = str(row.get("profile_item_status", "") or "").lower()
        if detected_status in {"sold", "unavailable", "unknown", "gone"}:
            continue
        row["profile_item_status"] = "new" if link in new_links else "still_active"
        row["profile_first_seen_at"] = str(history.get("first_seen_at", "") or snapshot_at)
        row["profile_last_seen_at"] = snapshot_at
    gone_rows = [
        {
            "source": "vinted_profile",
            "profile_url": normalized_profile_url,
            "member_id": member_id,
            "link": link,
            "item_id": extract_vinted_item_id_from_link(link),
            "name": str(history_by_link.get(link, {}).get("item_name", "") or ""),
            "price": str(history_by_link.get(link, {}).get("price_text", "") or ""),
            "image_url": str(history_by_link.get(link, {}).get("image_url", "") or ""),
            "profile_item_status": "gone",
            "profile_first_seen_at": str(history_by_link.get(link, {}).get("first_seen_at", "") or ""),
            "profile_last_seen_at": str(history_by_link.get(link, {}).get("last_seen_at", "") or ""),
            "profile_sold_after_seconds": history_by_link.get(link, {}).get("sold_after_seconds"),
            "profile_sold_after_text": str(history_by_link.get(link, {}).get("sold_after_text", "") or ""),
            "extracted_at": snapshot_at,
        }
        for link in sorted(gone_links)
    ]
    return {
        "db_path": str(path),
        "profile_snapshot_id": snapshot_id,
        "profile_checked_at": snapshot_at,
        "profile_member_id": member_id,
        "profile_item_count": len(active_links),
        "profile_new_count": len(new_links),
        "profile_gone_count": len(gone_links),
        "profile_sold_count": len(sold_links),
        "profile_still_count": len(still_links),
        "profile_current_rows": normalized_rows,
        "profile_gone_rows": gone_rows,
        "db_saved_live": True,
    }


def load_vinted_profile_known_items(
    profile_url: str,
    db_path: str | Path = DEFAULT_VINTED_DB_PATH,
) -> list[dict]:
    """Return one newest saved record for every listing ever observed on a profile.

    This is used only by an explicit, manual sold-state verification.  A
    profile grid cannot tell whether an item missing from the current listing
    was sold or merely removed, while its own item page can.
    """
    path = Path(db_path).expanduser().resolve()
    normalized_profile_url = str(profile_url or "").strip()
    member_id = normalize_vinted_member_id(normalized_profile_url)
    if not member_id or not path.is_file():
        return []
    try:
        with closing(sqlite3.connect(path)) as connection:
            connection.row_factory = sqlite3.Row
            _create_schema(connection)
            records = connection.execute(
                """
                SELECT psi.*, s.checked_at
                FROM vinted_profile_snapshot_items psi
                JOIN vinted_profile_snapshots s ON s.id = psi.snapshot_id
                WHERE psi.member_id = ? AND psi.profile_url = ? AND s.item_count > 0
                ORDER BY s.checked_at DESC, psi.id DESC
                """,
                (member_id, normalized_profile_url),
            ).fetchall()
    except sqlite3.Error as exc:
        raise ValueError(f"Database Vinted non valido: {exc}") from exc

    rows_by_link: dict[str, dict] = {}
    for record in records:
        link = str(record["item_link"] or "").strip()
        if not link or link in rows_by_link:
            continue
        try:
            payload = json.loads(str(record["snapshot_json"] or "{}"))
        except (TypeError, json.JSONDecodeError):
            payload = {}
        row = dict(payload) if isinstance(payload, dict) else {}
        row.update(
            {
                "source": "vinted_profile",
                "profile_url": normalized_profile_url,
                "member_id": member_id,
                "link": link,
                "item_id": str(record["item_id"] or row.get("item_id", "") or extract_vinted_item_id_from_link(link)),
                "name": str(record["item_name"] or row.get("name", "")),
                "price": str(record["price_text"] or row.get("price", "")),
                "image_url": str(record["image_url"] or row.get("image_url", "")),
                "profile_last_seen_at": str(row.get("profile_last_seen_at", "") or record["checked_at"] or ""),
                "profile_item_status": str(row.get("profile_item_status", "") or record["status"] or ""),
            }
        )
        rows_by_link[link] = row
    return list(rows_by_link.values())


def load_vinted_profile_latest_snapshot_items(
    profile_url: str,
    db_path: str | Path = DEFAULT_VINTED_DB_PATH,
) -> list[dict]:
    """Return the inventory from the latest non-empty profile snapshot.

    Profile comparisons must use the last complete inventory, not every item
    ever observed. Historical rows remain available through
    ``load_vinted_profile_known_items`` for unresolved gone-item follow-up.
    """
    path = Path(db_path).expanduser().resolve()
    normalized_profile_url = str(profile_url or "").strip()
    member_id = normalize_vinted_member_id(normalized_profile_url)
    if not member_id or not path.is_file():
        return []
    try:
        with closing(sqlite3.connect(path)) as connection:
            connection.row_factory = sqlite3.Row
            _create_schema(connection)
            snapshots = connection.execute(
                """
                SELECT id, checked_at, item_count, new_count, gone_count
                FROM vinted_profile_snapshots
                WHERE member_id = ? AND profile_url = ? AND item_count > 0
                ORDER BY checked_at DESC, id DESC
                """,
                (member_id, normalized_profile_url),
            ).fetchall()
            snapshot = next(
                (
                    record
                    for record in snapshots
                    if not (
                        int(record["item_count"] or 0) >= 12
                        and int(record["new_count"] or 0) >= max(8, int(int(record["item_count"]) * 0.25))
                        and int(record["gone_count"] or 0) >= max(8, int(int(record["item_count"]) * 0.25))
                    )
                ),
                None,
            )
            if snapshot is None:
                return []
            records = connection.execute(
                """
                SELECT * FROM vinted_profile_snapshot_items
                WHERE snapshot_id = ? AND present = 1
                ORDER BY id ASC
                """,
                (int(snapshot["id"]),),
            ).fetchall()
    except sqlite3.Error as exc:
        raise ValueError(f"Database Vinted non valido: {exc}") from exc

    rows: list[dict] = []
    for record in records:
        link = str(record["item_link"] or "").strip()
        if not link:
            continue
        try:
            payload = json.loads(str(record["snapshot_json"] or "{}"))
        except (TypeError, json.JSONDecodeError):
            payload = {}
        row = dict(payload) if isinstance(payload, dict) else {}
        row.update(
            {
                "source": "vinted_profile",
                "profile_url": normalized_profile_url,
                "member_id": member_id,
                "link": link,
                "item_id": str(record["item_id"] or row.get("item_id", "") or extract_vinted_item_id_from_link(link)),
                "name": str(record["item_name"] or row.get("name", "")),
                "price": str(record["price_text"] or row.get("price", "")),
                "image_url": str(record["image_url"] or row.get("image_url", "")),
                "profile_last_seen_at": str(row.get("profile_last_seen_at", "") or snapshot["checked_at"] or ""),
                "profile_item_status": str(row.get("profile_item_status", "") or record["status"] or ""),
            }
        )
        rows.append(row)
    return rows


def load_vinted_profile_daily_changes(
    *,
    db_path: str | Path = DEFAULT_VINTED_DB_PATH,
    since_at: str,
    until_at: str,
    profile_urls: list[str] | None = None,
    limit: int = 80,
) -> list[dict]:
    path = Path(db_path).expanduser().resolve()
    ensure_vinted_database(path)
    profile_member_ids = {
        normalize_vinted_member_id(profile_url)
        for profile_url in (profile_urls or [])
        if normalize_vinted_member_id(profile_url)
    }
    query = """
        SELECT
            psi.member_id,
            psi.profile_url,
            psi.item_link,
            psi.item_id,
            psi.item_name,
            psi.price_text,
            psi.image_url,
            psi.status,
            psi.snapshot_json,
            MIN(s.checked_at) AS first_change_at,
            MAX(s.checked_at) AS last_change_at
        FROM vinted_profile_snapshot_items psi
        JOIN vinted_profile_snapshots s ON s.id = psi.snapshot_id
        WHERE s.checked_at >= ?
          AND s.checked_at <= ?
          AND s.item_count > 0
          AND LOWER(psi.status) IN ('new', 'gone', 'sold')
    """
    params: list[object] = [since_at, until_at]
    if profile_member_ids:
        placeholders = ",".join("?" for _ in profile_member_ids)
        query += f" AND psi.member_id IN ({placeholders})"
        params.extend(sorted(profile_member_ids))
    query += """
        GROUP BY psi.member_id, psi.profile_url, psi.item_link, LOWER(psi.status)
        ORDER BY last_change_at DESC, first_change_at DESC
        LIMIT ?
    """
    params.append(max(int(limit or 0), 1))
    with closing(sqlite3.connect(path)) as connection:
        connection.row_factory = sqlite3.Row
        _create_schema(connection)
        records = connection.execute(query, params).fetchall()
    rows: list[dict] = []
    for record in records:
        snapshot_payload: dict = {}
        raw_json = str(record["snapshot_json"] or "")
        if raw_json:
            try:
                loaded = json.loads(raw_json)
                if isinstance(loaded, dict):
                    snapshot_payload = loaded
            except json.JSONDecodeError:
                snapshot_payload = {}
        rows.append(
            {
                "source": "vinted_profile_daily",
                "member_id": str(record["member_id"] or ""),
                "profile_url": str(record["profile_url"] or ""),
                "link": str(record["item_link"] or ""),
                "item_id": str(record["item_id"] or ""),
                "name": str(snapshot_payload.get("name", "") or record["item_name"] or ""),
                "price": str(snapshot_payload.get("price", "") or record["price_text"] or ""),
                "image_url": str(snapshot_payload.get("image_url", "") or record["image_url"] or ""),
                "profile_item_status": str(record["status"] or ""),
                "profile_first_seen_at": str(snapshot_payload.get("profile_first_seen_at", "") or ""),
                "profile_last_seen_at": str(snapshot_payload.get("profile_last_seen_at", "") or ""),
                "profile_sold_after_text": str(snapshot_payload.get("profile_sold_after_text", "") or ""),
                "profile_sold_at": str(snapshot_payload.get("profile_sold_at", "") or ""),
                "first_change_at": str(record["first_change_at"] or ""),
                "last_change_at": str(record["last_change_at"] or ""),
            }
        )
    return rows


def _load_vinted_profile_item_history(
    connection: sqlite3.Connection,
    *,
    member_id: str,
    profile_url: str,
    links: set[str],
    sold_at: str,
) -> dict[str, dict[str, object]]:
    history_by_link: dict[str, dict[str, object]] = {}
    for link in links:
        records = connection.execute(
            """
            SELECT
                psi.item_name,
                psi.price_text,
                psi.image_url,
                s.checked_at
            FROM vinted_profile_snapshot_items psi
            JOIN vinted_profile_snapshots s ON s.id = psi.snapshot_id
            WHERE psi.member_id = ?
              AND psi.profile_url = ?
              AND psi.item_link = ?
              AND psi.present = 1
            ORDER BY s.checked_at ASC, s.id ASC
            """,
            (member_id, profile_url, link),
        ).fetchall()
        if not records:
            continue
        first_seen_at = str(records[0]["checked_at"] or "")
        last_record = records[-1]
        last_seen_at = str(last_record["checked_at"] or "")
        sold_after_seconds = _seconds_between_iso(first_seen_at, sold_at)
        history_by_link[link] = {
            "item_name": str(last_record["item_name"] or ""),
            "price_text": str(last_record["price_text"] or ""),
            "image_url": str(last_record["image_url"] or ""),
            "first_seen_at": first_seen_at,
            "last_seen_at": last_seen_at,
            "sold_after_seconds": sold_after_seconds,
            "sold_after_text": _format_duration_seconds(sold_after_seconds),
        }
    return history_by_link


def _seconds_between_iso(start: str, end: str) -> int | None:
    try:
        start_dt = datetime.fromisoformat(str(start or ""))
        end_dt = datetime.fromisoformat(str(end or ""))
    except ValueError:
        return None
    return max(int((end_dt - start_dt).total_seconds()), 0)


def _format_duration_seconds(seconds: object) -> str:
    if seconds in (None, ""):
        return ""
    try:
        total_seconds = max(int(seconds), 0)
    except (TypeError, ValueError):
        return ""
    days, remainder = divmod(total_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, _seconds = divmod(remainder, 60)
    if days:
        return f"{days}g {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m"
    return "meno di 1m"


def _create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS vinted_items (
            link TEXT PRIMARY KEY,
            item_id TEXT NOT NULL DEFAULT '',
            name TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL DEFAULT '',
            published_at TEXT NOT NULL DEFAULT '',
            price_text TEXT NOT NULL DEFAULT '',
            price_value REAL,
            shipping_price_text TEXT NOT NULL DEFAULT '',
            shipping_price_value REAL,
            total_price_text TEXT NOT NULL DEFAULT '',
            total_price_value REAL,
            offer_available INTEGER NOT NULL DEFAULT 0,
            offer_text TEXT NOT NULL DEFAULT '',
            favorite_count INTEGER,
            seller_name TEXT NOT NULL DEFAULT '',
            seller_profile_url TEXT NOT NULL DEFAULT '',
            seller_member_id TEXT NOT NULL DEFAULT '',
            detail_category_path TEXT NOT NULL DEFAULT '',
            detail_catalog_ids TEXT NOT NULL DEFAULT '',
            evaluation_label TEXT NOT NULL DEFAULT '',
            currency TEXT NOT NULL DEFAULT '',
            raw_text TEXT NOT NULL DEFAULT '',
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS vinted_unavailable_items (
            item_link TEXT PRIMARY KEY,
            item_id TEXT NOT NULL DEFAULT '',
            detected_at TEXT NOT NULL,
            reason TEXT NOT NULL DEFAULT 'page_not_found',
            previous_status TEXT NOT NULL DEFAULT '',
            first_seen_at TEXT NOT NULL DEFAULT '',
            sold_at TEXT NOT NULL DEFAULT '',
            image_url TEXT NOT NULL DEFAULT '',
            local_image_path TEXT NOT NULL DEFAULT '',
            screenshot_path TEXT NOT NULL DEFAULT '',
            html_path TEXT NOT NULL DEFAULT '',
            snapshot_json TEXT NOT NULL DEFAULT ''
        );

        CREATE INDEX IF NOT EXISTS idx_vinted_unavailable_items_item_id
        ON vinted_unavailable_items(item_id);

        CREATE TABLE IF NOT EXISTS vinted_search_hits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_link TEXT NOT NULL,
            search_term TEXT NOT NULL,
            tag TEXT NOT NULL DEFAULT 'ricercato',
            search_url TEXT NOT NULL DEFAULT '',
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            times_seen INTEGER NOT NULL DEFAULT 1,
            UNIQUE(item_link, search_term),
            FOREIGN KEY(item_link) REFERENCES vinted_items(link) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_vinted_search_hits_term
        ON vinted_search_hits(search_term);

        CREATE INDEX IF NOT EXISTS idx_vinted_items_last_seen
        ON vinted_items(last_seen_at);

        CREATE TABLE IF NOT EXISTS vinted_search_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_key TEXT NOT NULL UNIQUE,
            run_kind TEXT NOT NULL DEFAULT 'search',
            title TEXT NOT NULL DEFAULT '',
            notes TEXT NOT NULL DEFAULT '',
            query_label TEXT NOT NULL DEFAULT '',
            search_term TEXT NOT NULL DEFAULT '',
            search_url TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            item_count INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS vinted_search_run_rows (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            row_index INTEGER NOT NULL,
            item_link TEXT NOT NULL,
            snapshot_json TEXT NOT NULL,
            UNIQUE(run_id, row_index),
            FOREIGN KEY(run_id) REFERENCES vinted_search_runs(id) ON DELETE CASCADE,
            FOREIGN KEY(item_link) REFERENCES vinted_items(link) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_vinted_search_runs_created_at
        ON vinted_search_runs(created_at DESC);

        CREATE INDEX IF NOT EXISTS idx_vinted_search_run_rows_run
        ON vinted_search_run_rows(run_id, row_index);

        CREATE TABLE IF NOT EXISTS vinted_offer_history (
            item_link TEXT PRIMARY KEY,
            item_id TEXT NOT NULL DEFAULT '',
            item_name TEXT NOT NULL DEFAULT '',
            submitted_at TEXT NOT NULL,
            offer_value REAL,
            offer_input_value TEXT NOT NULL DEFAULT '',
            offer_discount_percent REAL,
            source_price_value REAL,
            current_url TEXT NOT NULL DEFAULT '',
            result_json TEXT NOT NULL DEFAULT ''
        );

        CREATE INDEX IF NOT EXISTS idx_vinted_offer_history_submitted_at
        ON vinted_offer_history(submitted_at DESC);

        CREATE INDEX IF NOT EXISTS idx_vinted_offer_history_item_id
        ON vinted_offer_history(item_id);

        CREATE TABLE IF NOT EXISTS vinted_deal_notifications (
            item_link TEXT NOT NULL,
            webhook_target TEXT NOT NULL DEFAULT '',
            item_id TEXT NOT NULL DEFAULT '',
            item_name TEXT NOT NULL DEFAULT '',
            search_term TEXT NOT NULL DEFAULT '',
            sent_at TEXT NOT NULL,
            payload_json TEXT NOT NULL DEFAULT '',
            PRIMARY KEY(item_link, webhook_target)
        );

        CREATE INDEX IF NOT EXISTS idx_vinted_deal_notifications_sent_at
        ON vinted_deal_notifications(sent_at DESC);

        CREATE INDEX IF NOT EXISTS idx_vinted_deal_notifications_item_id
        ON vinted_deal_notifications(item_id);

        CREATE TABLE IF NOT EXISTS vinted_deal_sale_tracking (
            item_link TEXT PRIMARY KEY,
            item_id TEXT NOT NULL DEFAULT '',
            item_name TEXT NOT NULL DEFAULT '',
            first_seen_at TEXT NOT NULL,
            last_checked_at TEXT NOT NULL DEFAULT '',
            sold_at TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'active',
            availability_status TEXT NOT NULL DEFAULT 'available',
            unavailable_at TEXT NOT NULL DEFAULT '',
            unavailable_reason TEXT NOT NULL DEFAULT '',
            payload_json TEXT NOT NULL DEFAULT ''
        );

        CREATE INDEX IF NOT EXISTS idx_vinted_deal_sale_tracking_due
        ON vinted_deal_sale_tracking(status, last_checked_at, first_seen_at);

        CREATE TABLE IF NOT EXISTS vinted_item_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_link TEXT NOT NULL,
            item_id TEXT NOT NULL DEFAULT '',
            event_type TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            payload_json TEXT NOT NULL DEFAULT ''
        );

        CREATE INDEX IF NOT EXISTS idx_vinted_item_events_item
        ON vinted_item_events(item_id, item_link, occurred_at DESC);

        CREATE INDEX IF NOT EXISTS idx_vinted_item_events_type_time
        ON vinted_item_events(event_type, occurred_at DESC);

        CREATE TABLE IF NOT EXISTS vinted_profile_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            member_id TEXT NOT NULL DEFAULT '',
            profile_url TEXT NOT NULL DEFAULT '',
            checked_at TEXT NOT NULL,
            item_count INTEGER NOT NULL DEFAULT 0,
            new_count INTEGER NOT NULL DEFAULT 0,
            gone_count INTEGER NOT NULL DEFAULT 0,
            still_count INTEGER NOT NULL DEFAULT 0
        );

        CREATE INDEX IF NOT EXISTS idx_vinted_profile_snapshots_profile
        ON vinted_profile_snapshots(member_id, profile_url, checked_at DESC);

        CREATE TABLE IF NOT EXISTS vinted_profile_snapshot_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_id INTEGER NOT NULL,
            member_id TEXT NOT NULL DEFAULT '',
            profile_url TEXT NOT NULL DEFAULT '',
            item_link TEXT NOT NULL DEFAULT '',
            item_id TEXT NOT NULL DEFAULT '',
            item_name TEXT NOT NULL DEFAULT '',
            price_text TEXT NOT NULL DEFAULT '',
            image_url TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT '',
            present INTEGER NOT NULL DEFAULT 1,
            snapshot_json TEXT NOT NULL DEFAULT '',
            UNIQUE(snapshot_id, item_link),
            FOREIGN KEY(snapshot_id) REFERENCES vinted_profile_snapshots(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_vinted_profile_snapshot_items_profile
        ON vinted_profile_snapshot_items(member_id, profile_url, item_link);

        CREATE INDEX IF NOT EXISTS idx_vinted_profile_snapshot_items_status
        ON vinted_profile_snapshot_items(snapshot_id, status);

        CREATE TABLE IF NOT EXISTS vinted_owned_profiles (
            member_id TEXT PRIMARY KEY,
            profile_url TEXT NOT NULL DEFAULT '',
            display_name TEXT NOT NULL DEFAULT '',
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS vinted_competitors (
            seller_member_id TEXT PRIMARY KEY,
            seller_name TEXT NOT NULL DEFAULT '',
            seller_profile_url TEXT NOT NULL DEFAULT '',
            first_seen_at TEXT NOT NULL DEFAULT '',
            last_seen_at TEXT NOT NULL DEFAULT '',
            interesting_item_count INTEGER NOT NULL DEFAULT 0,
            distinct_search_terms INTEGER NOT NULL DEFAULT 0,
            gone_item_count INTEGER NOT NULL DEFAULT 0,
            top_deal_keyword TEXT NOT NULL DEFAULT '',
            same_keyword_deal_count INTEGER NOT NULL DEFAULT 0,
            max_favorite_count INTEGER NOT NULL DEFAULT 0,
            avg_favorite_count REAL NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'candidate',
            manual_priority INTEGER NOT NULL DEFAULT 0,
            score REAL NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL DEFAULT '',
            payload_json TEXT NOT NULL DEFAULT ''
        );

        CREATE INDEX IF NOT EXISTS idx_vinted_competitors_status_score
        ON vinted_competitors(status, score DESC);
        """
    )
    _ensure_vinted_search_hits_tag_column(connection)
    _ensure_vinted_items_description_column(connection)
    _ensure_vinted_items_published_at_column(connection)
    _ensure_vinted_items_offer_columns(connection)
    _ensure_vinted_items_favorite_columns(connection)
    _ensure_vinted_items_seller_columns(connection)
    _ensure_vinted_items_detail_category_columns(connection)
    _ensure_vinted_competitors_columns(connection)
    _ensure_vinted_search_runs_columns(connection)
    _ensure_vinted_offer_history_columns(connection)
    _ensure_vinted_history_columns(connection)
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_vinted_search_hits_tag ON vinted_search_hits(tag)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_vinted_items_seller_member_id ON vinted_items(seller_member_id)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_vinted_competitors_status_score ON vinted_competitors(status, score DESC)"
    )


def _ensure_vinted_search_hits_tag_column(connection: sqlite3.Connection) -> None:
    columns = {
        str(row[1])
        for row in connection.execute("PRAGMA table_info(vinted_search_hits)").fetchall()
    }
    if "tag" not in columns:
        connection.execute(
            "ALTER TABLE vinted_search_hits ADD COLUMN tag TEXT NOT NULL DEFAULT 'ricercato'"
        )


def _ensure_vinted_items_description_column(connection: sqlite3.Connection) -> None:
    columns = {
        str(row[1])
        for row in connection.execute("PRAGMA table_info(vinted_items)").fetchall()
    }
    if "description" not in columns:
        connection.execute(
            "ALTER TABLE vinted_items ADD COLUMN description TEXT NOT NULL DEFAULT ''"
        )


def _ensure_vinted_items_published_at_column(connection: sqlite3.Connection) -> None:
    columns = {
        str(row[1])
        for row in connection.execute("PRAGMA table_info(vinted_items)").fetchall()
    }
    if "published_at" not in columns:
        connection.execute(
            "ALTER TABLE vinted_items ADD COLUMN published_at TEXT NOT NULL DEFAULT ''"
        )


def _ensure_vinted_items_offer_columns(connection: sqlite3.Connection) -> None:
    columns = {
        str(row[1])
        for row in connection.execute("PRAGMA table_info(vinted_items)").fetchall()
    }
    additions = (
        ("shipping_price_text", "TEXT NOT NULL DEFAULT ''"),
        ("shipping_price_value", "REAL"),
        ("total_price_text", "TEXT NOT NULL DEFAULT ''"),
        ("total_price_value", "REAL"),
        ("offer_available", "INTEGER NOT NULL DEFAULT 0"),
        ("offer_text", "TEXT NOT NULL DEFAULT ''"),
    )
    for name, sql_type in additions:
        if name in columns:
            continue
        connection.execute(f"ALTER TABLE vinted_items ADD COLUMN {name} {sql_type}")


def _ensure_vinted_items_favorite_columns(connection: sqlite3.Connection) -> None:
    columns = {
        str(row[1])
        for row in connection.execute("PRAGMA table_info(vinted_items)").fetchall()
    }
    additions = (
        ("favorite_count", "INTEGER"),
        ("evaluation_label", "TEXT NOT NULL DEFAULT ''"),
    )
    for name, sql_type in additions:
        if name in columns:
            continue
        connection.execute(f"ALTER TABLE vinted_items ADD COLUMN {name} {sql_type}")


def _ensure_vinted_items_seller_columns(connection: sqlite3.Connection) -> None:
    columns = {
        str(row[1])
        for row in connection.execute("PRAGMA table_info(vinted_items)").fetchall()
    }
    additions = (
        ("seller_name", "TEXT NOT NULL DEFAULT ''"),
        ("seller_profile_url", "TEXT NOT NULL DEFAULT ''"),
        ("seller_member_id", "TEXT NOT NULL DEFAULT ''"),
    )
    for name, sql_type in additions:
        if name in columns:
            continue
        connection.execute(f"ALTER TABLE vinted_items ADD COLUMN {name} {sql_type}")


def _ensure_vinted_items_detail_category_columns(connection: sqlite3.Connection) -> None:
    columns = {
        str(row[1])
        for row in connection.execute("PRAGMA table_info(vinted_items)").fetchall()
    }
    additions = (
        ("detail_category_path", "TEXT NOT NULL DEFAULT ''"),
        ("detail_catalog_ids", "TEXT NOT NULL DEFAULT ''"),
    )
    for name, sql_type in additions:
        if name not in columns:
            connection.execute(f"ALTER TABLE vinted_items ADD COLUMN {name} {sql_type}")


def _ensure_vinted_competitors_columns(connection: sqlite3.Connection) -> None:
    columns = {
        str(row[1])
        for row in connection.execute("PRAGMA table_info(vinted_competitors)").fetchall()
    }
    additions = (
        ("seller_name", "TEXT NOT NULL DEFAULT ''"),
        ("seller_profile_url", "TEXT NOT NULL DEFAULT ''"),
        ("first_seen_at", "TEXT NOT NULL DEFAULT ''"),
        ("last_seen_at", "TEXT NOT NULL DEFAULT ''"),
        ("interesting_item_count", "INTEGER NOT NULL DEFAULT 0"),
        ("distinct_search_terms", "INTEGER NOT NULL DEFAULT 0"),
        ("gone_item_count", "INTEGER NOT NULL DEFAULT 0"),
        ("top_deal_keyword", "TEXT NOT NULL DEFAULT ''"),
        ("same_keyword_deal_count", "INTEGER NOT NULL DEFAULT 0"),
        ("max_favorite_count", "INTEGER NOT NULL DEFAULT 0"),
        ("avg_favorite_count", "REAL NOT NULL DEFAULT 0"),
        ("status", "TEXT NOT NULL DEFAULT 'candidate'"),
        ("manual_priority", "INTEGER NOT NULL DEFAULT 0"),
        ("score", "REAL NOT NULL DEFAULT 0"),
        ("updated_at", "TEXT NOT NULL DEFAULT ''"),
        ("payload_json", "TEXT NOT NULL DEFAULT ''"),
    )
    for name, sql_type in additions:
        if name in columns:
            continue
        connection.execute(f"ALTER TABLE vinted_competitors ADD COLUMN {name} {sql_type}")


def _ensure_vinted_search_runs_columns(connection: sqlite3.Connection) -> None:
    columns = {
        str(row[1])
        for row in connection.execute("PRAGMA table_info(vinted_search_runs)").fetchall()
    }
    additions = (
        ("title", "TEXT NOT NULL DEFAULT ''"),
        ("notes", "TEXT NOT NULL DEFAULT ''"),
    )
    for name, sql_type in additions:
        if name in columns:
            continue
        connection.execute(f"ALTER TABLE vinted_search_runs ADD COLUMN {name} {sql_type}")


def _ensure_vinted_offer_history_columns(connection: sqlite3.Connection) -> None:
    columns = {
        str(row[1])
        for row in connection.execute("PRAGMA table_info(vinted_offer_history)").fetchall()
    }
    additions = (
        ("item_id", "TEXT NOT NULL DEFAULT ''"),
        ("item_name", "TEXT NOT NULL DEFAULT ''"),
        ("offer_input_value", "TEXT NOT NULL DEFAULT ''"),
        ("offer_discount_percent", "REAL"),
        ("source_price_value", "REAL"),
        ("current_url", "TEXT NOT NULL DEFAULT ''"),
        ("result_json", "TEXT NOT NULL DEFAULT ''"),
    )
    for name, sql_type in additions:
        if name in columns:
            continue
        connection.execute(f"ALTER TABLE vinted_offer_history ADD COLUMN {name} {sql_type}")


def _ensure_vinted_history_columns(connection: sqlite3.Connection) -> None:
    unavailable_columns = {
        str(row[1])
        for row in connection.execute("PRAGMA table_info(vinted_unavailable_items)").fetchall()
    }
    unavailable_additions = (
        ("previous_status", "TEXT NOT NULL DEFAULT ''"),
        ("first_seen_at", "TEXT NOT NULL DEFAULT ''"),
        ("sold_at", "TEXT NOT NULL DEFAULT ''"),
        ("image_url", "TEXT NOT NULL DEFAULT ''"),
        ("local_image_path", "TEXT NOT NULL DEFAULT ''"),
        ("screenshot_path", "TEXT NOT NULL DEFAULT ''"),
        ("html_path", "TEXT NOT NULL DEFAULT ''"),
        ("snapshot_json", "TEXT NOT NULL DEFAULT ''"),
    )
    for name, sql_type in unavailable_additions:
        if name not in unavailable_columns:
            connection.execute(f"ALTER TABLE vinted_unavailable_items ADD COLUMN {name} {sql_type}")

    tracking_columns = {
        str(row[1])
        for row in connection.execute("PRAGMA table_info(vinted_deal_sale_tracking)").fetchall()
    }
    tracking_additions = (
        ("availability_status", "TEXT NOT NULL DEFAULT 'available'"),
        ("unavailable_at", "TEXT NOT NULL DEFAULT ''"),
        ("unavailable_reason", "TEXT NOT NULL DEFAULT ''"),
    )
    for name, sql_type in tracking_additions:
        if name not in tracking_columns:
            connection.execute(f"ALTER TABLE vinted_deal_sale_tracking ADD COLUMN {name} {sql_type}")


def _build_vinted_search_run_key() -> str:
    return f"vinted-{datetime.now().strftime('%Y%m%dT%H%M%S%f')}"


def _derive_vinted_run_query_label(rows: list[dict]) -> str:
    search_terms = []
    seen_terms: set[str] = set()
    for row in rows:
        value = str(row.get("search_term", "") or "").strip()
        if value and value not in seen_terms:
            seen_terms.add(value)
            search_terms.append(value)
    if not search_terms:
        for row in rows:
            value = _extract_vinted_search_text_from_url(str(row.get("search_url", "") or ""))
            if value and value not in seen_terms:
                seen_terms.add(value)
                search_terms.append(value)
    if not search_terms:
        return "ricerca senza query"
    if len(search_terms) == 1:
        return search_terms[0]
    preview = ", ".join(search_terms[:2])
    if len(search_terms) > 2:
        preview += f" (+{len(search_terms) - 2})"
    return preview


def _derive_vinted_run_primary_search_term(rows: list[dict]) -> str:
    for row in rows:
        value = str(row.get("search_term", "") or "").strip()
        if value:
            return value
    for row in rows:
        value = _extract_vinted_search_text_from_url(str(row.get("search_url", "") or ""))
        if value:
            return value
    return ""


def _derive_vinted_run_primary_search_url(rows: list[dict]) -> str:
    for row in rows:
        value = str(row.get("search_url", "") or "").strip()
        if value:
            return value
    return ""


def _extract_vinted_search_text_from_url(url: str) -> str:
    raw_url = str(url or "").strip()
    if not raw_url:
        return ""
    try:
        query_values = parse_qs(urlsplit(raw_url).query).get("search_text", [])
    except ValueError:
        return ""
    for value in query_values:
        normalized = str(value or "").strip()
        if normalized:
            return normalized
    return ""


def _deserialize_vinted_run_row(snapshot_json: str, db_path: Path) -> dict:
    try:
        row = json.loads(snapshot_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Snapshot ricerca Vinted non valido: {exc}") from exc
    if not isinstance(row, dict):
        raise ValueError("Snapshot ricerca Vinted non valido: atteso un oggetto JSON.")
    normalized_row = dict(row)
    normalized_row["source"] = str(normalized_row.get("source", "") or "vinted")
    normalized_row["db_path"] = str(db_path)
    normalized_row["db_saved"] = True
    if not str(normalized_row.get("shipping_alert", "") or "").strip():
        shipping_value = normalized_row.get("shipping_price_value")
        try:
            normalized_row["shipping_alert"] = (
                "sped > 2,99"
                if shipping_value not in ("", None) and float(shipping_value) > 2.99
                else ""
            )
        except (TypeError, ValueError):
            normalized_row["shipping_alert"] = ""
    return normalized_row


def _format_vinted_search_run_label(
    created_at: str,
    title: str,
    query_label: str,
    item_count: int,
    run_id: int,
) -> str:
    timestamp_label = str(created_at or "").replace("T", " ")
    if len(timestamp_label) >= 19:
        timestamp_label = timestamp_label[:19]
    label_part = str(title or "").strip() or str(query_label or "ricerca senza query").strip() or "ricerca senza query"
    item_label = "articolo" if int(item_count or 0) == 1 else "articoli"
    return f"{timestamp_label} · {label_part} · {int(item_count or 0)} {item_label} · #{run_id}"
