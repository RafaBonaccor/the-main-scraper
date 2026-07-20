import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scraper_app.models import ScrapeOutcome
from scraper_app.runner import _read_vinted_search_specs_file, _resolve_vinted_search_specs, _run_vinted_queries


class RunnerVintedTests(unittest.TestCase):
    def test_resolve_vinted_search_specs_from_single_search(self) -> None:
        specs = _resolve_vinted_search_specs(
            {
                "search": "macbook",
                "max_results": 12,
                "max_price": "45,50",
            }
        )

        self.assertEqual(
            [{"search": "macbook", "max_results": 12, "max_price": 45.5}],
            specs,
        )

    def test_read_vinted_search_specs_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "searches.json"
            path.write_text(
                json.dumps(
                    [
                        {"search": "maglietta", "max_results": 20, "max_price": 25},
                        {"search": "felpa", "max_results": 10},
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            specs = _read_vinted_search_specs_file(path, default_max_results=100, default_max_price=None)

        self.assertEqual(
            [
                {"search": "maglietta", "max_results": 20, "max_price": 25.0},
                {"search": "felpa", "max_results": 10, "max_price": None},
            ],
            specs,
        )

    @patch("scraper_app.runner.write_outcome_json")
    @patch("scraper_app.runner.consume_stop_after_current_item_request", side_effect=[False, False])
    @patch("scraper_app.runner.run_vinted_scraper")
    def test_run_vinted_queries_persists_partial_batch_results(
        self,
        mocked_run_vinted_scraper,
        _mocked_consume_stop,
        mocked_write_outcome_json,
    ) -> None:
        partial_snapshots: list[dict] = []

        def _capture_partial(_path, partial_outcome: ScrapeOutcome) -> None:
            partial_snapshots.append(
                {
                    "row_count": len(partial_outcome.rows),
                    "search_terms": list(partial_outcome.meta.get("search_terms", [])),
                }
            )

        mocked_run_vinted_scraper.side_effect = [
            ScrapeOutcome(
                source="vinted",
                rows=[{"item_id": "1", "link": "https://www.vinted.it/items/1"}],
                meta={"search_url": "https://www.vinted.it/catalog?search_text=charm", "deal_hunter_enabled": True},
            ),
            ScrapeOutcome(
                source="vinted",
                rows=[{"item_id": "2", "link": "https://www.vinted.it/items/2"}],
                meta={"search_url": "https://www.vinted.it/catalog?search_text=pandora", "deal_hunter_enabled": True},
            ),
        ]
        mocked_write_outcome_json.side_effect = _capture_partial

        outcome = _run_vinted_queries(
            searches=[
                {"search": "charm", "max_results": 25},
                {"search": "pandora", "max_results": 25},
            ],
            ui_result_json="/tmp/vinted-ui.json",
            deal_hunter_min_favorites=70,
        )

        self.assertEqual(2, len(outcome.rows))
        self.assertEqual(2, mocked_write_outcome_json.call_count)
        self.assertEqual("/tmp/vinted-ui.json", mocked_run_vinted_scraper.call_args_list[0].kwargs["ui_result_json"])
        self.assertEqual("/tmp/vinted-ui.json", mocked_run_vinted_scraper.call_args_list[1].kwargs["ui_result_json"])
        self.assertTrue(mocked_run_vinted_scraper.call_args_list[0].kwargs["keep_browser_open"])
        self.assertTrue(mocked_run_vinted_scraper.call_args_list[1].kwargs["keep_browser_open"])
        self.assertFalse(mocked_run_vinted_scraper.call_args_list[0].kwargs["detach_browser_on_complete"])
        self.assertFalse(mocked_run_vinted_scraper.call_args_list[1].kwargs["detach_browser_on_complete"])
        self.assertEqual(
            [
                {"row_count": 1, "search_terms": ["charm", "pandora"]},
                {"row_count": 2, "search_terms": ["charm", "pandora"]},
            ],
            partial_snapshots,
        )


if __name__ == "__main__":
    unittest.main()
