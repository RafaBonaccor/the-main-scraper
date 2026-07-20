import json
import re
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


DEFAULT_VINTED_DB_PATH = Path("data") / "scraper.db"
VINTED_ITEM_ID_PATTERN = re.compile(r"/items/(\d+)")


def ensure_vinted_database(db_path: str | Path = DEFAULT_VINTED_DB_PATH) -> Path:
    path = Path(db_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with closing(sqlite3.connect(path)) as connection:
            _create_schema(connection)
            connection.commit()
    except sqlite3.Error as exc:
        raise ValueError(f"Impossibile creare il database Vinted: {exc}") from exc
    return path


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


def load_vinted_known_item_keys(db_path: str | Path = DEFAULT_VINTED_DB_PATH) -> set[str]:
    path = Path(db_path).expanduser().resolve()
    ensure_vinted_database(path)
    try:
        with closing(sqlite3.connect(path)) as connection:
            records = connection.execute(
                "SELECT item_id, link FROM vinted_items"
            ).fetchall()
    except sqlite3.Error as exc:
        raise ValueError(f"Database Vinted non valido: {exc}") from exc

    keys: set[str] = set()
    for item_id, link in records:
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
) -> dict[str, int | str]:
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
        connection.commit()

    return {
        "db_path": str(path),
        "deal_notifications_saved_count": len(valid_rows),
        "new_deal_notifications": new_entries,
        "updated_deal_notifications": updated_entries,
    }


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

            item_exists = connection.execute(
                "SELECT 1 FROM vinted_items WHERE link = ?",
                (link,),
            ).fetchone() is not None
            connection.execute(
                """
                INSERT INTO vinted_items (
                    link, item_id, name, description, published_at, price_text, price_value,
                    shipping_price_text, shipping_price_value, total_price_text, total_price_value,
                    offer_available, offer_text, favorite_count, evaluation_label, currency,
                    raw_text, first_seen_at, last_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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

    return {
        "db_path": str(path),
        "db_total_search_runs": total_search_runs,
        "search_run_key": run_key,
        "search_run_label": run_label,
        "search_run_query": query_label,
        "search_run_kind": normalized_run_kind,
        "search_run_item_count": len(valid_rows),
        **counts,
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
            evaluation_label TEXT NOT NULL DEFAULT '',
            currency TEXT NOT NULL DEFAULT '',
            raw_text TEXT NOT NULL DEFAULT '',
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL
        );

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
        """
    )
    _ensure_vinted_search_hits_tag_column(connection)
    _ensure_vinted_items_description_column(connection)
    _ensure_vinted_items_published_at_column(connection)
    _ensure_vinted_items_offer_columns(connection)
    _ensure_vinted_items_favorite_columns(connection)
    _ensure_vinted_search_runs_columns(connection)
    _ensure_vinted_offer_history_columns(connection)
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_vinted_search_hits_tag ON vinted_search_hits(tag)"
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
