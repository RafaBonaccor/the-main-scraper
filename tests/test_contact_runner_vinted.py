import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scraper_app.contact_runner import _read_vinted_offer_items_file, _read_vinted_upload_items_file, run_contact_action


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

    def test_read_vinted_upload_items_file_supports_manifest_dict(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            photo = Path(temp_dir) / "1.jpg"
            photo.write_text("x", encoding="utf-8")
            path = Path(temp_dir) / "upload_items.json"
            path.write_text(
                json.dumps(
                    {
                        "generated_at": "2026-07-26T12:00:00",
                        "items": [
                            {
                                "title": "Charm",
                                "description": "Descrizione completa",
                                "price": "12,50",
                                "category": "Braccialetti",
                                "brand": "No Label",
                                "condition": "Ottime",
                                "material": "Acciaio",
                                "photo_paths": [str(photo)],
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            items = _read_vinted_upload_items_file(path)

        self.assertEqual(
            [
                {
                    "title": "Charm",
                    "description": "Descrizione completa",
                    "price": "12,50",
                    "category": "Braccialetti",
                    "brand": "No Label",
                    "condition": "Ottime",
                    "material": "Acciaio",
                    "photo_paths": [str(photo)],
                    "openai_used": False,
                    "openai_model": "",
                }
            ],
            items,
        )

    @patch("scraper_app.contact_runner.run_vinted_upload_action")
    def test_run_contact_action_dispatches_single_vinted_upload_from_manifest(self, mocked_run_vinted_upload_action) -> None:
        mocked_run_vinted_upload_action.return_value = {"ok": True}
        with tempfile.TemporaryDirectory() as temp_dir:
            photo = Path(temp_dir) / "1.jpg"
            photo.write_text("x", encoding="utf-8")
            path = Path(temp_dir) / "upload_items.json"
            path.write_text(
                json.dumps(
                    {
                        "items": [
                            {
                                "title": "Charm",
                                "description": "Descrizione completa",
                                "price": "12,50",
                                "category": "Braccialetti",
                                "brand": "No Label",
                                "condition": "Ottime",
                                "material": "Acciaio",
                                "photo_paths": [str(photo)],
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            run_contact_action(
                "vinted_upload",
                items_file=str(path),
                submit=True,
                keep_browser_open=True,
            )

        self.assertEqual("Charm", mocked_run_vinted_upload_action.call_args.kwargs["title"])
        self.assertEqual("Braccialetti", mocked_run_vinted_upload_action.call_args.kwargs["category"])

    @patch("scraper_app.contact_runner.run_vinted_upload_action")
    def test_run_contact_action_dispatches_single_vinted_upload(self, mocked_run_vinted_upload_action) -> None:
        mocked_run_vinted_upload_action.return_value = {"ok": True}
        with tempfile.TemporaryDirectory() as temp_dir:
            photo = Path(temp_dir) / "1.jpg"
            photo.write_text("x", encoding="utf-8")

            run_contact_action(
                "vinted_upload",
                title="Charm",
                description="Descrizione completa",
                price="12,50",
                category="Braccialetti",
                brand="No Label",
                condition="Ottime",
                material="Acciaio",
                photo_paths=[str(photo)],
                submit=True,
            )

        self.assertEqual("Charm", mocked_run_vinted_upload_action.call_args.kwargs["title"])
        self.assertEqual("12,50", mocked_run_vinted_upload_action.call_args.kwargs["price"])
        self.assertEqual("Braccialetti", mocked_run_vinted_upload_action.call_args.kwargs["category"])
        self.assertEqual(True, mocked_run_vinted_upload_action.call_args.kwargs["submit"])

    @patch("scraper_app.contact_runner.run_vinted_upload_batch")
    def test_run_contact_action_dispatches_batch_vinted_upload_from_manifest(self, mocked_run_vinted_upload_batch) -> None:
        mocked_run_vinted_upload_batch.return_value = {"ok": True}
        with tempfile.TemporaryDirectory() as temp_dir:
            photo = Path(temp_dir) / "1.jpg"
            photo.write_text("x", encoding="utf-8")
            photo2 = Path(temp_dir) / "2.jpg"
            photo2.write_text("x", encoding="utf-8")
            path = Path(temp_dir) / "upload_items.json"
            path.write_text(
                json.dumps(
                    {
                        "items": [
                            {
                                "title": "Charm",
                                "description": "Descrizione completa",
                                "price": "12,50",
                                "category": "Braccialetti",
                                "brand": "No Label",
                                "condition": "Ottime",
                                "material": "Acciaio",
                                "photo_paths": [str(photo)],
                            },
                            {
                                "title": "Collana",
                                "description": "Seconda descrizione",
                                "price": "20",
                                "category": "Braccialetti",
                                "brand": "No Label",
                                "condition": "Ottime",
                                "material": "Acciaio",
                                "photo_paths": [str(photo2)],
                            },
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            run_contact_action(
                "vinted_upload",
                items_file=str(path),
                submit=False,
                delay_between_seconds=3,
            )

        self.assertEqual(2, len(mocked_run_vinted_upload_batch.call_args.kwargs["items"]))
        self.assertEqual(3, mocked_run_vinted_upload_batch.call_args.kwargs["delay_between_seconds"])


if __name__ == "__main__":
    unittest.main()
