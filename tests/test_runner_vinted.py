import json
import tempfile
import unittest
from pathlib import Path

from scraper_app.runner import _read_vinted_search_specs_file, _resolve_vinted_search_specs


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


if __name__ == "__main__":
    unittest.main()
