"""Persistent cohort analytics for the independent marketplace trend scout."""

from __future__ import annotations

import math
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from .utils import normalize_whitespace


DEFAULT_TREND_QUERIES = (
    "gioielli", "spille", "collane", "braccialetti", "anelli",
    "orecchini", "ciondoli", "portachiavi", "accessori borsa",
)

_CATEGORY_ALIASES = {
    "gioielli": {"gioielli", "gioiello", "jewelry", "jewellery", "bijoux", "schmuck", "joya", "joyas",
                 "spilla", "spille", "brooch", "collana", "collane", "necklace", "bracciale", "braccialetti",
                 "bracelet", "anello", "anelli", "ring", "orecchino", "orecchini", "earring", "ciondolo",
                 "ciondoli", "pendant", "charm", "charms"},
    "spille": {"spilla", "spille", "brooch", "brooches", "broche", "brosche", "pin", "pins"},
    "collane": {"collana", "collane", "necklace", "necklaces", "ketting", "collier"},
    "braccialetti": {"bracciale", "bracciali", "braccialetto", "braccialetti", "bracelet", "bracelets", "armband"},
    "anelli": {"anello", "anelli", "ring", "rings", "bague", "ringen"},
    "orecchini": {"orecchino", "orecchini", "earring", "earrings", "boucles", "ohrringe"},
    "ciondoli": {"ciondolo", "ciondoli", "pendant", "pendants", "charm", "charms", "bedel", "bedels"},
    "portachiavi": {"portachiavi", "keychain", "keychains", "keyring", "keyrings", "sleutelhanger", "porte", "cles"},
    "accessori borsa": {"bagcharm", "bag", "charm", "portachiavi", "keychain", "keyring", "sleutelhanger"},
}
_CATEGORY_QUERY_CANONICAL = {
    "gioiello": "gioielli", "spillo": "spille", "spilli": "spille", "spilla": "spille",
    "collana": "collane", "bracciale": "braccialetti", "bracciali": "braccialetti",
    "anello": "anelli", "orecchino": "orecchini", "ciondolo": "ciondoli", "charms": "ciondoli",
    "portachiave": "portachiavi", "accessori per borsa": "accessori borsa",
}


def _canonical_category(query: str) -> str:
    normalized = normalize_whitespace(query).casefold()
    return _CATEGORY_QUERY_CANONICAL.get(normalized, normalized)


@dataclass(frozen=True)
class TrendPolicy:
    evaluation_window_hours: int = 72
    evaluation_spacing_hours: int = 24
    minimum_sample_size: int = 30
    minimum_sell_through_percent: float = 8.0
    promising_sell_through_percent: float = 15.0
    weak_evaluations_before_pause: int = 2
    empty_scans_before_pause: int = 3
    error_scans_before_cooldown: int = 3
    pause_hours: int = 72


def parse_trend_queries(value: object) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        chunks = [str(item or "") for item in value]
    else:
        chunks = re.split(r"[,;\n]+", str(value or ""))
    result: list[str] = []
    seen: set[str] = set()
    for chunk in chunks:
        query = normalize_whitespace(chunk)
        canonical = _canonical_category(query)
        if canonical in _CATEGORY_ALIASES:
            query = canonical
        key = query.casefold()
        if query and key not in seen:
            seen.add(key)
            result.append(query)
    return result or list(DEFAULT_TREND_QUERIES)


def query_key(query: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", query.casefold()).strip("-")


def infer_brand(query: str) -> str:
    known = (
        "pop mart", "hello kitty", "thomas sabo", "vivienne westwood",
        "maison de sabre", "jellycat", "sanrio", "kuromi", "monchhichi",
        "miffy", "coach", "nomination", "trollbeads", "swarovski",
        "trifari", "monet", "pandora", "rosato", "giovanni raspini",
    )
    lowered = _canonical_category(query)
    if lowered in _CATEGORY_ALIASES:
        return "Categoria"
    return next((brand.title() for brand in known if lowered.startswith(brand)), query.split()[0].title())


def query_kind(query: str) -> str:
    normalized = _canonical_category(query)
    if normalized == "gioielli":
        return "umbrella"
    if normalized in _CATEGORY_ALIASES:
        return "category"
    return "brand_product"


def row_matches_trend_query(row: dict, query: str) -> bool:
    """Reject search-engine noise without requiring a costly detail-page visit."""
    searchable = f"{row.get('name', '')} {row.get('link', '')}"
    title_tokens = set(re.findall(r"[a-z0-9]+", normalize_whitespace(searchable).casefold()))
    query_tokens = re.findall(r"[a-z0-9]+", normalize_whitespace(query).casefold())
    if not title_tokens or not query_tokens:
        return False
    normalized_query = _canonical_category(query)
    if normalized_query == "gioielli":
        # The umbrella query is a benchmark of the marketplace search itself;
        # item titles often contain only the subtype or brand.
        return True
    if normalized_query in _CATEGORY_ALIASES:
        return bool(title_tokens.intersection(_CATEGORY_ALIASES[normalized_query]))
    brand_tokens = re.findall(r"[a-z0-9]+", infer_brand(query).casefold())
    product_tokens = [token for token in query_tokens if token not in brand_tokens]
    product_aliases = {
        "bag": {"bag", "borsa"},
        "charm": {"charm", "bagcharm", "pendant", "hanger", "ciondolo", "keychain", "keyring", "sleutelhanger"},
        "portachiavi": {"portachiavi", "keychain", "keyring", "sleutelhanger", "porte", "cles"},
        "spilla": {"spilla", "brooch", "broche", "brosche"},
        "vintage": {"vintage", "retro"},
    }
    brand_match = all(token in title_tokens for token in brand_tokens)
    product_match = not product_tokens or any(
        title_tokens.intersection(product_aliases.get(token, {token})) for token in product_tokens
    )
    # Pop Mart character names are commonly listed without the parent company.
    distinctive_character_match = infer_brand(query).casefold() == "pop mart" and any(
        token in title_tokens for token in product_tokens if token not in {"bag", "charm", "portachiavi"}
    )
    return (brand_match and product_match) or distinctive_character_match


def wilson_lower_bound(sold: int, total: int, z: float = 1.96) -> float:
    if total <= 0:
        return 0.0
    p = sold / total
    denominator = 1 + (z * z / total)
    centre = p + (z * z / (2 * total))
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total)
    return max((centre - margin) / denominator, 0.0) * 100


class VintedTrendStore:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    def _initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS vinted_trend_queries (
                    query_key TEXT PRIMARY KEY, query_text TEXT NOT NULL, brand TEXT NOT NULL,
                    query_kind TEXT NOT NULL DEFAULT 'brand_product',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    status TEXT NOT NULL DEFAULT 'learning', created_at TEXT NOT NULL,
                    last_scan_at TEXT, last_evaluated_at TEXT, next_scan_at TEXT, next_review_at TEXT,
                    scans_count INTEGER NOT NULL DEFAULT 0, empty_streak INTEGER NOT NULL DEFAULT 0,
                    error_streak INTEGER NOT NULL DEFAULT 0, weak_streak INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT NOT NULL DEFAULT '', last_result_count INTEGER NOT NULL DEFAULT 0,
                    mature_count INTEGER NOT NULL DEFAULT 0, sold_count INTEGER NOT NULL DEFAULT 0,
                    sell_through_percent REAL NOT NULL DEFAULT 0,
                    confidence_lower_percent REAL NOT NULL DEFAULT 0, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS vinted_trend_items (
                    item_link TEXT PRIMARY KEY, item_id TEXT NOT NULL DEFAULT '', title TEXT NOT NULL DEFAULT '',
                    price_value REAL, favorites_count INTEGER, image_url TEXT NOT NULL DEFAULT '',
                    first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, last_checked_at TEXT,
                    status TEXT NOT NULL DEFAULT 'active', sold_at TEXT, unavailable_at TEXT,
                    sold_after_seconds INTEGER, seller_member_id TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS vinted_trend_item_queries (
                    item_link TEXT NOT NULL, query_key TEXT NOT NULL, first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL, PRIMARY KEY(item_link, query_key)
                );
                CREATE INDEX IF NOT EXISTS idx_vinted_trend_items_due
                    ON vinted_trend_items(status, last_checked_at, first_seen_at);
                CREATE INDEX IF NOT EXISTS idx_vinted_trend_item_queries_query
                    ON vinted_trend_item_queries(query_key, first_seen_at);
                """
            )
            columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(vinted_trend_queries)")}
            if "enabled" not in columns:
                connection.execute("ALTER TABLE vinted_trend_queries ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1")
            if "last_evaluated_at" not in columns:
                connection.execute("ALTER TABLE vinted_trend_queries ADD COLUMN last_evaluated_at TEXT")
            if "query_kind" not in columns:
                connection.execute("ALTER TABLE vinted_trend_queries ADD COLUMN query_kind TEXT NOT NULL DEFAULT 'brand_product'")

    def sync_queries(self, queries: list[str], *, now: datetime | None = None) -> None:
        timestamp = (now or datetime.now()).isoformat(timespec="seconds")
        with self.connect() as connection:
            connection.execute("UPDATE vinted_trend_queries SET enabled=0,updated_at=?", (timestamp,))
            for query in queries:
                key = query_key(query)
                connection.execute(
                    """INSERT INTO vinted_trend_queries
                    (query_key, query_text, brand, query_kind, enabled, created_at, next_scan_at, updated_at)
                    VALUES (?, ?, ?, ?, 1, ?, ?, ?)
                    ON CONFLICT(query_key) DO UPDATE SET query_text=excluded.query_text,
                    brand=excluded.brand, query_kind=excluded.query_kind, enabled=1, updated_at=excluded.updated_at""",
                    (key, query, infer_brand(query), query_kind(query), timestamp, timestamp, timestamp),
                )

    def due_queries(self, limit: int, *, now: datetime | None = None) -> list[dict]:
        timestamp = (now or datetime.now()).isoformat(timespec="seconds")
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT * FROM vinted_trend_queries
                WHERE enabled=1 AND COALESCE(next_scan_at, '') <= ?
                ORDER BY CASE status WHEN 'promising' THEN 0 WHEN 'active' THEN 1
                WHEN 'learning' THEN 2 ELSE 3 END, COALESCE(last_scan_at, '')
                LIMIT ?""",
                (timestamp, max(int(limit), 1)),
            ).fetchall()
        return [dict(row) for row in rows]

    def record_scan(self, query: str, rows: list[dict], *, next_scan_seconds: int, now: datetime | None = None) -> int:
        moment = now or datetime.now()
        timestamp = moment.isoformat(timespec="seconds")
        next_at = (moment + timedelta(seconds=max(next_scan_seconds, 0))).isoformat(timespec="seconds")
        key = query_key(query)
        saved = 0
        with self.connect() as connection:
            for row in rows:
                link = str(row.get("link", "") or "").strip()
                if not link:
                    continue
                saved += 1
                connection.execute(
                    """INSERT INTO vinted_trend_items
                    (item_link,item_id,title,price_value,favorites_count,image_url,first_seen_at,last_seen_at,status,seller_member_id)
                    VALUES (?,?,?,?,?,?,?,?, 'active', ?)
                    ON CONFLICT(item_link) DO UPDATE SET title=excluded.title,
                    price_value=COALESCE(excluded.price_value,vinted_trend_items.price_value),
                    favorites_count=COALESCE(excluded.favorites_count,vinted_trend_items.favorites_count),
                    image_url=CASE WHEN excluded.image_url<>'' THEN excluded.image_url ELSE vinted_trend_items.image_url END,
                    last_seen_at=excluded.last_seen_at,
                    seller_member_id=CASE WHEN excluded.seller_member_id<>'' THEN excluded.seller_member_id ELSE vinted_trend_items.seller_member_id END""",
                    (link, str(row.get("item_id", "") or ""), str(row.get("name", "") or ""),
                     _number(row.get("price_value")), _integer(row.get("favorite_count", row.get("favorites_count"))),
                     str(row.get("image_url", "") or ""), timestamp, timestamp,
                     str(row.get("seller_member_id", "") or "")),
                )
                connection.execute(
                    """INSERT INTO vinted_trend_item_queries(item_link,query_key,first_seen_at,last_seen_at)
                    VALUES(?,?,?,?) ON CONFLICT(item_link,query_key) DO UPDATE SET last_seen_at=excluded.last_seen_at""",
                    (link, key, timestamp, timestamp),
                )
            count = saved
            connection.execute(
                """UPDATE vinted_trend_queries SET last_scan_at=?, next_scan_at=?, scans_count=scans_count+1,
                empty_streak=CASE WHEN ?=0 THEN empty_streak+1 ELSE 0 END, error_streak=0,
                last_error='', last_result_count=?, updated_at=? WHERE query_key=?""",
                (timestamp, next_at, count, count, timestamp, key),
            )
        return saved

    def record_scan_error(self, query: str, error: Exception, *, policy: TrendPolicy, now: datetime | None = None) -> None:
        moment = now or datetime.now()
        timestamp = moment.isoformat(timespec="seconds")
        with self.connect() as connection:
            row = connection.execute("SELECT error_streak FROM vinted_trend_queries WHERE query_key=?", (query_key(query),)).fetchone()
            streak = int(row[0] if row else 0) + 1
            cooldown = streak >= policy.error_scans_before_cooldown
            next_at = moment + timedelta(hours=policy.pause_hours if cooldown else 1)
            connection.execute(
                """UPDATE vinted_trend_queries SET error_streak=?, last_error=?,
                status=CASE WHEN ? THEN 'cooldown_error' ELSE status END, next_scan_at=?,
                next_review_at=?, updated_at=? WHERE query_key=?""",
                (streak, f"{type(error).__name__}: {error}"[:1000], cooldown,
                 next_at.isoformat(timespec="seconds"), next_at.isoformat(timespec="seconds") if cooldown else None,
                 timestamp, query_key(query)),
            )

    def due_items(self, *, check_interval_seconds: int, limit: int, max_age_hours: int, now: datetime | None = None) -> list[dict]:
        moment = now or datetime.now()
        due = (moment - timedelta(seconds=max(check_interval_seconds, 0))).isoformat(timespec="seconds")
        oldest = (moment - timedelta(hours=max(max_age_hours, 1))).isoformat(timespec="seconds")
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT * FROM vinted_trend_items WHERE status='active' AND first_seen_at>=?
                AND (last_checked_at IS NULL OR last_checked_at<=?)
                ORDER BY CASE WHEN last_checked_at IS NULL THEN 0 ELSE 1 END,
                COALESCE(last_checked_at,first_seen_at), COALESCE(favorites_count,0) DESC LIMIT ?""",
                (oldest, due, max(int(limit), 1)),
            ).fetchall()
        return [dict(row) for row in rows]

    def save_item_check(self, link: str, *, sold: bool, unavailable: bool, checked_at: datetime | None = None) -> None:
        moment = checked_at or datetime.now()
        timestamp = moment.isoformat(timespec="seconds")
        with self.connect() as connection:
            row = connection.execute("SELECT first_seen_at,status FROM vinted_trend_items WHERE item_link=?", (link,)).fetchone()
            if not row:
                return
            if sold:
                first_seen = datetime.fromisoformat(str(row["first_seen_at"]))
                connection.execute(
                    """UPDATE vinted_trend_items SET status='sold',sold_at=COALESCE(sold_at,?),
                    sold_after_seconds=COALESCE(sold_after_seconds,?),last_checked_at=? WHERE item_link=?""",
                    (timestamp, max(int((moment-first_seen).total_seconds()), 0), timestamp, link),
                )
            elif unavailable:
                connection.execute(
                    "UPDATE vinted_trend_items SET status='unavailable',unavailable_at=?,last_checked_at=? WHERE item_link=?",
                    (timestamp, timestamp, link),
                )
            else:
                connection.execute("UPDATE vinted_trend_items SET last_checked_at=? WHERE item_link=?", (timestamp, link))

    def evaluate_all(self, policy: TrendPolicy, *, now: datetime | None = None) -> list[dict]:
        moment = now or datetime.now()
        cutoff = (moment - timedelta(hours=policy.evaluation_window_hours)).isoformat(timespec="seconds")
        timestamp = moment.isoformat(timespec="seconds")
        changes: list[dict] = []
        with self.connect() as connection:
            queries = connection.execute("SELECT * FROM vinted_trend_queries WHERE enabled=1 ORDER BY query_text").fetchall()
            for query in queries:
                counts = connection.execute(
                    """SELECT COUNT(*) AS mature,
                    SUM(CASE WHEN i.status='sold' AND i.sold_after_seconds<=? THEN 1 ELSE 0 END) AS sold
                    FROM vinted_trend_item_queries q JOIN vinted_trend_items i ON i.item_link=q.item_link
                    WHERE q.query_key=? AND q.first_seen_at<=? AND i.status<>'unavailable'""",
                    (policy.evaluation_window_hours * 3600, query["query_key"], cutoff),
                ).fetchone()
                mature = int(counts["mature"] or 0)
                sold = int(counts["sold"] or 0)
                rate = (sold / mature * 100) if mature else 0.0
                lower = wilson_lower_bound(sold, mature)
                old_status = str(query["status"])
                weak = int(query["weak_streak"] or 0)
                empty = int(query["empty_streak"] or 0)
                next_review_raw = str(query["next_review_at"] or "")
                last_scan_raw = str(query["last_scan_at"] or "")
                last_evaluated_raw = str(query["last_evaluated_at"] or "")
                transient_error = int(query["error_streak"] or 0) > 0
                evaluation_due_before = (moment - timedelta(hours=policy.evaluation_spacing_hours)).isoformat(timespec="seconds")
                evaluation_due = not last_evaluated_raw or last_evaluated_raw <= evaluation_due_before
                probe_not_run = bool(next_review_raw and last_scan_raw < next_review_raw)
                still_cooling = old_status == "cooldown_error" and (next_review_raw > timestamp or probe_not_run)
                waiting_for_probe = old_status.startswith("paused") and (next_review_raw > timestamp or probe_not_run)
                record_evaluation = evaluation_due and not transient_error and not still_cooling and not waiting_for_probe
                if still_cooling:
                    status = old_status
                elif waiting_for_probe:
                    status = old_status
                elif transient_error:
                    status = old_status
                elif empty >= policy.empty_scans_before_pause:
                    status, weak = "paused_empty", weak
                elif mature < policy.minimum_sample_size:
                    status, weak = "learning", 0
                elif rate >= policy.promising_sell_through_percent:
                    status, weak = "promising", 0
                elif rate < policy.minimum_sell_through_percent:
                    if record_evaluation:
                        weak += 1
                    status = "paused_low_rotation" if weak >= policy.weak_evaluations_before_pause else "observing"
                else:
                    status, weak = "active", 0
                paused = status.startswith("paused")
                next_review = (
                    next_review_raw if still_cooling else
                    next_review_raw if waiting_for_probe else
                    (moment + timedelta(hours=policy.pause_hours)).isoformat(timespec="seconds") if paused else None
                )
                connection.execute(
                    """UPDATE vinted_trend_queries SET status=?,weak_streak=?,mature_count=?,sold_count=?,
                    sell_through_percent=?,confidence_lower_percent=?,next_scan_at=CASE WHEN ? THEN ? ELSE next_scan_at END,
                    next_review_at=?,last_evaluated_at=CASE WHEN ? THEN ? ELSE last_evaluated_at END,
                    updated_at=? WHERE query_key=?""",
                    (status, weak, mature, sold, rate, lower, paused, next_review, next_review,
                     record_evaluation, timestamp, timestamp, query["query_key"]),
                )
                changes.append({"query": query["query_text"], "brand": query["brand"], "old_status": old_status,
                                "status": status, "mature_count": mature, "sold_count": sold,
                                "sell_through_percent": round(rate, 2), "confidence_lower_percent": round(lower, 2)})
        return changes

    def summary(self) -> list[dict]:
        with self.connect() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT * FROM vinted_trend_queries ORDER BY sell_through_percent DESC,mature_count DESC,query_text"
            ).fetchall()]


def _number(value: object) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _integer(value: object) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None
