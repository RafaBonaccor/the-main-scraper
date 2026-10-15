import sqlite3
import tempfile
import unittest
from pathlib import Path

from scraper_app.subito_monitor import (
    build_caregiver_queries,
    classify_caregiver_row,
    parse_monitor_values,
    persist_subito_monitor_cycle,
    row_matches_location_target,
)


class SubitoCaregiverMonitorTests(unittest.TestCase):
    def test_builds_one_query_per_term_and_location(self):
        locations = parse_monitor_values("Roma, Monterotondo\nRoma", ())
        queries = build_caregiver_queries(["badante part time"], locations)
        self.assertEqual(["Roma", "Monterotondo"], locations)
        self.assertEqual(
            ["badante part time Roma", "badante part time Monterotondo"],
            [item["query"] for item in queries],
        )

    def test_requires_caregiver_and_explicit_part_time_markers(self):
        accepted, reason = classify_caregiver_row(
            {"title": "Badante per anziana", "schedule": "Part time"}
        )
        self.assertTrue(accepted)
        self.assertEqual("caregiver-part-time", reason)
        self.assertTrue(classify_caregiver_row({"title": "Badante servizio ad ore"})[0])
        self.assertFalse(classify_caregiver_row({"title": "Badante convivente", "schedule": "Full time"})[0])
        self.assertFalse(classify_caregiver_row({"title": "Cameriere", "schedule": "Part time"})[0])

    def test_location_match_does_not_accept_unrelated_roma_province_listing(self):
        row = {"title": "Badante part time", "location": "Roma (RM)", "raw_text": "Badante part time"}
        self.assertTrue(row_matches_location_target(row, "Roma"))
        self.assertFalse(row_matches_location_target(row, "Monterotondo"))
        self.assertTrue(
            row_matches_location_target(
                {**row, "title": "Badante part time Roma Nord"},
                "Roma Nord",
            )
        )

    def test_sqlite_history_marks_only_first_sighting_as_new(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "subito_jobs.db"
            first_rows = [{"link": "https://example.test/job/1", "title": "Badante part time"}]
            new_rows, meta = persist_subito_monitor_cycle(
                db_path, first_rows, cycle_index=1, config={}, errors=[]
            )
            self.assertEqual(1, len(new_rows))
            self.assertTrue(first_rows[0]["monitor_is_new"])

            second_rows = [{"link": "https://example.test/job/1", "title": "Badante part time"}]
            repeated_rows, _ = persist_subito_monitor_cycle(
                db_path, second_rows, cycle_index=2, config={}, errors=[]
            )
            self.assertEqual([], repeated_rows)
            self.assertFalse(second_rows[0]["monitor_is_new"])
            self.assertTrue(Path(meta["db_path"]).exists())
            with sqlite3.connect(db_path) as connection:
                seen_count = connection.execute(
                    "SELECT seen_count FROM subito_monitor_listings"
                ).fetchone()[0]
            self.assertEqual(2, seen_count)


if __name__ == "__main__":
    unittest.main()
