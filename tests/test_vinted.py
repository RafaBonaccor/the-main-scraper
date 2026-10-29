import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import ANY, Mock, patch

from botasaurus.browser import Wait

from scraper_app.discord_notifications import (
    build_vinted_profile_daily_report_discord_message,
    split_discord_message,
)
from scraper_app.sources.vinted import (
    _build_vinted_total,
    _build_detached_vinted_browser_command,
    _build_detached_vinted_browser_profile,
    _build_vinted_detail_row,
    _card_payload_to_row,
    _detach_vinted_browser_if_requested,
    _enrich_vinted_priority_rows,
    _extract_vinted_description_from_body_text,
    _extract_vinted_base_price,
    _extract_vinted_primary_price,
    _click_vinted_favourite_button,
    _normalize_vinted_max_price,
    _notify_vinted_deal_immediately,
    _persist_vinted_progress_results,
    _read_vinted_published_text,
    _extract_vinted_shipping_price_text,
    _keep_browser_open,
    _open_vinted_next_page,
    _wait_for_vinted_detail_page_ready,
    _prioritize_vinted_rows,
    _read_vinted_next_page_target,
    _read_vinted_favourite_button_state,
    _vinted_row_matches_known_item_keys,
    _vinted_row_matches_max_price,
    _vinted_row_matches_min_price_for_deal_hunter,
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
from scraper_app.vinted_tabs import navigate_vinted_status_tab, restore_vinted_primary_tab
from scraper_app.sources.vinted_deal_sales import check_due_vinted_deal_sales_with_driver
from scraper_app.sources.vinted_profile import (
    _check_profile_listing_status,
    _filter_vinted_profile_rows_by_category,
    _read_all_vinted_profile_items_via_browser_session,
    _vinted_profile_rows_missing_from_current,
    _wait_for_vinted_profile_dom_ready,
    normalize_vinted_profile_urls,
)
from scraper_app.runner import run_scraper
from scraper_app.vinted_database import (
    annotate_rows_with_vinted_offer_history,
    build_vinted_item_identity_keys,
    classify_vinted_competitor_status,
    delete_vinted_search_run,
    delete_vinted_item_by_link,
    list_vinted_search_runs,
    load_vinted_completed_detail_rows,
    load_vinted_sold_deal_identity_keys,
    load_vinted_known_item_keys,
    load_vinted_profile_daily_changes,
    load_vinted_profile_known_items,
    load_vinted_profile_latest_snapshot_items,
    load_due_vinted_deal_sale_checks,
    load_vinted_deal_sales,
    load_vinted_submitted_offer_keys,
    load_vinted_rows,
    parse_vinted_precise_listing_age_seconds,
    save_vinted_profile_snapshot,
    save_vinted_offer_results,
    save_vinted_deal_notifications,
    save_vinted_deal_sale_check,
    summarize_vinted_deal_sales,
    save_vinted_rows,
    update_vinted_competitors_for_sellers,
    update_vinted_search_run,
)


class VintedTests(unittest.TestCase):
    def test_profile_api_pagination_collects_all_pages(self) -> None:
        class FakeDriver:
            def __init__(self) -> None:
                self.calls: list[dict] = []

            def run_js(self, _script: str, args: dict | None = None) -> dict:
                if args is None:
                    return {"csrf": "test-csrf", "anonId": "test-anon"}
                self.calls.append(args)
                page = 2 if "page=2" in args["endpoint"] else 1
                start = 97 if page == 2 else 1
                end = 98 if page == 2 else 97
                items = [
                    {
                        "id": str(index),
                        "title": f"Item {index}",
                        "price": "10.00",
                        "currency": "EUR",
                        "url": f"/items/{index}-item-{index}",
                        "photo": {"url": f"https://images.example/{index}.jpg"},
                    }
                    for index in range(start, end)
                ]
                return {
                    "status": 200,
                    "text": json.dumps({"items": items, "pagination": {"current_page": page, "total_pages": 2}}),
                }

        driver = FakeDriver()
        rows = _read_all_vinted_profile_items_via_browser_session(driver, member_id="306388399", max_items=0)
        self.assertEqual(97, len(rows))
        self.assertEqual(2, len(driver.calls))
        self.assertEqual("97", rows[-1]["item_id"])

    def test_detail_inspection_uses_separate_tab_and_restores_search_tab(self) -> None:
        search_tab = Mock()
        search_tab.target.target_id = "search-target"
        status_tab = Mock()
        browser_session = Mock()
        browser_session._agent_lab_vinted_primary_tab = None
        browser_session._agent_lab_vinted_status_tab = None
        driver = Mock()
        driver._browser = browser_session
        driver._tab = search_tab
        driver.config.arguments = ["--start-minimized"]
        driver.open_link_in_new_tab.return_value = status_tab

        self.assertTrue(navigate_vinted_status_tab(driver, "https://www.vinted.it/items/123-test"))
        self.assertIs(driver._tab, status_tab)
        status_tab.minimize.assert_called_once_with()
        driver.open_link_in_new_tab.assert_called_once_with(
            "about:blank",
            wait=None,
            timeout=1,
        )

        self.assertTrue(navigate_vinted_status_tab(driver, "https://www.vinted.it/items/456-test"))
        self.assertEqual(2, status_tab.send.call_count)
        status_tab.minimize.assert_called_once_with()
        self.assertIs(driver._tab, status_tab)

        restore_vinted_primary_tab(driver)
        self.assertIs(driver._tab, search_tab)
        search_tab.minimize.assert_not_called()
        browser_session.connection.send.assert_not_called()

    def test_detail_tab_falls_back_to_original_tab_when_creation_fails(self) -> None:
        search_tab = Mock()
        browser_session = Mock()
        browser_session._agent_lab_vinted_primary_tab = None
        browser_session._agent_lab_vinted_status_tab = None
        driver = Mock()
        driver._browser = browser_session
        driver._tab = search_tab
        driver.open_link_in_new_tab.side_effect = RuntimeError("tab creation failed")

        self.assertFalse(navigate_vinted_status_tab(driver, "https://www.vinted.it/items/123-test"))
        self.assertIs(driver._tab, search_tab)

    @patch("scraper_app.sources.vinted_deal_sales.time.sleep")
    @patch("scraper_app.sources.vinted_deal_sales.restore_vinted_primary_tab")
    @patch("scraper_app.sources.vinted_deal_sales.save_vinted_deal_sale_check", return_value={})
    @patch("scraper_app.sources.vinted_deal_sales._sold_status_from_page", return_value={"sold": False})
    @patch("scraper_app.sources.vinted_deal_sales.navigate_vinted_status_tab", return_value=True)
    @patch(
        "scraper_app.sources.vinted_deal_sales.load_due_vinted_deal_sale_checks",
        return_value=[
            {
                "link": "https://www.vinted.it/items/123-test",
                "name": "Test charm",
                "deal_first_seen_at": "2026-10-24T10:00:00",
            }
        ],
    )
    def test_continuous_sale_checks_use_the_secondary_status_tab(
        self,
        load_due,
        navigate_status_tab,
        _read_status,
        _save_check,
        restore_primary,
        _sleep,
    ) -> None:
        result = check_due_vinted_deal_sales_with_driver(
            Mock(),
            db_path="data/scraper.db",
            interval_seconds=1800,
            max_items=40,
            discord_webhook_url="",
        )

        self.assertEqual(1, result["checked_count"])
        self.assertEqual(0, result["sold_count"])
        navigate_status_tab.assert_called_once()
        restore_primary.assert_called_once()
        self.assertEqual(40, load_due.call_args.kwargs["limit"])

    def test_precise_listing_age_parser_uses_hours_but_rejects_day_labels(self) -> None:
        self.assertEqual(3_600, parse_vinted_precise_listing_age_seconds("1 ora fa"))
        self.assertEqual(7_200, parse_vinted_precise_listing_age_seconds("Caricato 2 ore fa"))
        self.assertEqual(1_800, parse_vinted_precise_listing_age_seconds("30 minuti fa"))
        self.assertEqual(86_400, parse_vinted_precise_listing_age_seconds("24 ore fa"))
        self.assertIsNone(parse_vinted_precise_listing_age_seconds("un giorno fa"))
        self.assertIsNone(parse_vinted_precise_listing_age_seconds("2 giorni fa"))

    def test_sold_deal_uses_persisted_precise_listing_age(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "scraper.db"
            row = {
                "item_id": "8999",
                "name": "Fast charm",
                "link": "https://www.vinted.it/items/8999-fast-charm",
                "notification_sent_at": "2026-10-07T10:00:00",
            }
            save_vinted_deal_notifications([row], db_path=db_path, webhook_target="https://discord.test/webhook")
            save_vinted_deal_sale_check(
                {
                    **row,
                    "deal_sale_duration_seconds": 7_200,
                    "deal_sale_duration_source": "listing_age",
                    "deal_listing_age_text": "2 ore fa",
                },
                sold=True,
                checked_at="2026-10-07T15:00:00",
                db_path=db_path,
            )
            sales = load_vinted_deal_sales(db_path)

        self.assertEqual(7_200, sales[0]["deal_sold_after_seconds"])
        self.assertEqual("2h 0m", sales[0]["deal_sold_after_text"])
        self.assertEqual("listing_age", sales[0]["deal_sale_duration_source"])

    def test_daily_report_includes_every_sold_deal_across_discord_messages(self) -> None:
        sold_deals = [
            {
                "name": f"Affare numero {index}",
                "price": "19,00 €",
                "deal_sold_after_text": "2h 10m",
                "link": f"https://www.vinted.it/items/{1000 + index}-affare-{index}",
            }
            for index in range(1, 35)
        ]
        report = build_vinted_profile_daily_report_discord_message(
            {"profile_count": 2},
            [],
            deal_sales=sold_deals,
            deal_sales_summary={"average_text": "2h 10m", "median_text": "2h 10m"},
        )
        messages = split_discord_message(report, max_length=700)

        self.assertGreater(len(messages), 1)
        self.assertTrue(all(len(message) <= 2000 for message in messages))
        self.assertNotIn("...altri", report)
        for index in range(1, 35):
            self.assertIn(f"/{1000 + index}-affare-{index}", report)

    def test_notified_deal_is_tracked_and_sold_state_is_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "scraper.db"
            row = {
                "item_id": "9001",
                "name": "Charm test",
                "price": "12,00 €",
                "link": "https://www.vinted.it/items/9001-charm-test",
                "notification_sent_at": "2026-10-07T10:00:00",
            }
            save_vinted_deal_notifications([row], db_path=db_path, webhook_target="https://discord.test/webhook")
            due = load_due_vinted_deal_sale_checks(db_path, due_before="2026-10-07T10:01:00")
            self.assertEqual(1, len(due))
            saved = save_vinted_deal_sale_check(
                due[0], sold=True, checked_at="2026-10-07T12:15:00", db_path=db_path
            )
            self.assertEqual("sold", saved["status"])
            self.assertEqual("2026-10-07T12:15:00", saved["sold_at"])
            self.assertEqual([], load_due_vinted_deal_sale_checks(db_path, due_before="2026-10-08T00:00:00"))
            self.assertIn("id:9001", load_vinted_sold_deal_identity_keys(db_path))
            self.assertIn("link:https://www.vinted.it/items/9001-charm-test", load_vinted_sold_deal_identity_keys(db_path))
            sales = load_vinted_deal_sales(db_path, since_at="2026-10-07T00:00:00")
            self.assertEqual(1, len(sales))
            self.assertEqual("2h 15m", sales[0]["deal_sold_after_text"])
            self.assertEqual("2h 15m", summarize_vinted_deal_sales(sales)["average_text"])

    def test_sold_deal_is_not_reopened_for_detail_enrichment(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "scraper.db"
            row = {
                "item_id": "9001",
                "name": "Charm sold",
                "link": "https://www.vinted.it/items/9001-charm-sold",
            }
            save_vinted_deal_notifications([row], db_path=db_path, webhook_target="https://discord.test/webhook")
            save_vinted_deal_sale_check(row, sold=True, checked_at="2026-10-07T12:15:00", db_path=db_path)
            driver = Mock()

            meta = _enrich_vinted_priority_rows(
                driver,
                [{**row, "deal_hunter_candidate": True, "deal_hunter_match": False}],
                {"db_path": str(db_path), "deal_hunter_enabled": True},
            )

            driver.get.assert_not_called()
            self.assertEqual(1, meta["skipped_sold_count"])

    def test_deal_sale_checks_ignore_active_deals_older_than_tracking_window(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "scraper.db"
            row = {
                "item_id": "9002",
                "name": "Old charm",
                "link": "https://www.vinted.it/items/9002-old-charm",
                "notification_sent_at": "2026-10-01T10:00:00",
            }
            save_vinted_deal_notifications([row], db_path=db_path, webhook_target="https://discord.test/webhook")

            due = load_due_vinted_deal_sale_checks(
                db_path,
                due_before="2026-10-08T10:00:00",
                first_seen_after="2026-10-05T10:00:00",
            )

            self.assertEqual([], due)

    def test_page_not_found_listing_is_archived_without_losing_history(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "scraper.db"
            row = {
                "item_id": "9003",
                "name": "Removed charm",
                "link": "https://www.vinted.it/items/9003-removed-charm",
                "search_term": "charm",
            }
            save_vinted_rows([row], db_path=db_path)
            save_vinted_deal_notifications([row], db_path=db_path, webhook_target="https://discord.test/webhook")

            result = delete_vinted_item_by_link(
                "https://www.vinted.it/items/9003-different-slug",
                db_path=db_path,
            )

            self.assertEqual(0, result["deleted"])
            self.assertEqual(1, result["archived"])
            self.assertEqual("9003", result["item_id"])
            self.assertIn("id:9003", load_vinted_known_item_keys(db_path))
            with closing(sqlite3.connect(db_path)) as connection:
                self.assertEqual(1, connection.execute("SELECT COUNT(*) FROM vinted_items").fetchone()[0])
                self.assertEqual(1, connection.execute("SELECT COUNT(*) FROM vinted_deal_notifications").fetchone()[0])
                self.assertEqual(1, connection.execute("SELECT COUNT(*) FROM vinted_unavailable_items").fetchone()[0])
                self.assertEqual(1, connection.execute("SELECT COUNT(*) FROM vinted_item_events").fetchone()[0])
                status, availability = connection.execute(
                    "SELECT status, availability_status FROM vinted_deal_sale_tracking"
                ).fetchone()
                self.assertEqual("unavailable", status)
                self.assertEqual("unavailable", availability)

    def test_page_not_found_keeps_confirmed_sale_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "scraper.db"
            row = {
                "item_id": "9004",
                "name": "Sold charm",
                "link": "https://www.vinted.it/items/9004-sold-charm",
                "notification_sent_at": "2026-10-01T10:00:00",
            }
            save_vinted_deal_notifications([row], db_path=db_path, webhook_target="https://discord.test/webhook")
            save_vinted_deal_sale_check(
                row,
                sold=True,
                checked_at="2026-10-01T12:00:00",
                db_path=db_path,
            )

            delete_vinted_item_by_link(row["link"], db_path=db_path)

            with closing(sqlite3.connect(db_path)) as connection:
                status, availability, sold_at = connection.execute(
                    "SELECT status, availability_status, sold_at FROM vinted_deal_sale_tracking"
                ).fetchone()
                self.assertEqual("sold", status)
                self.assertEqual("unavailable", availability)
                self.assertEqual("2026-10-01T12:00:00", sold_at)

    @patch("scraper_app.sources.vinted._notify_new_vinted_deals")
    def test_qualified_deal_is_notified_before_full_run_finishes(self, mocked_notify) -> None:
        mocked_notify.return_value = {"discord_deal_notifications_sent": 1, "discord_deal_notifications_failed": 0}

        _notify_vinted_deal_immediately(
            {"link": "https://www.vinted.it/items/1", "deal_hunter_match": True},
            {"discord_deal_notifications": True},
        )

        mocked_notify.assert_called_once()

    def test_normalize_vinted_profile_urls_accepts_multiple_formats(self) -> None:
        self.assertEqual(
            [
                "https://www.vinted.it/member/262102939",
                "https://www.vinted.it/member/123456789",
            ],
            normalize_vinted_profile_urls("262102939\nhttps://www.vinted.it/member/123456789,262102939"),
        )
        self.assertEqual(
            [
                "https://www.vinted.it/member/262102939",
                "https://www.vinted.it/member/167398536",
            ],
            normalize_vinted_profile_urls(
                ["https://www.vinted.it/member/262102939\nhttps://www.vinted.it/member/167398536"]
            ),
        )
        self.assertEqual(
            [
                "https://www.vinted.it/member/262102939",
                "https://www.vinted.it/member/167398536",
            ],
            normalize_vinted_profile_urls(
                "https://www.vinted.it/member/262102939/nhttps://www.vinted.it/member/167398536"
            ),
        )

    def test_profile_missing_rows_include_unchecked_gone_and_skip_verified_terminal(self) -> None:
        current = [{"link": "https://www.vinted.it/items/1-active"}]
        history = [
            {"link": "https://www.vinted.it/items/1-active", "profile_item_status": "still_active"},
            {"link": "https://www.vinted.it/items/2-gone", "profile_item_status": "gone"},
            {"link": "https://www.vinted.it/items/3-gone-checked", "profile_item_status": "gone", "profile_page_status": "active", "profile_status_checked_at": "2026-10-28T10:00:00"},
            {"link": "https://www.vinted.it/items/4-sold", "profile_item_status": "sold"},
            {"link": "https://www.vinted.it/items/5-unavailable", "profile_item_status": "unavailable"},
        ]
        self.assertEqual(
            ["https://www.vinted.it/items/2-gone"],
            [row["link"] for row in _vinted_profile_rows_missing_from_current(current_rows=current, historical_rows=history)],
        )

    def test_profile_category_filter_matches_category_or_legacy_item_title(self) -> None:
        rows = [
            {"link": "https://www.vinted.it/items/1", "category": "Charm e ciondoli", "name": "Charm Disney"},
            {"link": "https://www.vinted.it/items/2", "category": "Altro", "name": "Charm Pandora in argento"},
            {"link": "https://www.vinted.it/items/3", "category": "Anelli", "name": "Anello argento"},
        ]
        self.assertEqual(
            ["https://www.vinted.it/items/2"],
            [row["link"] for row in _filter_vinted_profile_rows_by_category(rows, ["pandora"])],
        )
        self.assertEqual(
            ["https://www.vinted.it/items/1"],
            [row["link"] for row in _filter_vinted_profile_rows_by_category(rows, ["charm e ciondoli"])],
        )

    def test_profile_listing_check_marks_missing_live_listing_gone_not_sold(self) -> None:
        class FakeDriver:
            def get(self, *_args, **_kwargs):
                return None

            def run_js(self, *_args, **_kwargs):
                return {"sold": False, "page_not_found": False, "current_url": "https://www.vinted.it/items/123-charm", "published_text": ""}

        checked = _check_profile_listing_status(
            FakeDriver(),
            [{"link": "https://www.vinted.it/items/123-charm", "item_id": "123"}],
            page_settle_seconds=0.25,
        )
        self.assertEqual("gone", checked[0]["profile_item_status"])
        self.assertEqual("active", checked[0]["profile_page_status"])

    def test_save_vinted_profile_snapshot_tracks_new_and_gone_items(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "scraper.db"
            profile_url = "https://www.vinted.it/member/262102939"

            first = save_vinted_profile_snapshot(
                profile_url,
                [
                    {"link": "https://www.vinted.it/items/100-a", "name": "A", "price": "10,00 €"},
                    {"link": "https://www.vinted.it/items/200-b", "name": "B", "price": "20,00 €"},
                ],
                db_path=db_path,
            )
            with closing(sqlite3.connect(db_path)) as connection:
                connection.execute("UPDATE vinted_profile_snapshots SET checked_at = '2026-01-01T10:00:00'")
                connection.commit()
            second_rows = [
                {"link": "https://www.vinted.it/items/200-b", "name": "B", "price": "20,00 €"},
                {"link": "https://www.vinted.it/items/300-c", "name": "C", "price": "30,00 €"},
            ]
            second = save_vinted_profile_snapshot(profile_url, second_rows, db_path=db_path)

        self.assertEqual(2, first["profile_new_count"])
        self.assertEqual(0, first["profile_gone_count"])
        self.assertEqual(1, second["profile_new_count"])
        self.assertEqual(1, second["profile_gone_count"])
        self.assertEqual("still_active", second["profile_current_rows"][0]["profile_item_status"])
        self.assertEqual("new", second["profile_current_rows"][1]["profile_item_status"])
        self.assertEqual("100", second["profile_gone_rows"][0]["item_id"])
        self.assertEqual("A", second["profile_gone_rows"][0]["name"])
        self.assertGreater(second["profile_gone_rows"][0]["profile_sold_after_seconds"], 0)
        self.assertTrue(second["profile_gone_rows"][0]["profile_sold_after_text"])

    def test_save_vinted_profile_snapshot_counts_explicitly_sold_items_as_not_active(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "scraper.db"
            profile_url = "https://www.vinted.it/member/262102939"
            save_vinted_profile_snapshot(
                profile_url,
                [
                    {"link": "https://www.vinted.it/items/100-a", "name": "A", "price": "10,00 €"},
                    {"link": "https://www.vinted.it/items/200-b", "name": "B", "price": "20,00 €"},
                ],
                db_path=db_path,
            )
            sold = save_vinted_profile_snapshot(
                profile_url,
                [
                    {"link": "https://www.vinted.it/items/100-a", "name": "A", "price": "10,00 €", "profile_item_status": "sold"},
                    {"link": "https://www.vinted.it/items/200-b", "name": "B", "price": "20,00 €", "profile_item_status": "active"},
                ],
                db_path=db_path,
            )
            with closing(sqlite3.connect(db_path)) as connection:
                record = connection.execute(
                    "SELECT status, present FROM vinted_profile_snapshot_items WHERE snapshot_id = ? AND item_id = '100'",
                    (sold["profile_snapshot_id"],),
                ).fetchone()

        self.assertEqual(1, sold["profile_item_count"])
        self.assertEqual(1, sold["profile_sold_count"])
        self.assertEqual(("sold", 0), tuple(record))

    def test_save_profile_missing_live_listing_as_gone_and_preserves_detail_check(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "scraper.db"
            profile_url = "https://www.vinted.it/member/262102939"
            save_vinted_profile_snapshot(
                profile_url,
                [
                    {"link": "https://www.vinted.it/items/100-a", "name": "A", "price": "10,00 €"},
                    {"link": "https://www.vinted.it/items/200-b", "name": "B", "price": "20,00 €"},
                ],
                db_path=db_path,
            )
            result = save_vinted_profile_snapshot(
                profile_url,
                [
                    {"link": "https://www.vinted.it/items/200-b", "name": "B", "price": "20,00 €"},
                    {
                        "link": "https://www.vinted.it/items/100-a",
                        "name": "A",
                        "price": "10,00 €",
                        "profile_item_status": "gone",
                        "profile_page_status": "active",
                        "profile_status_checked_at": "2026-10-29T10:00:00",
                    },
                ],
                db_path=db_path,
            )
            with closing(sqlite3.connect(db_path)) as connection:
                record = connection.execute(
                    "SELECT status, present, snapshot_json FROM vinted_profile_snapshot_items WHERE snapshot_id = ? AND item_id = '100'",
                    (result["profile_snapshot_id"],),
                ).fetchone()

        self.assertEqual("gone", record[0])
        self.assertEqual(0, record[1])
        payload = json.loads(record[2])
        self.assertEqual("active", payload["profile_page_status"])
        self.assertEqual("2026-10-29T10:00:00", payload["profile_status_checked_at"])

    def test_load_vinted_profile_daily_changes_reads_new_and_gone_for_day(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "scraper.db"
            profile_url = "https://www.vinted.it/member/262102939"
            save_vinted_profile_snapshot(
                profile_url,
                [
                    {"link": "https://www.vinted.it/items/100-a", "name": "A", "price": "10,00 €"},
                    {"link": "https://www.vinted.it/items/200-b", "name": "B", "price": "20,00 €"},
                ],
                db_path=db_path,
            )
            with closing(sqlite3.connect(db_path)) as connection:
                connection.execute("UPDATE vinted_profile_snapshots SET checked_at = '2026-10-07T09:00:00'")
                connection.commit()
            save_vinted_profile_snapshot(
                profile_url,
                [
                    {"link": "https://www.vinted.it/items/200-b", "name": "B", "price": "20,00 €"},
                    {"link": "https://www.vinted.it/items/300-c", "name": "C", "price": "30,00 €"},
                ],
                db_path=db_path,
            )
            with closing(sqlite3.connect(db_path)) as connection:
                connection.execute(
                    "UPDATE vinted_profile_snapshots SET checked_at = '2026-10-07T12:00:00' WHERE id = (SELECT MAX(id) FROM vinted_profile_snapshots)"
                )
                connection.commit()

            rows = load_vinted_profile_daily_changes(
                db_path=db_path,
                since_at="2026-10-07T00:00:00",
                until_at="2026-10-07T23:59:59",
                profile_urls=[profile_url],
            )

        status_pairs = {(row["item_id"], row["profile_item_status"]) for row in rows}
        self.assertIn(("100", "gone"), status_pairs)
        self.assertIn(("300", "new"), status_pairs)

    def test_save_vinted_profile_snapshot_ignores_likely_incomplete_reads(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "scraper.db"
            profile_url = "https://www.vinted.it/member/167398536"
            full_rows = [
                {
                    "link": f"https://www.vinted.it/items/{1000 + index}-item-{index}",
                    "name": f"Item {index}",
                    "price": "10,00 €",
                }
                for index in range(60)
            ]
            partial_rows = full_rows[:20]

            save_vinted_profile_snapshot(profile_url, full_rows, db_path=db_path)
            # Emulate a legacy false-empty snapshot that marked all items gone.
            with closing(sqlite3.connect(db_path)) as connection:
                cursor = connection.execute(
                    "INSERT INTO vinted_profile_snapshots (member_id, profile_url, checked_at, item_count, new_count, gone_count, still_count) VALUES (?, ?, ?, 0, 0, ?, 0)",
                    ("167398536", profile_url, "2026-10-28T10:00:00", len(full_rows)),
                )
                legacy_snapshot_id = int(cursor.lastrowid)
                connection.executemany(
                    "INSERT INTO vinted_profile_snapshot_items (snapshot_id,member_id,profile_url,item_link,item_id,item_name,price_text,image_url,status,present,snapshot_json) VALUES (?, ?, ?, ?, ?, ?, '', '', 'gone', 0, '{}')",
                    [(legacy_snapshot_id, "167398536", profile_url, row["link"], row["link"].split("/")[-1].split("-")[0], row["name"]) for row in full_rows],
                )
                connection.commit()
            partial = save_vinted_profile_snapshot(profile_url, partial_rows, db_path=db_path)
            known = load_vinted_profile_known_items(profile_url, db_path=db_path)

            with closing(sqlite3.connect(db_path)) as connection:
                snapshot_count = connection.execute("SELECT COUNT(*) FROM vinted_profile_snapshots").fetchone()[0]

        self.assertTrue(partial["profile_snapshot_incomplete"])
        self.assertFalse(partial["profile_snapshot_saved"])
        self.assertEqual(60, partial["profile_item_count"])
        self.assertEqual(60, partial["profile_previous_item_count"])
        self.assertEqual(40, partial["profile_missing_item_count"])
        self.assertEqual(0, partial["profile_gone_count"])
        self.assertEqual(0, partial["profile_new_count"])
        self.assertEqual(2, snapshot_count)
        self.assertEqual(60, len(partial["profile_current_rows"]))
        self.assertTrue(all(row["profile_item_status"] == "still_active" for row in partial["profile_current_rows"]))
        self.assertEqual(60, len(known))

    def test_load_vinted_profile_latest_snapshot_items_uses_latest_complete_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "scraper.db"
            profile_url = "https://www.vinted.it/member/306388399"
            first_rows = [
                {"link": f"https://www.vinted.it/items/{item_id}-item", "name": f"Pandora {item_id}"}
                for item_id in ("100", "200")
            ]
            latest_rows = [first_rows[0], {"link": "https://www.vinted.it/items/300-item", "name": "Pandora 300"}]
            save_vinted_profile_snapshot(profile_url, first_rows, db_path=db_path)
            save_vinted_profile_snapshot(profile_url, latest_rows, db_path=db_path)

            loaded = load_vinted_profile_latest_snapshot_items(profile_url, db_path=db_path)

        self.assertEqual({row["link"] for row in latest_rows}, {row["link"] for row in loaded})
        self.assertNotIn("https://www.vinted.it/items/200-item", {row["link"] for row in loaded})

    def test_same_count_but_disjoint_profile_read_is_rejected_as_incomplete(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "scraper.db"
            profile_url = "https://www.vinted.it/member/306388399"
            previous_rows = [
                {"link": f"https://www.vinted.it/items/{item_id}-old", "name": f"Old {item_id}"}
                for item_id in range(100, 120)
            ]
            unrelated_rows = [
                {"link": f"https://www.vinted.it/items/{item_id}-other", "name": f"Other {item_id}"}
                for item_id in range(200, 220)
            ]
            save_vinted_profile_snapshot(profile_url, previous_rows, db_path=db_path)

            result = save_vinted_profile_snapshot(profile_url, unrelated_rows, db_path=db_path)
            latest_inventory = load_vinted_profile_latest_snapshot_items(profile_url, db_path=db_path)

        self.assertTrue(result["profile_snapshot_incomplete"])
        self.assertFalse(result["profile_snapshot_saved"])
        self.assertEqual({row["link"] for row in previous_rows}, {row["link"] for row in latest_inventory})

    def test_save_vinted_profile_snapshot_ignores_empty_read_after_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "scraper.db"
            profile_url = "https://www.vinted.it/member/167398536"
            rows = [
                {"link": f"https://www.vinted.it/items/{1000 + index}-item-{index}", "name": f"Item {index}"}
                for index in range(3)
            ]
            save_vinted_profile_snapshot(profile_url, rows, db_path=db_path)
            empty = save_vinted_profile_snapshot(profile_url, [], db_path=db_path)
            with closing(sqlite3.connect(db_path)) as connection:
                snapshot_count = connection.execute("SELECT COUNT(*) FROM vinted_profile_snapshots").fetchone()[0]

        self.assertTrue(empty["profile_snapshot_incomplete"])
        self.assertTrue(empty["profile_empty_response_guard"])
        self.assertFalse(empty["profile_snapshot_saved"])
        self.assertEqual(0, empty["profile_gone_count"])
        self.assertEqual(1, snapshot_count)

    def test_search_url_and_term(self) -> None:
        url = build_vinted_search_url("macbook pro")

        self.assertEqual("https://www.vinted.it/catalog?search_text=macbook+pro", url)
        self.assertEqual("macbook pro", extract_vinted_search_term(url))

    def test_persist_vinted_progress_results_writes_live_ui_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "ui.json"
            _persist_vinted_progress_results(
                rows=[
                    {
                        "source": "vinted",
                        "item_id": "1",
                        "link": "https://www.vinted.it/items/1",
                        "favorite_count": 72,
                        "published_at": "",
                    }
                ],
                config={
                    "search": "charm",
                    "search_term": "charm",
                    "ui_result_json": str(path),
                    "deal_hunter_enabled": True,
                    "deal_hunter_min_favorites": 70,
                    "deal_hunter_max_age_hours": 24,
                    "max_results": 25,
                },
                search_url="https://www.vinted.it/catalog?search_text=charm",
                pages_visited=[1],
                filtered_out_known_items=0,
                filtered_out_by_price=0,
                cookie_action="",
                access_status={"marker_present": True},
                enrichment_meta={"enriched_count": 0, "demoted_count": 0},
                live_stage="catalog",
            )

            payload = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual("vinted", payload["source"])
        self.assertTrue(payload["meta"]["live_partial"])
        self.assertEqual("catalog", payload["meta"]["live_stage"])
        self.assertEqual(1, payload["meta"]["deal_hunter_candidates"])
        self.assertEqual(1, len(payload["rows"]))

    def test_vinted_page_url_helpers(self) -> None:
        base_url = "https://www.vinted.it/catalog?search_text=magliettina&search_id=1033594103"

        self.assertEqual(1, extract_vinted_page_number(base_url))
        self.assertEqual(3, extract_vinted_page_number(f"{base_url}&page=3"))
        self.assertEqual(
            f"{base_url}&page=2",
            build_vinted_page_url(base_url, 2),
        )
        self.assertEqual(base_url, build_vinted_page_url(f"{base_url}&page=4", 1))

    def test_read_vinted_next_page_target_clicks_real_pagination_link(self) -> None:
        driver = Mock()
        driver.run_js.return_value = {
            "href": "/catalog?search_id=1119058183&page=2&time=1784304226",
            "clicked": True,
        }

        target = _read_vinted_next_page_target(driver, 2)

        self.assertEqual(
            "https://www.vinted.it/catalog?search_id=1119058183&page=2&time=1784304226",
            target["href"],
        )
        self.assertTrue(target["clicked"])

    @patch("scraper_app.sources.vinted._wait_for_vinted_catalog_page")
    @patch("scraper_app.sources.vinted.current_page_url")
    def test_open_vinted_next_page_falls_back_to_driver_get_when_click_does_not_transition(
        self,
        mocked_current_url,
        mocked_wait_for_page,
    ) -> None:
        mocked_wait_for_page.side_effect = [False, True]
        mocked_current_url.return_value = "https://www.vinted.it/catalog?search_id=1119058183&page=2&time=1784304226"
        driver = Mock()
        driver.run_js.return_value = {
            "href": "/catalog?search_id=1119058183&page=2&time=1784304226",
            "clicked": True,
        }

        next_page_url = _open_vinted_next_page(driver, 2, page_settle_seconds=0.5)

        driver.get.assert_called_once_with(
            "https://www.vinted.it/catalog?search_id=1119058183&page=2&time=1784304226",
            wait=Wait.SHORT,
            timeout=15,
        )
        self.assertEqual(
            "https://www.vinted.it/catalog?search_id=1119058183&page=2&time=1784304226",
            next_page_url,
        )
        self.assertEqual(1.8, mocked_wait_for_page.call_args_list[0].args[2])
        self.assertEqual(1.8, mocked_wait_for_page.call_args_list[1].args[2])

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
    @patch("scraper_app.sources.vinted.click_first_matching_text")
    @patch("scraper_app.sources.vinted.emit_vinted_login_required_signal")
    @patch("scraper_app.sources.vinted.emit_vinted_access_signal")
    @patch("scraper_app.sources.vinted.wait_for_vinted_access_status")
    @patch("scraper_app.sources.vinted.consume_stop_after_current_item_request", return_value=False)
    @patch("scraper_app.sources.vinted.consume_vinted_login_confirmed_request")
    def test_wait_for_vinted_login_rechecks_after_user_confirmation(
        self,
        mocked_confirmed,
        _mocked_stop,
        mocked_wait_for_status,
        _mocked_emit_access,
        mocked_emit_required,
        mocked_cookie_click,
        _mocked_sleep,
    ) -> None:
        mocked_confirmed.side_effect = [False, True]
        mocked_cookie_click.return_value = ""
        mocked_wait_for_status.return_value = {
            "marker_present": True,
            "expected_alt": "bonaccarla",
            "current_url": "https://www.vinted.it/catalog",
            "checked_at": "2026-07-14T10:00:00",
        }

        driver = Mock()
        result = _wait_for_vinted_login_if_needed(
            driver,
            {
                "marker_present": False,
                "expected_alt": "bonaccarla",
                "current_url": "https://www.vinted.it/catalog",
                "checked_at": "2026-07-14T09:59:00",
            },
            revisit_url="https://www.vinted.it/catalog?search_text=macbook",
        )

        self.assertTrue(result["marker_present"])
        self.assertEqual(1, mocked_emit_required.call_count)

    @patch("scraper_app.sources.vinted.emit_vinted_login_required_signal")
    def test_wait_for_vinted_login_if_needed_skips_login_flow_on_page_not_found(
        self,
        mocked_emit_required,
    ) -> None:
        driver = Mock()

        result = _wait_for_vinted_login_if_needed(
            driver,
            {
                "marker_present": False,
                "page_not_found": True,
                "expected_alt": "bonaccarla",
                "current_url": "https://www.vinted.it/items/123",
                "checked_at": "2026-07-22T10:00:00",
            },
            revisit_url="https://www.vinted.it/items/123",
        )

        self.assertTrue(result["page_not_found"])
        mocked_emit_required.assert_not_called()
        driver.get.assert_not_called()

    @patch("scraper_app.sources.vinted.time.sleep")
    @patch("scraper_app.sources.vinted.click_first_matching_text", return_value="")
    @patch("scraper_app.sources.vinted.emit_vinted_login_required_signal")
    @patch("scraper_app.sources.vinted.emit_vinted_access_signal")
    @patch("scraper_app.sources.vinted.wait_for_vinted_access_status")
    @patch("scraper_app.sources.vinted.consume_stop_after_current_item_request", return_value=False)
    @patch("scraper_app.sources.vinted.consume_vinted_login_confirmed_request")
    def test_wait_for_vinted_login_reopens_target_url_after_confirmation(
        self,
        mocked_confirmed,
        _mocked_stop,
        mocked_wait_for_status,
        _mocked_emit_access,
        _mocked_emit_required,
        _mocked_cookie_click,
        _mocked_sleep,
    ) -> None:
        mocked_confirmed.side_effect = [True]
        mocked_wait_for_status.return_value = {
            "marker_present": True,
            "expected_alt": "bonaccarla",
            "current_url": "https://www.vinted.it/items/123",
            "checked_at": "2026-07-14T10:00:00",
        }
        driver = Mock()

        result = _wait_for_vinted_login_if_needed(
            driver,
            {
                "marker_present": False,
                "expected_alt": "bonaccarla",
                "current_url": "https://www.vinted.it/items/123",
                "checked_at": "2026-07-14T09:59:00",
            },
            revisit_url="https://www.vinted.it/items/123",
        )

        driver.get.assert_not_called()
        self.assertTrue(result["marker_present"])

    @patch("scraper_app.sources.vinted.time.sleep")
    @patch("scraper_app.sources.vinted.click_first_matching_text", return_value="")
    @patch("scraper_app.sources.vinted.emit_vinted_login_required_signal")
    @patch("scraper_app.sources.vinted.emit_vinted_access_signal")
    @patch("scraper_app.sources.vinted.wait_for_vinted_access_status")
    @patch("scraper_app.sources.vinted.consume_stop_after_current_item_request", return_value=False)
    @patch("scraper_app.sources.vinted.consume_vinted_login_confirmed_request", return_value=False)
    def test_wait_for_vinted_login_auto_recovers_after_manual_login_without_gui_confirmation(
        self,
        _mocked_confirmed,
        _mocked_stop,
        mocked_wait_for_status,
        _mocked_emit_access,
        mocked_emit_required,
        _mocked_cookie_click,
        _mocked_sleep,
    ) -> None:
        mocked_wait_for_status.side_effect = [
            {
                "marker_present": True,
                "expected_alt": "bonaccarla",
                "current_url": "https://www.vinted.it/",
                "checked_at": "2026-07-22T10:00:00",
            },
            {
                "marker_present": True,
                "expected_alt": "bonaccarla",
                "current_url": "https://www.vinted.it/items/123",
                "checked_at": "2026-07-22T10:00:01",
            },
        ]
        driver = Mock()

        result = _wait_for_vinted_login_if_needed(
            driver,
            {
                "marker_present": False,
                "expected_alt": "bonaccarla",
                "current_url": "https://www.vinted.it/items/123",
                "checked_at": "2026-07-22T09:59:00",
            },
            revisit_url="https://www.vinted.it/items/123",
        )

        mocked_emit_required.assert_called_once()
        driver.get.assert_called_once_with(
            "https://www.vinted.it/items/123",
            wait=Wait.SHORT,
            timeout=15,
        )
        self.assertTrue(result["marker_present"])
        self.assertEqual("https://www.vinted.it/items/123", result["current_url"])

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

    def test_vinted_max_price_helpers(self) -> None:
        self.assertEqual(25.0, _normalize_vinted_max_price("25"))
        self.assertEqual(25.5, _normalize_vinted_max_price("25,50 €"))
        self.assertIsNone(_normalize_vinted_max_price(""))
        self.assertTrue(_vinted_row_matches_max_price({"price_value": 24.0, "total_price_value": 24.0}, 25.0))
        self.assertFalse(_vinted_row_matches_max_price({"price_value": 26.0, "total_price_value": 26.0}, 25.0))
        self.assertTrue(_vinted_row_matches_min_price_for_deal_hunter({"price_value": 10.0}, 5.0))
        self.assertFalse(_vinted_row_matches_min_price_for_deal_hunter({"price_value": 4.99}, 5.0))
        self.assertTrue(_vinted_row_matches_min_price_for_deal_hunter({"price_value": ""}, 5.0))

    def test_favorite_count_and_evaluation_helpers(self) -> None:
        self.assertEqual(34, parse_vinted_favorite_count("34"))
        self.assertEqual(1200, parse_vinted_favorite_count("1.200"))
        self.assertIsNone(parse_vinted_favorite_count(""))
        self.assertEqual("", classify_vinted_evaluation(15, False))
        self.assertEqual("da valutare", classify_vinted_evaluation(16, False))
        self.assertEqual("da valutare assolutamente", classify_vinted_evaluation(None, True))
        self.assertEqual("da valutare assolutamente", classify_vinted_evaluation(3, True))
        self.assertEqual("da valutare assolutamente", classify_vinted_evaluation(16, True))
        self.assertEqual("da valutare assolutamente", classify_vinted_evaluation(16, True, "1 settimana fa"))
        self.assertEqual("da valutare", classify_vinted_evaluation(16, True, "8 giorni fa"))
        self.assertEqual("da valutare", classify_vinted_evaluation(16, True, "2 settimane fa"))
        self.assertEqual("da valutare", classify_vinted_evaluation(16, True, "1 mese fa"))
        self.assertEqual("", classify_vinted_evaluation(16, False, "2 mesi fa"))
        self.assertEqual("", classify_vinted_evaluation(16, True, "1 anno fa"))

    def test_vinted_timing_config_applies_slow_mode_floor(self) -> None:
        self.assertEqual((2.5, 4.0), _vinted_timing_config(True, 0.5, 1.0))
        self.assertEqual((1.0, 2.0), _vinted_timing_config(False, 1.0, 2.0))

    @patch("scraper_app.sources.vinted.time.sleep")
    @patch("scraper_app.sources.vinted.time.monotonic")
    def test_wait_for_vinted_detail_page_ready_returns_early_when_page_is_ready(
        self,
        mocked_monotonic,
        mocked_sleep,
    ) -> None:
        mocked_monotonic.side_effect = [10.0, 10.05, 10.1]
        driver = Mock()
        driver.run_js.side_effect = [
            {
                "readyState": "interactive",
                "title": "",
                "rawPriceText": "",
                "offerText": "",
                "bodyLength": 40,
            },
            {
                "readyState": "complete",
                "title": "Charm Pandora",
                "rawPriceText": "12,00 €",
                "offerText": "",
                "bodyLength": 220,
            },
            {
                "readyState": "complete",
                "title": "Charm Pandora",
                "rawPriceText": "12,00 €",
                "offerText": "",
                "bodyLength": 220,
            },
        ]

        result = _wait_for_vinted_detail_page_ready(driver, max_wait_seconds=3.0)

        self.assertTrue(result)
        self.assertEqual(3, driver.run_js.call_count)
        self.assertEqual(2, mocked_sleep.call_count)

    @patch("scraper_app.sources.vinted_profile.time.sleep")
    def test_profile_dom_wait_requires_two_stable_observations(self, mocked_sleep) -> None:
        driver = Mock()
        driver.run_js.side_effect = ["complete|8|one|two|3", "complete|8|one|two|3"]

        self.assertTrue(_wait_for_vinted_profile_dom_ready(driver, max_wait_seconds=0))
        self.assertEqual(2, driver.run_js.call_count)
        self.assertEqual(1, mocked_sleep.call_count)

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
        self.assertEqual("3,00 €", _extract_vinted_base_price(page_text, "Magliettina vintage"))

    def test_primary_price_prefers_value_before_include_protezione_acquisti(self) -> None:
        page_text = (
            "MacBook Air 1.199,00 € 1.254,70 € Include la Protezione acquisti e assistenza"
        )
        self.assertEqual("1.254,70 €", _extract_vinted_primary_price(page_text, "MacBook Air"))
        self.assertEqual("1.199,00 €", _extract_vinted_base_price(page_text, "MacBook Air"))

    def test_read_vinted_published_text_from_page_text(self) -> None:
        page_text = "Titolo annuncio Caricato 3 ore fa Marca Apple"
        driver = Mock()
        driver.run_js.return_value = ""
        self.assertEqual("3 ore fa", _read_vinted_published_text(driver, page_text))

    @patch("scraper_app.sources.vinted._read_vinted_detail_text", return_value="Page not found")
    def test_build_vinted_detail_row_marks_missing_page_without_crashing(self, _mocked_page_text) -> None:
        row = _build_vinted_detail_row(
            driver=Mock(),
            current_link="https://www.vinted.it/items/123-page-missing",
            search_term="pandora",
            search_url="https://www.vinted.it/catalog?search_text=pandora",
            tag="",
            item_name="Pandora charm",
            base_row={
                "name": "Pandora charm",
                "favorite_count": 78,
                "deal_hunter_candidate": True,
                "deal_hunter_match": False,
            },
        )

        self.assertEqual("page_not_found", row["detail_error"])
        self.assertEqual("Pandora charm", row["name"])
        self.assertEqual("123", row["item_id"])
        self.assertEqual("https://www.vinted.it/items/123-page-missing", row["link"])

    def test_build_vinted_detail_row_recognizes_exact_not_found_h1(self) -> None:
        driver = Mock()
        driver.run_js.return_value = {
            "bodyText": "Not found",
            "h1Text": "Not found",
            "pageNotFound": True,
        }

        row = _build_vinted_detail_row(
            driver=driver,
            current_link="https://www.vinted.it/items/456-missing",
            search_term="charm",
            search_url="https://www.vinted.it/catalog?search_text=charm",
            tag="",
            item_name="Charm non disponibile",
            base_row={"item_id": "456"},
        )

        self.assertEqual("page_not_found", row["detail_error"])
        self.assertEqual("456", row["item_id"])

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
                "seller_name": "Seller One",
                "seller_profile_url": "https://www.vinted.it/member/123456789",
                "seller_member_id": "123456789",
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
            self.assertEqual(4, meta["db_total_search_runs"])
            macbook_row = next(row for row in rows if row["search_term"] == "macbook")
            self.assertEqual(3, macbook_row["times_seen"])
            self.assertEqual("MacBook", macbook_row["name"])
            self.assertEqual("", macbook_row["tag"])
            self.assertEqual("Testo descrizione", macbook_row["description"])
            self.assertEqual("3 ore fa", macbook_row["published_at"])
            self.assertEqual("5,49 â‚¬", macbook_row["shipping_price"])
            self.assertEqual("sped > 2,99", macbook_row["shipping_alert"])
            self.assertEqual(305.49, macbook_row["total_price_value"])
            self.assertTrue(macbook_row["offer_available"])
            self.assertEqual(34, macbook_row["favorite_count"])
            self.assertEqual("Seller One", macbook_row["seller_name"])
            self.assertEqual("https://www.vinted.it/member/123456789", macbook_row["seller_profile_url"])
            self.assertEqual("123456789", macbook_row["seller_member_id"])
            self.assertEqual("da valutare", macbook_row["evaluation_label"])

            filtered_rows, filtered_meta = load_vinted_rows(db_path, tag_filter="ricercato", limit=10)
            self.assertEqual(0, len(filtered_rows))
            self.assertEqual("ricercato", filtered_meta["db_tag_filter"])

            hidden_rows, hidden_meta = load_vinted_rows(db_path, tag_filter="altro", limit=10)
            self.assertEqual([], hidden_rows)
            self.assertEqual(0, hidden_meta["db_filtered_search_hits"])

    def test_completed_detail_cache_prevents_reextracting_vinted_item(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = str(Path(temp_dir) / "scraper.db")
            save_vinted_rows(
                [
                    {
                        "source": "vinted",
                        "item_id": "9425130935",
                        "name": "Charm Pandora",
                        "description": "Descrizione gia estratta",
                        "published_at": "3 ore fa",
                        "price": "10,00 €",
                        "price_value": 10.0,
                        "shipping_price": "1,99 €",
                        "shipping_price_value": 1.99,
                        "total_price": "12,49 €",
                        "total_price_value": 12.49,
                        "favorite_count": 80,
                        "seller_name": "Seller Charm",
                        "seller_profile_url": "https://www.vinted.it/member/987654321",
                        "seller_member_id": "987654321",
                        "evaluation_label": "da valutare assolutamente",
                        "link": "https://www.vinted.it/items/9425130935-charm-pandora",
                        "search_term": "charm",
                    }
                ],
                db_path,
            )

            cached_rows = load_vinted_completed_detail_rows(db_path)
            driver = Mock()
            rows = [
                {
                    "source": "vinted",
                    "item_id": "9425130935",
                    "name": "Charm Pandora",
                    "link": "https://www.vinted.it/items/9425130935-charm-pandora",
                    "search_term": "charm",
                    "search_url": "https://www.vinted.it/catalog?search_text=charm",
                    "favorite_count": 80,
                    "deal_hunter_candidate": True,
                    "deal_hunter_match": False,
                }
            ]

            meta = _enrich_vinted_priority_rows(
                driver,
                rows,
                {
                    "db_path": db_path,
                    "deal_hunter_enabled": True,
                    "deal_hunter_min_favorites": 70,
                    "deal_hunter_max_age_hours": 24,
                },
            )

        self.assertIn("id:9425130935", cached_rows)
        driver.get.assert_not_called()
        self.assertEqual(1, meta["cached_count"])
        self.assertEqual("Descrizione gia estratta", rows[0]["description"])
        self.assertEqual("Seller Charm", rows[0]["seller_name"])
        self.assertEqual("https://www.vinted.it/member/987654321", rows[0]["seller_profile_url"])
        self.assertEqual("987654321", rows[0]["seller_member_id"])
        self.assertTrue(rows[0]["detail_cached"])
        self.assertTrue(rows[0]["deal_hunter_match"])

    @patch("scraper_app.sources.vinted._read_vinted_offer_text", return_value="Fai un'offerta")
    @patch("scraper_app.sources.vinted._read_vinted_shipping_price", return_value="1,99 €")
    @patch("scraper_app.sources.vinted._read_vinted_price", return_value="10,00 €")
    @patch("scraper_app.sources.vinted._read_vinted_published_text", return_value="3 ore fa")
    @patch("scraper_app.sources.vinted._read_vinted_title", return_value="Charm Pandora")
    @patch(
        "scraper_app.sources.vinted._read_vinted_detail_payload",
        return_value={
            "bodyText": "Charm Pandora 10,00 € 11,99 € incl. 3 ore fa",
            "title": "Charm Pandora",
            "publishedText": "3 ore fa",
            "rawPriceText": "10,00 €",
            "shippingText": "Spedizione da 1,99 €",
            "offerText": "Fai un'offerta",
            "favorite_count_text": "80",
            "sellerName": "Competitor Charm",
            "sellerProfileUrl": "https://www.vinted.it/member/123456789-competitor-charm",
            "sellerMemberId": "123456789",
        },
    )
    def test_detail_extraction_promotes_hot_listing_to_deal_when_detail_data_matches(
        self,
        _mocked_payload,
        _mocked_title,
        _mocked_published,
        _mocked_price,
        _mocked_shipping,
        _mocked_offer,
    ) -> None:
        row = _build_vinted_detail_row(
            driver=Mock(),
            current_link="https://www.vinted.it/items/9425130935-charm-pandora",
            search_term="charm",
            search_url="https://www.vinted.it/catalog?search_text=charm",
            tag="ricercato",
            item_name="Charm Pandora",
            base_row={
                "source": "vinted",
                "item_id": "9425130935",
                "name": "Charm Pandora",
                "link": "https://www.vinted.it/items/9425130935-charm-pandora",
                "search_term": "charm",
                "search_url": "https://www.vinted.it/catalog?search_text=charm",
                "favorite_count": 12,
                "evaluation_label": "da valutare assolutamente",
                "has_ricercato_badge": True,
                "secondary_badge_text": "Ricercato",
            },
            deal_hunter_min_favorites=70,
            deal_hunter_max_age_hours=24,
        )

        self.assertEqual(80, row["favorite_count"])
        self.assertEqual("da valutare assolutamente", row["evaluation_label"])
        self.assertTrue(row["deal_hunter_candidate"])
        self.assertTrue(row["deal_hunter_match"])
        self.assertEqual("affare 24h/70+", row["deal_hunter_label"])
        self.assertEqual("Competitor Charm", row["seller_name"])
        self.assertEqual("https://www.vinted.it/member/123456789", row["seller_profile_url"])
        self.assertEqual("123456789", row["seller_member_id"])

    @patch("scraper_app.sources.vinted.time.monotonic", side_effect=[0.0, 61.0])
    @patch("scraper_app.sources.vinted._wait_for_vinted_login_if_needed", return_value={"marker_present": True})
    @patch("scraper_app.sources.vinted.wait_for_vinted_access_status", return_value={"marker_present": True})
    @patch("scraper_app.sources.vinted.click_first_matching_text", return_value="")
    @patch("scraper_app.sources.vinted._wait_for_vinted_detail_page_ready")
    @patch("scraper_app.sources.vinted._like_vinted_deal_if_needed", return_value={"favorite_clicked": True, "favorite_action": "clicked"})
    def test_priority_detail_timeout_skips_listing_without_blocking(
        self,
        _mocked_like,
        _mocked_ready,
        _mocked_cookie,
        _mocked_access,
        _mocked_login,
        _mocked_monotonic,
    ) -> None:
        driver = Mock()
        rows = [
            {
                "source": "vinted",
                "item_id": "9425130935",
                "name": "Charm Pandora",
                "link": "https://www.vinted.it/items/9425130935-charm-pandora",
                "search_term": "charm",
                "search_url": "https://www.vinted.it/catalog?search_text=charm",
                "favorite_count": 80,
                "evaluation_label": "da valutare assolutamente",
                "deal_hunter_candidate": True,
                "deal_hunter_match": False,
            }
        ]

        meta = _enrich_vinted_priority_rows(
            driver,
            rows,
            {
                "db_path": ":memory:",
                "deal_hunter_enabled": True,
                "deal_hunter_min_favorites": 70,
                "deal_hunter_max_age_hours": 24,
                "detail_item_timeout_seconds": 60,
            },
        )

        self.assertEqual(1, meta["enriched_count"])
        self.assertEqual("detail_timeout", rows[0]["detail_error"])
        self.assertFalse(rows[0].get("deal_hunter_match", False))
        _mocked_like.assert_not_called()

    @patch("scraper_app.sources.vinted._like_vinted_deal_if_needed", return_value={"favorite_clicked": True, "favorite_action": "clicked"})
    @patch("scraper_app.sources.vinted._wait_for_vinted_login_if_needed", return_value={"marker_present": True})
    @patch("scraper_app.sources.vinted.wait_for_vinted_access_status", return_value={"marker_present": True})
    @patch("scraper_app.sources.vinted.click_first_matching_text", return_value="")
    @patch("scraper_app.sources.vinted._wait_for_vinted_detail_page_ready")
    @patch(
        "scraper_app.sources.vinted._build_vinted_detail_row",
        return_value={
            "source": "vinted",
            "item_id": "9425130935",
            "name": "Charm Pandora",
            "link": "https://www.vinted.it/items/9425130935-charm-pandora",
            "favorite_count": 80,
            "evaluation_label": "da valutare assolutamente",
            "deal_hunter_candidate": True,
            "deal_hunter_match": True,
        },
    )
    def test_priority_detail_likes_confirmed_deal_after_extraction(
        self,
        _mocked_build_detail,
        _mocked_ready,
        _mocked_cookie,
        _mocked_access,
        _mocked_login,
        mocked_like,
    ) -> None:
        driver = Mock()
        rows = [
            {
                "source": "vinted",
                "item_id": "9425130935",
                "name": "Charm Pandora",
                "link": "https://www.vinted.it/items/9425130935-charm-pandora",
                "search_term": "charm",
                "search_url": "https://www.vinted.it/catalog?search_text=charm",
                "favorite_count": 80,
                "evaluation_label": "da valutare assolutamente",
                "deal_hunter_candidate": True,
                "deal_hunter_match": False,
            }
        ]

        meta = _enrich_vinted_priority_rows(
            driver,
            rows,
            {
                "db_path": ":memory:",
                "deal_hunter_enabled": True,
                "deal_hunter_min_favorites": 70,
                "deal_hunter_max_age_hours": 24,
                "detail_item_timeout_seconds": 60,
            },
        )

        self.assertEqual(1, meta["enriched_count"])
        mocked_like.assert_called_once()
        self.assertTrue(rows[0]["favorite_clicked"])
        self.assertEqual("clicked", rows[0]["favorite_action"])

    def test_click_vinted_favourite_button_clicks_when_not_already_pressed(self) -> None:
        driver = Mock()
        driver.run_js.side_effect = [
            {"found": True, "pressed": False, "label": "Aggiungi ai preferiti", "count": "74"},
            True,
            {"found": True, "pressed": True, "label": "Aggiunto ai preferiti", "count": "75"},
        ]

        result = _click_vinted_favourite_button(driver, action_delay_seconds=0)

        self.assertTrue(result["favorite_button_found"])
        self.assertTrue(result["favorite_clicked"])
        self.assertEqual("clicked", result["favorite_action"])
        self.assertEqual("75", result["favorite_count_text"])

    @patch("scraper_app.sources.vinted._wait_for_vinted_login_if_needed")
    def test_like_vinted_deal_is_skipped_in_no_login_mode(self, mocked_login) -> None:
        from scraper_app.sources.vinted import _like_vinted_deal_if_needed

        result = _like_vinted_deal_if_needed(
            Mock(),
            {"deal_hunter_match": True, "link": "https://www.vinted.it/items/1-charm"},
            current_link="https://www.vinted.it/items/1-charm",
            config={"skip_login_required": True},
            page_already_open=True,
        )

        self.assertEqual("skipped_no_login_mode", result["favorite_action"])
        mocked_login.assert_not_called()

    def test_deal_hunter_does_not_extract_hot_listing_below_min_likes(self) -> None:
        driver = Mock()
        rows = [
            {
                "source": "vinted",
                "item_id": "1",
                "name": "Ricercato low likes",
                "link": "https://www.vinted.it/items/1-ricercato-low-likes",
                "search_term": "charm",
                "search_url": "https://www.vinted.it/catalog?search_text=charm",
                "favorite_count": 12,
                "evaluation_label": "da valutare assolutamente",
                "deal_hunter_candidate": False,
                "deal_hunter_match": False,
                "has_ricercato_badge": True,
            }
        ]

        meta = _enrich_vinted_priority_rows(
            driver,
            rows,
            {
                "db_path": ":memory:",
                "deal_hunter_enabled": True,
                "deal_hunter_min_favorites": 70,
                "deal_hunter_max_age_hours": 24,
            },
        )

        driver.get.assert_not_called()
        self.assertEqual(0, meta["enriched_count"])

    def test_known_item_keys_and_offer_history_use_item_id_and_link(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "vinted.db"
            row = {
                "link": "https://www.vinted.it/items/123-macbook",
                "item_id": "123",
                "name": "MacBook",
                "price": "300,00 â‚¬",
                "price_value": 300.0,
                "total_price": "305,49 EUR",
                "total_price_value": 305.49,
                "favorite_count": 12,
                "evaluation_label": "",
                "currency": "EUR",
                "raw_text": "MacBook 300,00 â‚¬",
                "extracted_at": "2026-07-01T10:00:00",
                "search_term": "macbook",
                "search_url": "https://www.vinted.it/catalog?search_text=macbook",
            }

            save_vinted_rows([row], db_path)
            known_item_keys = load_vinted_known_item_keys(db_path)

            self.assertIn("id:123", known_item_keys)
            self.assertIn("link:https://www.vinted.it/items/123-macbook", known_item_keys)
            self.assertEqual(
                ("id:123", "link:https://www.vinted.it/items/123-macbook"),
                build_vinted_item_identity_keys("123", "https://www.vinted.it/items/123-macbook"),
            )
            self.assertTrue(
                _vinted_row_matches_known_item_keys(
                    {"item_id": "123", "link": "https://www.vinted.it/items/123-macbook"},
                    known_item_keys,
                )
            )

            save_vinted_offer_results(
                [
                    {
                        "link": "https://www.vinted.it/items/123-macbook",
                        "item_id": "123",
                        "item_name": "MacBook",
                        "submitted": True,
                        "offer_value": 255.0,
                        "offer_input_value": "255.00",
                        "offer_discount_percent": 15.0,
                        "source_price": 300.0,
                        "submitted_at": "2026-07-16T10:00:00",
                    }
                ],
                db_path,
            )
            offered_keys = load_vinted_submitted_offer_keys(db_path)
            annotated_rows = annotate_rows_with_vinted_offer_history(
                [{"item_id": "123", "link": "https://www.vinted.it/items/123-macbook"}],
                db_path,
            )

            self.assertIn("id:123", offered_keys)
            self.assertIn("link:https://www.vinted.it/items/123-macbook", offered_keys)
            self.assertTrue(annotated_rows[0]["offer_already_submitted"])
            self.assertEqual("2026-07-16T10:00:00", annotated_rows[0]["offer_last_submitted_at"])
            self.assertEqual(255.0, annotated_rows[0]["offer_last_value"])

    def test_database_can_reload_specific_saved_search_run_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "vinted.db"
            first_row = {
                "link": "https://www.vinted.it/items/123-macbook",
                "item_id": "123",
                "name": "MacBook",
                "price": "300,00 â‚¬",
                "price_value": 300.0,
                "total_price": "305,49 EUR",
                "total_price_value": 305.49,
                "favorite_count": 12,
                "evaluation_label": "",
                "currency": "EUR",
                "raw_text": "MacBook 300,00 â‚¬",
                "extracted_at": "2026-07-01T10:00:00",
                "search_term": "macbook",
                "search_url": "https://www.vinted.it/catalog?search_text=macbook",
            }
            updated_row = {
                **first_row,
                "price": "280,00 â‚¬",
                "price_value": 280.0,
                "total_price": "285,49 EUR",
                "total_price_value": 285.49,
                "favorite_count": 34,
                "evaluation_label": "da valutare assolutamente",
                "extracted_at": "2026-07-01T11:00:00",
            }

            first_meta = save_vinted_rows([first_row], db_path)
            second_meta = save_vinted_rows([updated_row], db_path)
            runs = list_vinted_search_runs(db_path, limit=10)

            self.assertEqual(2, len(runs))
            self.assertEqual(first_meta["search_run_key"], runs[-1]["run_key"])
            self.assertEqual(second_meta["search_run_key"], runs[0]["run_key"])

            first_run_rows, first_run_meta = load_vinted_rows(
                db_path,
                search_run_key=str(first_meta["search_run_key"]),
                limit=10,
            )
            second_run_rows, second_run_meta = load_vinted_rows(
                db_path,
                search_run_key=str(second_meta["search_run_key"]),
                limit=10,
            )

            self.assertEqual(1, len(first_run_rows))
            self.assertEqual(1, len(second_run_rows))
            self.assertEqual(305.49, first_run_rows[0]["total_price_value"])
            self.assertEqual("", first_run_rows[0]["evaluation_label"])
            self.assertEqual(285.49, second_run_rows[0]["total_price_value"])
            self.assertEqual("da valutare assolutamente", second_run_rows[0]["evaluation_label"])
            self.assertEqual(first_meta["search_run_key"], first_run_meta["db_search_run_key"])
            self.assertEqual(second_meta["search_run_key"], second_run_meta["db_search_run_key"])

    def test_saved_search_run_can_be_renamed_filtered_and_deleted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "vinted.db"
            row = {
                "link": "https://www.vinted.it/items/123-macbook",
                "item_id": "123",
                "name": "MacBook",
                "price": "300,00 â‚¬",
                "price_value": 300.0,
                "total_price": "305,49 EUR",
                "total_price_value": 305.49,
                "currency": "EUR",
                "raw_text": "MacBook 300,00 â‚¬",
                "extracted_at": "2026-07-01T10:00:00",
                "search_term": "macbook",
                "search_url": "https://www.vinted.it/catalog?search_text=macbook",
            }
            saved = save_vinted_rows([row], db_path)

            updated = update_vinted_search_run(
                run_key=str(saved["search_run_key"]),
                db_path=db_path,
                title="Affari MacBook",
                notes="Controllare preferiti alti",
            )
            runs = list_vinted_search_runs(db_path, limit=10, text_filter="affari")
            missing = list_vinted_search_runs(db_path, limit=10, text_filter="inesistente")

            self.assertEqual("Affari MacBook", updated["title"])
            self.assertEqual("Controllare preferiti alti", updated["notes"])
            self.assertEqual(1, len(runs))
            self.assertEqual("Affari MacBook", runs[0]["title"])
            self.assertEqual([], missing)

            run_rows, run_meta = load_vinted_rows(
                db_path,
                search_run_key=str(saved["search_run_key"]),
                limit=10,
            )
            self.assertEqual("Affari MacBook", run_meta["db_search_run_title"])
            self.assertEqual("Controllare preferiti alti", run_meta["db_search_run_notes"])
            self.assertEqual("Affari MacBook", run_rows[0]["search_run_title"])

            delete_result = delete_vinted_search_run(str(saved["search_run_key"]), db_path)
            remaining = list_vinted_search_runs(db_path, limit=10)

            self.assertEqual(0, delete_result["db_total_search_runs"])
            self.assertEqual([], remaining)

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

    @patch("scraper_app.runner.run_vinted_scraper")
    def test_competitor_discovery_is_independent_cron_feature(self, mocked_scraper) -> None:
        mocked_scraper.return_value = Mock(
            source="vinted",
            rows=[
                {
                    "link": "https://www.vinted.it/items/1-charm",
                    "seller_member_id": "123",
                    "seller_profile_url": "https://www.vinted.it/member/123",
                    "seller_name": "Seller",
                }
            ],
            meta={"row_count": 1, "db_path": "data/scraper.db"},
        )

        outcome = run_scraper(
            "vinted_competitors",
            competitor_keywords="charm",
            competitor_category="Gioielli donna",
            competitor_min_likes=15,
            competitor_max_age_hours=720,
            competitor_interval_seconds=0,
            max_results=20,
            db_path="data/scraper.db",
        )

        self.assertEqual("vinted_competitors", outcome.source)
        self.assertTrue(outcome.meta["competitor_discovery_enabled"])
        self.assertEqual(1, outcome.meta["competitor_sellers_found"])
        mocked_scraper.assert_called()
        call_kwargs = mocked_scraper.call_args.kwargs
        self.assertEqual(15, call_kwargs["deal_hunter_min_favorites"])
        self.assertEqual(720.0, call_kwargs["deal_hunter_max_age_hours"])
        self.assertEqual("competitor_discovery", call_kwargs["run_kind"])
        self.assertTrue(call_kwargs["skip_login_required"])
        self.assertFalse(call_kwargs["discord_deal_notifications"])
        self.assertFalse(call_kwargs["auto_submit_offers"])

    @patch("scraper_app.runner.run_vinted_scraper")
    @patch("scraper_app.runner.run_vinted_profile_monitor")
    def test_continuous_monitoring_runs_profiles_then_competitors_once(
        self,
        mocked_profiles,
        mocked_scraper,
    ) -> None:
        mocked_profiles.return_value = Mock(
            source="vinted_profile",
            rows=[{"item_id": "1", "name": "Profile item", "link": "https://www.vinted.it/items/1"}],
            meta={"profile_item_count": 1},
        )
        mocked_scraper.return_value = Mock(
            source="vinted",
            rows=[{"item_id": "2", "name": "Competitor item", "link": "https://www.vinted.it/items/2"}],
            meta={"row_count": 1, "db_path": "data/scraper.db"},
        )

        outcome = run_scraper(
            "vinted_continuous",
            profile_urls="https://www.vinted.it/member/262102939",
            competitor_keywords="charm",
            competitor_category="Gioielli donna",
            article_interval_seconds=0,
            profile_interval_seconds=0,
            discord_profile_report=False,
            discord_deal_notifications=True,
            db_path="data/scraper.db",
        )

        self.assertEqual("vinted_continuous", outcome.source)
        self.assertTrue(outcome.meta["continuous_monitoring_enabled"])
        self.assertEqual(1, outcome.meta["continuous_cycle"])
        self.assertEqual(["profiles", "competitors"], [row["continuous_component"] for row in outcome.rows])
        self.assertEqual(0, mocked_profiles.call_args.kwargs["interval_seconds"])
        self.assertFalse(mocked_profiles.call_args.kwargs["send_daily_report"])
        self.assertTrue(mocked_profiles.call_args.kwargs["check_listing_statuses"])
        self.assertEqual("competitor_discovery", mocked_scraper.call_args.kwargs["run_kind"])
        self.assertTrue(mocked_scraper.call_args.kwargs["skip_login_required"])
        self.assertFalse(mocked_scraper.call_args.kwargs["auto_submit_offers"])
        self.assertTrue(mocked_scraper.call_args.kwargs["discord_deal_notifications"])

    @patch("scraper_app.runner.run_vinted_description_extractor")
    def test_vinted_descriptions_runner_does_not_pass_profile_monitor_kwargs(self, mocked_extractor) -> None:
        mocked_extractor.return_value = Mock(source="vinted", rows=[], meta={})

        run_scraper(
            "vinted_descriptions",
            items=["https://www.vinted.it/items/1-charm"],
            daily_report_hour=11,
            discord_profile_report=True,
        )

        call_kwargs = mocked_extractor.call_args.kwargs
        self.assertNotIn("daily_report_hour", call_kwargs)
        self.assertNotIn("discord_profile_report", call_kwargs)

    @patch("scraper_app.runner.run_vinted_profile_monitor")
    def test_vinted_profile_runner_passes_daily_report_hour(self, mocked_monitor) -> None:
        mocked_monitor.return_value = Mock(source="vinted_profile", rows=[], meta={})

        run_scraper(
            "vinted_profile",
            profile_urls="https://www.vinted.it/member/262102939",
            interval_seconds=0,
            daily_report_hour=11,
        )

        self.assertEqual(11, mocked_monitor.call_args.kwargs["daily_report_hour"])
        self.assertTrue(mocked_monitor.call_args.kwargs["check_listing_statuses"])

    def test_competitor_status_classification_thresholds(self) -> None:
        self.assertEqual(
            "candidate",
            classify_vinted_competitor_status(
                interesting_item_count=1,
                distinct_search_terms=1,
                gone_item_count=0,
                max_favorite_count=15,
            ),
        )
        self.assertEqual(
            "active",
            classify_vinted_competitor_status(
                interesting_item_count=3,
                distinct_search_terms=1,
                gone_item_count=0,
                max_favorite_count=30,
            ),
        )
        self.assertEqual(
            "confirmed",
            classify_vinted_competitor_status(
                interesting_item_count=1,
                distinct_search_terms=1,
                gone_item_count=1,
                max_favorite_count=30,
            ),
        )
        self.assertEqual(
            "confirmed",
            classify_vinted_competitor_status(
                interesting_item_count=3,
                distinct_search_terms=1,
                gone_item_count=0,
                max_favorite_count=30,
                same_keyword_deal_count=3,
            ),
        )
        self.assertEqual(
            "priority",
            classify_vinted_competitor_status(
                interesting_item_count=3,
                distinct_search_terms=2,
                gone_item_count=2,
                max_favorite_count=80,
            ),
        )

    def test_competitor_discovery_updates_competitor_table(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "scraper.db"
            save_vinted_rows(
                [
                    {
                        "source": "vinted",
                        "item_id": "1",
                        "name": "Charm cuore",
                        "link": "https://www.vinted.it/items/1-charm",
                        "search_term": "charm",
                        "favorite_count": 18,
                        "seller_name": "Seller A",
                        "seller_profile_url": "https://www.vinted.it/member/111",
                        "seller_member_id": "111",
                        "extracted_at": "2026-10-07T10:00:00",
                    },
                    {
                        "source": "vinted",
                        "item_id": "2",
                        "name": "Pandora cuore",
                        "link": "https://www.vinted.it/items/2-pandora",
                        "search_term": "pandora",
                        "favorite_count": 33,
                        "seller_name": "Seller A",
                        "seller_profile_url": "https://www.vinted.it/member/111",
                        "seller_member_id": "111",
                        "extracted_at": "2026-10-07T10:01:00",
                    },
                ],
                db_path,
                run_kind="competitor_discovery",
            )
            meta = update_vinted_competitors_for_sellers({"111"}, db_path=db_path)
            with closing(sqlite3.connect(db_path)) as connection:
                record = connection.execute(
                    """
                    SELECT seller_name, interesting_item_count, distinct_search_terms, max_favorite_count, status
                    FROM vinted_competitors
                    WHERE seller_member_id = '111'
                    """
                ).fetchone()

        self.assertEqual(1, meta["competitor_sellers_updated"])
        self.assertEqual("Seller A", record[0])
        self.assertEqual(2, record[1])
        self.assertEqual(2, record[2])
        self.assertEqual(33, record[3])
        self.assertEqual("active", record[4])

    def test_three_deals_for_same_keyword_confirm_competitor(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "scraper.db"
            rows = [
                {
                    "source": "vinted",
                    "item_id": str(index),
                    "name": f"Charm {index}",
                    "link": f"https://www.vinted.it/items/{index}-charm",
                    "search_term": " Charm ",
                    "favorite_count": 20 + index,
                    "seller_name": "Seller B",
                    "seller_profile_url": "https://www.vinted.it/member/222",
                    "seller_member_id": "222",
                    "extracted_at": f"2026-10-07T10:0{index}:00",
                }
                for index in range(1, 4)
            ]
            save_vinted_rows(rows, db_path, run_kind="competitor_discovery")
            save_vinted_deal_notifications(
                rows,
                db_path=db_path,
                webhook_target="https://discord.test/webhook",
            )
            with closing(sqlite3.connect(db_path)) as connection:
                record = connection.execute(
                    """
                    SELECT status, top_deal_keyword, same_keyword_deal_count
                    FROM vinted_competitors
                    WHERE seller_member_id = '222'
                    """
                ).fetchone()

        self.assertEqual("confirmed", record[0])
        self.assertEqual("charm", record[1])
        self.assertEqual(3, record[2])


if __name__ == "__main__":
    unittest.main()
