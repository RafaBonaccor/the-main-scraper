import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from scraper_app.sources.vinted import (
    _card_payload_to_row,
    _vinted_timing_config,
    build_vinted_search_url,
    extract_vinted_search_term,
    parse_vinted_price,
)
from scraper_app.vinted_database import load_vinted_rows, save_vinted_rows


class VintedTests(unittest.TestCase):
    def test_search_url_and_term(self) -> None:
        url = build_vinted_search_url("macbook pro")

        self.assertEqual("https://www.vinted.it/catalog?search_text=macbook+pro", url)
        self.assertEqual("macbook pro", extract_vinted_search_term(url))

    def test_card_payload_extracts_name_price_and_clean_link(self) -> None:
        row = _card_payload_to_row(
            {
                "link": "https://www.vinted.it/items/123456-macbook-pro?referrer=catalog",
                "title": "Apple MacBook Pro 13",
                "price": "450,00 €",
                "raw_text": "Apple MacBook Pro 13 450,00 €",
            },
            search_term="macbook",
            search_url="https://www.vinted.it/catalog?search_text=macbook",
        )

        self.assertEqual("123456", row["item_id"])
        self.assertEqual("Apple MacBook Pro 13", row["name"])
        self.assertEqual(450.0, row["price_value"])
        self.assertEqual("https://www.vinted.it/items/123456-macbook-pro", row["link"])
        self.assertEqual("ricercato", row["tag"])

    def test_price_parser_supports_italian_format(self) -> None:
        self.assertEqual(1299.99, parse_vinted_price("1.299,99 €"))
        self.assertEqual(25.0, parse_vinted_price("25 €"))

    def test_vinted_timing_config_applies_slow_mode_floor(self) -> None:
        self.assertEqual((2.5, 4.0), _vinted_timing_config(True, 0.5, 1.0))
        self.assertEqual((1.0, 2.0), _vinted_timing_config(False, 1.0, 2.0))

    def test_database_deduplicates_items_and_keeps_search_tags(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "vinted.db"
            base_row = {
                "link": "https://www.vinted.it/items/123-macbook",
                "item_id": "123",
                "name": "MacBook",
                "price": "300,00 €",
                "price_value": 300.0,
                "currency": "EUR",
                "raw_text": "MacBook 300,00 €",
                "extracted_at": "2026-07-01T10:00:00",
                "search_url": "https://www.vinted.it/catalog?search_text=macbook",
            }

            first = save_vinted_rows([{**base_row, "search_term": "macbook"}], db_path)
            second = save_vinted_rows([{**base_row, "search_term": "macbook pro"}], db_path)
            third = save_vinted_rows([{**base_row, "search_term": "macbook"}], db_path)

            self.assertEqual(1, first["new_items"])
            self.assertEqual(1, second["updated_items"])
            self.assertEqual(1, second["new_search_hits"])
            self.assertEqual(1, third["updated_search_hits"])
            with closing(sqlite3.connect(db_path)) as connection:
                item_count = connection.execute("SELECT COUNT(*) FROM vinted_items").fetchone()[0]
                hit_count = connection.execute("SELECT COUNT(*) FROM vinted_search_hits").fetchone()[0]
                macbook_times = connection.execute(
                    "SELECT times_seen FROM vinted_search_hits WHERE search_term = 'macbook'"
                ).fetchone()[0]
                tags = [
                    row[0]
                    for row in connection.execute(
                        "SELECT DISTINCT tag FROM vinted_search_hits ORDER BY tag"
                    ).fetchall()
                ]
            self.assertEqual(1, item_count)
            self.assertEqual(2, hit_count)
            self.assertEqual(2, macbook_times)
            self.assertEqual(["ricercato"], tags)

            rows, meta = load_vinted_rows(db_path, search_term="macbook", limit=10)
            self.assertEqual(2, len(rows))
            self.assertEqual(1, meta["db_total_items"])
            self.assertEqual(2, meta["db_total_search_hits"])
            macbook_row = next(row for row in rows if row["search_term"] == "macbook")
            self.assertEqual(2, macbook_row["times_seen"])
            self.assertEqual("MacBook", macbook_row["name"])
            self.assertEqual("ricercato", macbook_row["tag"])

            filtered_rows, filtered_meta = load_vinted_rows(db_path, tag_filter="ricercato", limit=10)
            self.assertEqual(2, len(filtered_rows))
            self.assertEqual("ricercato", filtered_meta["db_tag_filter"])

            hidden_rows, hidden_meta = load_vinted_rows(db_path, tag_filter="altro", limit=10)
            self.assertEqual([], hidden_rows)
            self.assertEqual(0, hidden_meta["db_filtered_search_hits"])

    def test_existing_database_without_tag_column_is_migrated(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "old_vinted.db"
            with closing(sqlite3.connect(db_path)) as connection:
                connection.executescript(
                    """
                    CREATE TABLE vinted_items (
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
                    CREATE TABLE vinted_search_hits (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        item_link TEXT NOT NULL,
                        search_term TEXT NOT NULL,
                        search_url TEXT NOT NULL DEFAULT '',
                        first_seen_at TEXT NOT NULL,
                        last_seen_at TEXT NOT NULL,
                        times_seen INTEGER NOT NULL DEFAULT 1,
                        UNIQUE(item_link, search_term),
                        FOREIGN KEY(item_link) REFERENCES vinted_items(link) ON DELETE CASCADE
                    );
                    INSERT INTO vinted_items (
                        link, item_id, name, price_text, price_value, currency, raw_text, first_seen_at, last_seen_at
                    ) VALUES (
                        'https://www.vinted.it/items/456-air', '456', 'MacBook Air', '250,00 â‚¬',
                        250.0, 'EUR', 'MacBook Air 250,00 â‚¬', '2026-07-01T10:00:00', '2026-07-01T10:00:00'
                    );
                    INSERT INTO vinted_search_hits (
                        item_link, search_term, search_url, first_seen_at, last_seen_at, times_seen
                    ) VALUES (
                        'https://www.vinted.it/items/456-air', 'macbook',
                        'https://www.vinted.it/catalog?search_text=macbook',
                        '2026-07-01T10:00:00', '2026-07-01T10:00:00', 1
                    );
                    """
                )
                connection.commit()

            rows, meta = load_vinted_rows(db_path, tag_filter="ricercato", limit=10)

            self.assertEqual(1, len(rows))
            self.assertEqual("ricercato", rows[0]["tag"])
            self.assertEqual(1, meta["db_filtered_search_hits"])

    def test_loading_missing_database_creates_empty_archive(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "new" / "scraper.db"

            rows, meta = load_vinted_rows(db_path, limit=100)

            self.assertTrue(db_path.exists())
            self.assertEqual([], rows)
            self.assertTrue(meta["db_created"])
            self.assertEqual(0, meta["db_total_items"])


if __name__ == "__main__":
    unittest.main()
