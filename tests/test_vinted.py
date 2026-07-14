import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch

from scraper_app.sources.vinted import (
    _build_vinted_total,
    _build_detached_vinted_browser_command,
    _build_detached_vinted_browser_profile,
    _card_payload_to_row,
    _detach_vinted_browser_if_requested,
    _extract_vinted_description_from_body_text,
    _extract_vinted_primary_price,
    _read_vinted_published_text,
    _extract_vinted_shipping_price_text,
    _keep_browser_open,
    _prioritize_vinted_rows,
    _wait_for_vinted_login_if_needed,
    _vinted_timing_config,
    build_vinted_page_url,
    build_vinted_search_url,
    classify_vinted_evaluation,
    extract_vinted_page_number,
    extract_vinted_search_term,
    parse_vinted_favorite_count,
    parse_vinted_price,
)
from scraper_app.vinted_database import load_vinted_rows, save_vinted_rows


class VintedTests(unittest.TestCase):
    def test_search_url_and_term(self) -> None:
        url = build_vinted_search_url("macbook pro")

        self.assertEqual("https://www.vinted.it/catalog?search_text=macbook+pro", url)
        self.assertEqual("macbook pro", extract_vinted_search_term(url))

    def test_vinted_page_url_helpers(self) -> None:
        base_url = "https://www.vinted.it/catalog?search_text=magliettina&search_id=1033594103"

        self.assertEqual(1, extract_vinted_page_number(base_url))
        self.assertEqual(3, extract_vinted_page_number(f"{base_url}&page=3"))
        self.assertEqual(
            f"{base_url}&page=2",
            build_vinted_page_url(base_url, 2),
        )
        self.assertEqual(base_url, build_vinted_page_url(f"{base_url}&page=4", 1))

    def test_detached_vinted_browser_command_uses_custom_profile_copy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_root = Path(temp_dir) / "profile-root"
            (source_root / "Default").mkdir(parents=True)
            (source_root / "Local State").write_text("{}", encoding="utf-8")
            (source_root / "Default" / "Preferences").write_text("{}", encoding="utf-8")
            config = {
                "_resolved_browser_profile_root": str(source_root),
                "browser_profile_directory": "Default",
                "keep_browser_open": True,
            }

            mode, detached_root, profile_directory = _build_detached_vinted_browser_profile(config)
            command = _build_detached_vinted_browser_command(
                "https://www.vinted.it/catalog?search_text=macbook",
                config,
                0,
            )
            detached_root_from_command = command[command.index("--browser-user-data-dir") + 1]

            self.assertEqual("profilo_personalizzato", mode)
            self.assertEqual("Default", profile_directory)
            self.assertNotEqual(str(source_root), detached_root)
            self.assertTrue((Path(detached_root) / "Local State").exists())
            self.assertIn("--browser-mode", command)
            self.assertIn("profilo_personalizzato", command)
            self.assertTrue((Path(detached_root_from_command) / "Local State").exists())

    @patch("scraper_app.sources.vinted.subprocess.Popen")
    @patch("scraper_app.sources.vinted.get_active_vinted_browser_session")
    def test_detached_vinted_browser_does_not_spawn_if_already_registered(self, mocked_active_session, mocked_popen) -> None:
        mocked_active_session.return_value = {
            "pid": 12345,
            "url": "https://www.vinted.it/catalog?search_text=macbook",
        }

        _detach_vinted_browser_if_requested(
            object(),
            {
                "keep_browser_open": True,
                "keep_open_seconds": 0,
                "search_url": "https://www.vinted.it/catalog?search_text=macbook",
            },
        )

        mocked_popen.assert_not_called()

    @patch("scraper_app.sources.vinted.subprocess.Popen")
    @patch("scraper_app.sources.vinted.try_reuse_running_chrome")
    @patch("scraper_app.sources.vinted.get_active_vinted_browser_session", return_value=None)
    def test_detached_vinted_browser_does_not_spawn_if_running_chrome_is_reused(
        self,
        _mocked_active_session,
        mocked_reuse_chrome,
        mocked_popen,
    ) -> None:
        mocked_reuse_chrome.return_value = {"reused": True, "action": "reused_matching_tab"}

        _detach_vinted_browser_if_requested(
            object(),
            {
                "keep_browser_open": True,
                "keep_open_seconds": 0,
                "search_url": "https://www.vinted.it/catalog?search_text=macbook",
            },
        )

        mocked_popen.assert_not_called()

    @patch("scraper_app.sources.vinted.time.sleep")
    @patch("scraper_app.sources.vinted.emit_vinted_login_required_signal")
    @patch("scraper_app.sources.vinted.emit_vinted_access_signal")
    @patch("scraper_app.sources.vinted.read_vinted_access_status")
    @patch("scraper_app.sources.vinted.consume_stop_after_current_item_request", return_value=False)
    @patch("scraper_app.sources.vinted.consume_vinted_login_confirmed_request")
    def test_wait_for_vinted_login_rechecks_after_user_confirmation(
        self,
        mocked_confirmed,
        _mocked_stop,
        mocked_read_status,
        _mocked_emit_access,
        mocked_emit_required,
        _mocked_sleep,
    ) -> None:
        mocked_confirmed.side_effect = [False, True]
        mocked_read_status.return_value = {
            "marker_present": True,
            "expected_alt": "bonaccarla",
            "current_url": "https://www.vinted.it/catalog",
            "checked_at": "2026-07-14T10:00:00",
        }

        result = _wait_for_vinted_login_if_needed(
            object(),
            {
                "marker_present": False,
                "expected_alt": "bonaccarla",
                "current_url": "https://www.vinted.it/catalog",
                "checked_at": "2026-07-14T09:59:00",
            },
        )

        self.assertTrue(result["marker_present"])
        self.assertEqual(1, mocked_emit_required.call_count)

    def test_card_payload_extracts_name_price_and_clean_link(self) -> None:
        row = _card_payload_to_row(
            {
                "link": "https://www.vinted.it/items/123456-macbook-pro?referrer=catalog",
                "title": "Apple MacBook Pro 13",
                "price": "450,00 â‚¬",
                "favorite_count_text": "34",
                "raw_text": "Apple MacBook Pro 13 450,00 â‚¬",
            },
            search_term="macbook",
            search_url="https://www.vinted.it/catalog?search_text=macbook",
        )

        self.assertEqual("123456", row["item_id"])
        self.assertEqual("Apple MacBook Pro 13", row["name"])
        self.assertEqual(450.0, row["price_value"])
        self.assertEqual("450,00 €", row["total_price"])
        self.assertEqual(450.0, row["total_price_value"])
        self.assertEqual("https://www.vinted.it/items/123456-macbook-pro", row["link"])
        self.assertEqual("", row["tag"])
        self.assertEqual(34, row["favorite_count"])
        self.assertEqual("da valutare", row["evaluation_label"])
        self.assertFalse(row["has_ricercato_badge"])

    def test_card_payload_marks_ricercato_badge(self) -> None:
        row = _card_payload_to_row(
            {
                "link": "https://www.vinted.it/items/9391411280-macbook-pro",
                "title": "Apple MacBook Pro 14",
                "price": "999,00 â‚¬",
                "favorite_count_text": "34",
                "secondary_badge_text": "Ricercato",
                "raw_text": "Ricercato Apple MacBook Pro 14 999,00 â‚¬",
            },
            search_term="macbook",
            search_url="https://www.vinted.it/catalog?search_text=macbook",
        )

        self.assertTrue(row["has_ricercato_badge"])
        self.assertEqual("ricercato", row["tag"])
        self.assertEqual(34, row["favorite_count"])
        self.assertEqual("da valutare assolutamente", row["evaluation_label"])
        self.assertEqual("Ricercato", row["secondary_badge_text"])

    def test_prioritize_vinted_rows_moves_stronger_signals_to_top(self) -> None:
        rows = [
            {"item_id": "1", "name": "Later", "has_ricercato_badge": False, "favorite_count": 4, "evaluation_label": ""},
            {"item_id": "2", "name": "Wanted", "has_ricercato_badge": True, "favorite_count": 22, "evaluation_label": "da valutare assolutamente"},
            {"item_id": "3", "name": "Review", "has_ricercato_badge": False, "favorite_count": 18, "evaluation_label": "da valutare"},
            {"item_id": "4", "name": "Badge only", "has_ricercato_badge": True, "favorite_count": 3, "evaluation_label": ""},
        ]

        ordered = _prioritize_vinted_rows(rows)

        self.assertEqual(["2", "3", "4", "1"], [row["item_id"] for row in ordered])

    def test_price_parser_supports_italian_format(self) -> None:
        self.assertEqual(1299.99, parse_vinted_price("1.299,99 â‚¬"))
        self.assertEqual(25.0, parse_vinted_price("25 â‚¬"))

    def test_favorite_count_and_evaluation_helpers(self) -> None:
        self.assertEqual(34, parse_vinted_favorite_count("34"))
        self.assertEqual(1200, parse_vinted_favorite_count("1.200"))
        self.assertIsNone(parse_vinted_favorite_count(""))
        self.assertEqual("", classify_vinted_evaluation(15, False))
        self.assertEqual("da valutare", classify_vinted_evaluation(16, False))
        self.assertEqual("da valutare assolutamente", classify_vinted_evaluation(16, True))
        self.assertEqual("da valutare assolutamente", classify_vinted_evaluation(16, True, "1 settimana fa"))
        self.assertEqual("da valutare", classify_vinted_evaluation(16, True, "8 giorni fa"))
        self.assertEqual("da valutare", classify_vinted_evaluation(16, True, "2 settimane fa"))
        self.assertEqual("da valutare", classify_vinted_evaluation(16, True, "1 mese fa"))

    def test_vinted_timing_config_applies_slow_mode_floor(self) -> None:
        self.assertEqual((2.5, 4.0), _vinted_timing_config(True, 0.5, 1.0))
        self.assertEqual((1.0, 2.0), _vinted_timing_config(False, 1.0, 2.0))

    @patch("scraper_app.sources.vinted.time.sleep")
    @patch("scraper_app.sources.vinted.current_page_url")
    def test_keep_browser_open_waits_until_manual_close_when_seconds_zero(self, mocked_current_url, mocked_sleep) -> None:
        mocked_current_url.side_effect = [
            "https://www.vinted.it/items/1",
            "https://www.vinted.it/items/1",
            "",
            "",
            "",
        ]

        _keep_browser_open(object(), 0)

        self.assertEqual(5, mocked_current_url.call_count)
        self.assertEqual(5, mocked_sleep.call_count)

    @patch("scraper_app.sources.vinted.time.sleep")
    @patch("scraper_app.sources.vinted.current_page_url", return_value="https://www.vinted.it/items/1")
    @patch("scraper_app.sources.vinted.time.monotonic", side_effect=[10.0, 10.0, 11.0, 12.0])
    def test_keep_browser_open_waits_until_deadline_when_seconds_positive(self, mocked_monotonic, mocked_current_url, mocked_sleep) -> None:
        _keep_browser_open(object(), 2)

        self.assertEqual(2, mocked_current_url.call_count)
        self.assertEqual(2, mocked_sleep.call_count)
        self.assertEqual(4, mocked_monotonic.call_count)

    def test_shipping_and_total_extractors(self) -> None:
        self.assertEqual("5,49 €", _extract_vinted_shipping_price_text("Spedizione da 5,49 € con corriere"))
        self.assertEqual(("455,49 EUR", 455.49), _build_vinted_total("450,00 €", "5,49 €"))
        self.assertEqual(("450,00 €", 450.0), _build_vinted_total("450,00 €", ""))

    def test_primary_price_prefers_value_immediately_before_incl(self) -> None:
        page_text = (
            "Magliettina vintage 3,00 € 4,90 € incl. Protezione acquisti dettagli spedizione"
        )
        self.assertEqual("4,90 €", _extract_vinted_primary_price(page_text, "Magliettina vintage"))

    def test_primary_price_prefers_value_before_include_protezione_acquisti(self) -> None:
        page_text = (
            "MacBook Air 1.199,00 € 1.254,70 € Include la Protezione acquisti e assistenza"
        )
        self.assertEqual("1.254,70 €", _extract_vinted_primary_price(page_text, "MacBook Air"))

    def test_read_vinted_published_text_from_page_text(self) -> None:
        page_text = "Titolo annuncio Caricato 3 ore fa Marca Apple"
        driver = Mock()
        driver.run_js.return_value = ""
        self.assertEqual("3 ore fa", _read_vinted_published_text(driver, page_text))

    def test_description_parser_uses_text_after_descrizione_heading(self) -> None:
        body_text = "\n".join(
            [
                "Titolo annuncio",
                "Descrizione",
                "MacBook Air in ottime condizioni, batteria al 92%.",
                "Consegna a mano a Roma.",
                "Marca",
                "Apple",
            ]
        )

        self.assertEqual(
            "MacBook Air in ottime condizioni, batteria al 92%. Consegna a mano a Roma.",
            _extract_vinted_description_from_body_text(body_text),
        )

    def test_database_deduplicates_items_and_keeps_search_tags(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "vinted.db"
            base_row = {
                "link": "https://www.vinted.it/items/123-macbook",
                "item_id": "123",
                "name": "MacBook",
                "price": "300,00 â‚¬",
                "price_value": 300.0,
                "shipping_price": "5,49 â‚¬",
                "shipping_price_value": 5.49,
                "total_price": "305,49 EUR",
                "total_price_value": 305.49,
                "offer_available": True,
                "offer_text": "Fare un'offerta",
                "favorite_count": 34,
                "evaluation_label": "da valutare",
                "currency": "EUR",
                "description": "Testo descrizione",
                "published_at": "3 ore fa",
                "raw_text": "MacBook 300,00 â‚¬",
                "extracted_at": "2026-07-01T10:00:00",
                "search_url": "https://www.vinted.it/catalog?search_text=macbook",
            }

            first = save_vinted_rows([{**base_row, "search_term": "macbook"}], db_path)
            second = save_vinted_rows([{**base_row, "search_term": "macbook pro"}], db_path)
            third = save_vinted_rows([{**base_row, "search_term": "macbook"}], db_path)
            fourth = save_vinted_rows([{**{k: v for k, v in base_row.items() if k != "description"}, "search_term": "macbook"}], db_path)

            self.assertEqual(1, first["new_items"])
            self.assertEqual(1, second["updated_items"])
            self.assertEqual(1, second["new_search_hits"])
            self.assertEqual(1, third["updated_search_hits"])
            self.assertEqual(1, fourth["updated_items"])
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
            self.assertEqual(3, macbook_times)
            self.assertEqual([""], tags)

            rows, meta = load_vinted_rows(db_path, search_term="macbook", limit=10)
            self.assertEqual(2, len(rows))
            self.assertEqual(1, meta["db_total_items"])
            self.assertEqual(2, meta["db_total_search_hits"])
            macbook_row = next(row for row in rows if row["search_term"] == "macbook")
            self.assertEqual(3, macbook_row["times_seen"])
            self.assertEqual("MacBook", macbook_row["name"])
            self.assertEqual("", macbook_row["tag"])
            self.assertEqual("Testo descrizione", macbook_row["description"])
            self.assertEqual("3 ore fa", macbook_row["published_at"])
            self.assertEqual("5,49 â‚¬", macbook_row["shipping_price"])
            self.assertEqual(305.49, macbook_row["total_price_value"])
            self.assertTrue(macbook_row["offer_available"])
            self.assertEqual(34, macbook_row["favorite_count"])
            self.assertEqual("da valutare", macbook_row["evaluation_label"])

            filtered_rows, filtered_meta = load_vinted_rows(db_path, tag_filter="ricercato", limit=10)
            self.assertEqual(0, len(filtered_rows))
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
                        'https://www.vinted.it/items/456-air', '456', 'MacBook Air', '250,00 Ã¢â€šÂ¬',
                        250.0, 'EUR', 'MacBook Air 250,00 Ã¢â€šÂ¬', '2026-07-01T10:00:00', '2026-07-01T10:00:00'
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
            self.assertIsNone(rows[0]["favorite_count"])
            self.assertEqual("", rows[0]["evaluation_label"])
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
