import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path


DEFAULT_VINTED_DB_PATH = Path("data") / "scraper.db"


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


def save_vinted_rows(rows: list[dict], db_path: str | Path = DEFAULT_VINTED_DB_PATH) -> dict[str, int | str]:
    path = Path(db_path).expanduser().resolve()
    ensure_vinted_database(path)
    counts = {"new_items": 0, "updated_items": 0, "new_search_hits": 0, "updated_search_hits": 0}

    with closing(sqlite3.connect(path)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        _create_schema(connection)
        for row in rows:
            link = str(row.get("link", "") or "").strip()
            if not link:
                continue
            search_term = str(row.get("search_term", "") or "").strip()
            tag = str(row.get("tag", "") or "ricercato").strip() or "ricercato"
            observed_at = str(row.get("extracted_at", "") or datetime.now().isoformat(timespec="seconds"))

            item_exists = connection.execute(
                "SELECT 1 FROM vinted_items WHERE link = ?",
                (link,),
            ).fetchone() is not None
            connection.execute(
                """
                INSERT INTO vinted_items (
                    link, item_id, name, price_text, price_value, currency,
                    raw_text, first_seen_at, last_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(link) DO UPDATE SET
                    item_id = excluded.item_id,
                    name = excluded.name,
                    price_text = excluded.price_text,
                    price_value = excluded.price_value,
                    currency = excluded.currency,
                    raw_text = excluded.raw_text,
                    last_seen_at = excluded.last_seen_at
                """,
                (
                    link,
                    str(row.get("item_id", "") or ""),
                    str(row.get("name", "") or ""),
                    str(row.get("price", "") or ""),
                    row.get("price_value"),
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

        connection.commit()

    return {"db_path": str(path), **counts}


def load_vinted_rows(
    db_path: str | Path = DEFAULT_VINTED_DB_PATH,
    search_term: str = "",
    tag_filter: str = "",
    limit: int = 500,
) -> tuple[list[dict], dict[str, int | str | bool]]:
    path = Path(db_path).expanduser().resolve()
    database_created = not path.exists()
    ensure_vinted_database(path)

    normalized_limit = max(int(limit), 0)
    filter_value = str(search_term or "").strip()
    tag_value = str(tag_filter or "").strip()
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
                    i.price_text,
                    i.price_value,
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
            "tag": record["tag"] or "ricercato",
            "search_url": record["search_url"],
            "item_id": record["item_id"],
            "name": record["name"],
            "price": record["price_text"],
            "price_value": record["price_value"],
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
    return rows, {
        "db_path": str(path),
        "loaded_from_db": True,
        "db_created": database_created,
        "db_search_filter": filter_value,
        "db_tag_filter": tag_value,
        "db_limit": normalized_limit,
        "db_total_items": total_items,
        "db_total_search_hits": total_hits,
        "db_filtered_search_hits": filtered_hits,
        "row_count": len(rows),
    }


def _create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS vinted_items (
            link TEXT PRIMARY KEY,
            item_id TEXT NOT NULL DEFAULT '',
            name TEXT NOT NULL DEFAULT '',
            price_text TEXT NOT NULL DEFAULT '',
            price_value REAL,
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
        """
    )
    _ensure_vinted_search_hits_tag_column(connection)
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
