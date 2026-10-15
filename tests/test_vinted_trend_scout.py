import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from scraper_app.vinted_trend_scout import (
    DEFAULT_TREND_QUERIES,
    TrendPolicy,
    VintedTrendStore,
    parse_trend_queries,
    row_matches_trend_query,
    wilson_lower_bound,
)


class VintedTrendScoutTests(unittest.TestCase):
    def test_empty_queries_use_curated_preset_and_dedupe_custom_values(self):
        self.assertEqual(parse_trend_queries("")[0], DEFAULT_TREND_QUERIES[0])
        self.assertEqual(parse_trend_queries("Pandora charm, pandora charm\nTrollbeads charm"), ["Pandora charm", "Trollbeads charm"])
        self.assertEqual(parse_trend_queries("spilli, spille, bracciali"), ["spille", "braccialetti"])

    def test_catalog_noise_is_rejected(self):
        self.assertTrue(row_matches_trend_query({"name": "Jellycat bunny bag charm"}, "jellycat bag charm"))
        self.assertFalse(row_matches_trend_query({"name": "Vintage industrial bobbin"}, "jellycat bag charm"))
        self.assertFalse(row_matches_trend_query({"name": "Generic bag charm"}, "coach bag charm"))
        self.assertTrue(row_matches_trend_query({"name": "Labubu Have a Seat pendant"}, "pop mart labubu"))
        self.assertTrue(row_matches_trend_query(
            {"name": "Jellycat", "link": "https://example.test/items/1-sleutelhanger-draak-merk-jellycat"},
            "jellycat portachiavi",
        ))
        self.assertTrue(row_matches_trend_query({"name": "Collier argent vintage"}, "collane"))
        self.assertTrue(row_matches_trend_query({"name": "Gold brooch flower"}, "spille"))
        self.assertTrue(row_matches_trend_query({"name": "Pandora vintage"}, "gioielli"))
        self.assertFalse(row_matches_trend_query({"name": "Scarpe sportive"}, "collane"))

    def test_wilson_bound_penalizes_tiny_samples(self):
        self.assertLess(wilson_lower_bound(1, 2), 50)
        self.assertGreater(wilson_lower_bound(50, 100), 39)

    def test_removed_query_is_disabled_but_history_is_kept(self):
        with tempfile.TemporaryDirectory() as directory:
            store = VintedTrendStore(Path(directory) / "trend.db")
            store.sync_queries(["jellycat bag charm", "coach bag charm"])
            store.sync_queries(["coach bag charm"])
            due = store.due_queries(20)
            self.assertEqual([row["query_text"] for row in due], ["coach bag charm"])
            self.assertEqual(len(store.summary()), 2)

    def test_query_is_not_judged_before_mature_sample_then_pauses_after_two_weak_windows(self):
        with tempfile.TemporaryDirectory() as directory:
            store = VintedTrendStore(Path(directory) / "trend.db")
            query = "nomination charm"
            old = datetime.now() - timedelta(hours=80)
            store.sync_queries([query], now=old)
            rows = [
                {"link": f"https://example.test/items/{index}", "item_id": str(index), "name": f"Nomination charm {index}"}
                for index in range(30)
            ]
            store.record_scan(query, rows, next_scan_seconds=900, now=old)
            evaluated_at = datetime.now()
            first = store.evaluate_all(TrendPolicy(), now=evaluated_at)[0]
            self.assertEqual(first["status"], "observing")
            too_soon = store.evaluate_all(TrendPolicy(), now=evaluated_at + timedelta(hours=1))[0]
            self.assertEqual(too_soon["status"], "observing")
            second = store.evaluate_all(TrendPolicy(), now=evaluated_at + timedelta(hours=25))[0]
            self.assertEqual(second["status"], "paused_low_rotation")
            self.assertEqual(second["mature_count"], 30)
            frozen = store.evaluate_all(TrendPolicy(), now=evaluated_at + timedelta(hours=26))[0]
            self.assertEqual(frozen["status"], "paused_low_rotation")
            with store.connect() as connection:
                state = connection.execute(
                    "SELECT weak_streak,next_review_at FROM vinted_trend_queries"
                ).fetchone()
            self.assertEqual(state["weak_streak"], 2)
            self.assertTrue(state["next_review_at"])

    def test_unavailable_items_do_not_poison_sell_through_denominator(self):
        with tempfile.TemporaryDirectory() as directory:
            store = VintedTrendStore(Path(directory) / "trend.db")
            query = "trifari spilla"
            old = datetime.now() - timedelta(hours=80)
            store.sync_queries([query], now=old)
            rows = [
                {"link": f"https://example.test/items/{index}", "item_id": str(index), "name": f"Trifari spilla {index}"}
                for index in range(31)
            ]
            store.record_scan(query, rows, next_scan_seconds=900, now=old)
            store.save_item_check(rows[0]["link"], sold=False, unavailable=True)
            result = store.evaluate_all(TrendPolicy())[0]
            self.assertEqual(result["mature_count"], 30)

    def test_transient_browser_error_does_not_count_as_weak_sales_evaluation(self):
        with tempfile.TemporaryDirectory() as directory:
            store = VintedTrendStore(Path(directory) / "trend.db")
            query = "swarovski spilla"
            old = datetime.now() - timedelta(hours=80)
            store.sync_queries([query], now=old)
            store.record_scan(
                query,
                [{"link": f"https://example.test/items/{index}", "name": f"Swarovski spilla {index}"} for index in range(30)],
                next_scan_seconds=900,
                now=old,
            )
            evaluated_at = datetime.now()
            store.evaluate_all(TrendPolicy(), now=evaluated_at)
            store.record_scan_error(query, RuntimeError("rate limited"), policy=TrendPolicy(), now=evaluated_at + timedelta(hours=25))
            state = store.evaluate_all(TrendPolicy(), now=evaluated_at + timedelta(hours=25))[0]
            self.assertEqual(state["status"], "observing")
            with store.connect() as connection:
                weak = connection.execute("SELECT weak_streak FROM vinted_trend_queries").fetchone()[0]
            self.assertEqual(weak, 1)


if __name__ == "__main__":
    unittest.main()
