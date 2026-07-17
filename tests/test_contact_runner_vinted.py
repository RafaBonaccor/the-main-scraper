import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scraper_app.contact_runner import _read_vinted_offer_items_file, run_contact_action


class ContactRunnerVintedTests(unittest.TestCase):
    def test_read_vinted_offer_items_file_supports_json_dict_items(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "offer_items.json"
            path.write_text(
                json.dumps(
                    [
                        {"link": "https://www.vinted.it/items/1", "base_price": "2.50"},
                        {"link": "https://www.vinted.it/items/2", "price_value": 4.9},
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            items = _read_vinted_offer_items_file(path)

        self.assertEqual(
            [
                {"link": "https://www.vinted.it/items/1", "item_id": "", "base_price": "2.50", "base_total_price": ""},
                {"link": "https://www.vinted.it/items/2", "item_id": "", "base_price": 4.9, "base_total_price": ""},
            ],
            items,
        )

    @patch("scraper_app.contact_runner.run_vinted_offer_action")
    def test_run_contact_action_passes_offer_discount_percent(self, mocked_run_vinted_offer_action) -> None:
        mocked_run_vinted_offer_action.return_value = {"ok": True}

        run_contact_action(
            "vinted",
            link="https://www.vinted.it/items/1",
            base_price="4.50",
            offer_discount_percent=22.5,
            db_path="custom-vinted.db",
            submit=True,
        )

        self.assertEqual(22.5, mocked_run_vinted_offer_action.call_args.kwargs["offer_discount_percent"])
        self.assertEqual("custom-vinted.db", mocked_run_vinted_offer_action.call_args.kwargs["db_path"])


if __name__ == "__main__":
    unittest.main()
