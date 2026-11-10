import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from scraper_app.models import ScrapeOutcome
from scraper_app.runner import _read_vinted_search_specs_file, _resolve_vinted_search_specs, _run_vinted_queries, _run_vinted_queries_once
from scraper_app import runner as runner_module


class RunnerVintedTests(unittest.TestCase):
    def setUp(self) -> None:
        connectivity = patch("scraper_app.runner.wait_for_internet_connection", return_value=False)
        internet = patch("scraper_app.runner.internet_connection_available", return_value=True)
        connectivity.start()
        internet.start()
        self.addCleanup(connectivity.stop)
        self.addCleanup(internet.stop)

    def test_orphan_chrome_sweeper_starts_only_once(self) -> None:
        with (
            patch.object(runner_module, "_ORPHAN_CHROME_SWEEPER_STARTED", False),
            patch.object(runner_module.threading, "Thread") as thread,
        ):
            runner_module._start_orphan_chrome_sweeper()
            runner_module._start_orphan_chrome_sweeper()

        thread.assert_called_once()
        thread.return_value.start.assert_called_once()

    @patch("scraper_app.runner.run_vinted_scraper")
    def test_procacciatore_marks_runs_and_uses_given_isolated_database(self, mocked_scraper) -> None:
        mocked_scraper.return_value = ScrapeOutcome(source="vinted", rows=[], meta={})

        _run_vinted_queries_once(
            [{"search": "gioielli", "max_results": 10, "max_price": None}],
            deal_hunter_min_favorites=10,
            db_path="/tmp/procacciatore.db",
        )

        self.assertEqual("/tmp/procacciatore.db", mocked_scraper.call_args.kwargs["db_path"])
        self.assertEqual("procacciatore", mocked_scraper.call_args.kwargs["run_kind"])
        self.assertTrue(mocked_scraper.call_args.kwargs["monitor_existing_deal_sales_in_search_task"])

    @patch("scraper_app.runner.run_vinted_scraper")
    def test_parallel_sales_worker_disables_in_search_sales_checks(self, mocked_scraper) -> None:
        mocked_scraper.return_value = ScrapeOutcome(source="vinted", rows=[], meta={})

        _run_vinted_queries_once(
            [{"search": "gioielli", "max_results": 10, "max_price": None}],
            deal_hunter_min_favorites=30,
            monitor_existing_deal_sales_in_search_task=True,
            _parallel_deal_sales_worker=True,
        )

        self.assertFalse(mocked_scraper.call_args.kwargs["monitor_existing_deal_sales_in_search_task"])

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
    @patch("scraper_app.runner._start_vinted_deal_sales_background_monitor")
    def test_run_vinted_queries_persists_partial_batch_results(
        self,
        mocked_start_sales_worker,
        mocked_run_vinted_scraper,
        _mocked_consume_stop,
        mocked_write_outcome_json,
    ) -> None:
        mocked_start_sales_worker.return_value = (threading.Event(), Mock())
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

    @patch("scraper_app.runner.time.sleep")
    @patch("scraper_app.runner.consume_stop_after_current_item_request")
    @patch("scraper_app.runner._run_vinted_queries_once")
    @patch("scraper_app.runner._start_vinted_deal_sales_background_monitor")
    def test_run_vinted_queries_keeps_internal_loop_for_deal_hunter(
        self,
        mocked_start_sales_worker,
        mocked_run_once,
        mocked_consume_stop,
        mocked_sleep,
    ) -> None:
        stop_event = threading.Event()
        mocked_start_sales_worker.return_value = (stop_event, Mock())
        stop_checks = {"count": 0}

        def _consume_stop() -> bool:
            stop_checks["count"] += 1
            return stop_checks["count"] >= 8

        mocked_consume_stop.side_effect = _consume_stop
        mocked_run_once.side_effect = [
            ScrapeOutcome(
                source="vinted",
                rows=[{"item_id": "1", "link": "https://www.vinted.it/items/1"}],
                meta={"search_url": "https://www.vinted.it/catalog?search_text=charm", "deal_hunter_enabled": True},
            ),
            ScrapeOutcome(
                source="vinted",
                rows=[{"item_id": "2", "link": "https://www.vinted.it/items/2"}],
                meta={"search_url": "https://www.vinted.it/catalog?search_text=charm", "deal_hunter_enabled": True},
            ),
        ]

        monotonic_values = [0.0, 0.0, 0.6, 1.1, 2.0, 2.0, 2.6, 3.1, 4.0, 4.0]

        def _monotonic() -> float:
            if monotonic_values:
                return monotonic_values.pop(0)
            return 999.0

        with patch("scraper_app.runner.time.monotonic", side_effect=_monotonic):
            outcome = _run_vinted_queries(
                search="charm",
                max_results=25,
                deal_hunter_min_favorites=70,
                deal_hunter_loop_seconds=1,
            )

        self.assertEqual("2", outcome.rows[0]["item_id"])
        self.assertEqual(2, mocked_run_once.call_count)
        self.assertTrue(mocked_run_once.call_args_list[0].kwargs["_parallel_deal_sales_worker"])
        mocked_start_sales_worker.assert_called_once()
        mocked_sleep.assert_called()

    @patch("scraper_app.runner._VINTED_DEAL_HUNTER_RETRY_BASE_SECONDS", 1)
    @patch("scraper_app.runner.time.sleep")
    @patch("scraper_app.runner.consume_stop_after_current_item_request")
    @patch("scraper_app.runner._run_vinted_queries_once")
    @patch("scraper_app.runner._start_vinted_deal_sales_background_monitor")
    def test_procacciatore_retries_failed_cycle_without_failing_job(
        self,
        mocked_start_sales_worker,
        mocked_run_once,
        mocked_consume_stop,
        mocked_sleep,
    ) -> None:
        mocked_start_sales_worker.return_value = (threading.Event(), Mock())
        mocked_run_once.side_effect = [
            TimeoutError("Document did not become ready"),
            ScrapeOutcome(
                source="vinted",
                rows=[{"item_id": "recovered", "link": "https://www.vinted.it/items/recovered"}],
                meta={"deal_hunter_enabled": True},
            ),
        ]
        # Keep running for the retry, then request a clean stop after recovery.
        mocked_consume_stop.side_effect = [False, False, False, False, True]

        outcome = _run_vinted_queries(
            search="gioielli",
            max_results=500,
            deal_hunter_min_favorites=20,
            deal_hunter_loop_seconds=60,
        )

        self.assertEqual("recovered", outcome.rows[0]["item_id"])
        self.assertEqual(2, mocked_run_once.call_count)
        mocked_sleep.assert_called_once_with(1)


if __name__ == "__main__":
    unittest.main()
