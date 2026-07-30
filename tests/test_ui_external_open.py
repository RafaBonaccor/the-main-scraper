import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from scraper_app.ui import (
    ScraperApp,
    build_vinted_deal_hunter_search_specs,
    build_vinted_search_target_url,
    detect_vinted_category_label_from_url,
    normalize_vinted_upload_photo_paths,
    normalize_vinted_upload_queue_item,
    open_external_target,
    resolve_vinted_category_url,
)


class UiExternalOpenTests(unittest.TestCase):
    class _Var:
        def __init__(self, value):
            self.value = value

        def get(self):
            return self.value

        def set(self, value):
            self.value = value

    class _Root:
        def __init__(self):
            self.destroy_called = False

        def after(self, _ms, callback):
            callback()
            return "after-id"

        def after_cancel(self, _after_id):
            return None

        def destroy(self):
            self.destroy_called = True

    class _TextWidget:
        def __init__(self, value: str = ""):
            self.value = value

        def get(self, *_args, **_kwargs):
            return self.value

        def delete(self, *_args, **_kwargs):
            self.value = ""

        def insert(self, *_args):
            self.value = _args[-1]

    @patch("scraper_app.ui.os.startfile", create=True)
    @patch("scraper_app.ui.os.name", "nt")
    def test_open_external_target_uses_startfile_on_windows(self, mocked_startfile) -> None:
        result = open_external_target("https://www.vinted.it/items/1")

        self.assertTrue(result)
        mocked_startfile.assert_called_once_with("https://www.vinted.it/items/1")

    @patch("scraper_app.ui.subprocess.Popen")
    @patch("scraper_app.ui.sys.platform", "darwin")
    @patch("scraper_app.ui.os.name", "posix")
    def test_open_external_target_uses_open_on_macos(self, mocked_popen) -> None:
        result = open_external_target("https://www.vinted.it/items/1")

        self.assertTrue(result)
        mocked_popen.assert_called_once()
        self.assertEqual(["open", "https://www.vinted.it/items/1"], mocked_popen.call_args.args[0])

    def test_open_external_target_rejects_empty_values(self) -> None:
        self.assertFalse(open_external_target(""))

    def test_resolve_vinted_category_url_supports_presets(self) -> None:
        self.assertEqual(
            "https://www.vinted.it/catalog/1187-accessories",
            resolve_vinted_category_url("Accessori donna"),
        )
        self.assertEqual(
            "https://www.vinted.it/catalog/2994-electronics",
            resolve_vinted_category_url("Elettronica"),
        )
        self.assertEqual(
            "https://www.vinted.it/catalog/21-jewellery",
            resolve_vinted_category_url("Gioielli donna"),
        )

    def test_build_vinted_search_target_url_supports_direct_category(self) -> None:
        self.assertEqual(
            "https://www.vinted.it/catalog/82-accessories",
            build_vinted_search_target_url("", "Accessori uomo"),
        )

    def test_build_vinted_search_target_url_supports_keyword_inside_category(self) -> None:
        self.assertEqual(
            "https://www.vinted.it/catalog/16-shoes?search_text=nike",
            build_vinted_search_target_url("nike", "Scarpe donna"),
        )

    def test_detect_vinted_category_label_from_url_matches_category_pages(self) -> None:
        self.assertEqual(
            "Scarpe uomo",
            detect_vinted_category_label_from_url("https://www.vinted.it/catalog/1231-shoes?search_text=adidas"),
        )

    def test_normalize_vinted_upload_photo_paths_dedupes_and_resolves(self) -> None:
        with TemporaryDirectory() as temp_dir:
            first = Path(temp_dir) / "1.jpg"
            second = Path(temp_dir) / "2.jpg"
            first.write_text("x", encoding="utf-8")
            second.write_text("x", encoding="utf-8")

            result = normalize_vinted_upload_photo_paths([first, str(first), second])

        self.assertEqual(2, len(result))
        self.assertEqual(str(first.resolve()), result[0])
        self.assertEqual(str(second.resolve()), result[1])

    def test_normalize_vinted_upload_queue_item_requires_core_fields(self) -> None:
        with self.assertRaises(ValueError):
            normalize_vinted_upload_queue_item({"title": "", "description": "x", "price": "10", "photo_paths": ["/tmp/x.jpg"]})

        with self.assertRaises(ValueError):
            normalize_vinted_upload_queue_item({"title": "Charm", "description": "", "price": "10", "photo_paths": ["/tmp/x.jpg"]})

        with self.assertRaises(ValueError):
            normalize_vinted_upload_queue_item({"title": "Charm", "description": "Test", "price": "", "photo_paths": ["/tmp/x.jpg"]})

        with self.assertRaises(ValueError):
            normalize_vinted_upload_queue_item({"title": "Charm", "description": "Test", "price": "10", "category": "", "brand": "No Label", "condition": "Ottime", "material": "Acciaio", "photo_paths": ["/tmp/x.jpg"]})

    def test_normalize_vinted_upload_queue_item_returns_stable_payload(self) -> None:
        with TemporaryDirectory() as temp_dir:
            photo = Path(temp_dir) / "1.jpg"
            photo.write_text("x", encoding="utf-8")

            payload = normalize_vinted_upload_queue_item(
                {
                    "id": "item-1",
                    "created_at": "2026-07-26T10:00:00",
                    "title": "Charm Pandora",
                    "description": "Descrizione completa",
                    "price": "12,50",
                    "category": "Braccialetti",
                    "brand": "No Label",
                    "condition": "Ottime",
                    "material": "Acciaio",
                    "photo_paths": [str(photo)],
                }
            )

        self.assertEqual("item-1", payload["id"])
        self.assertEqual("2026-07-26T10:00:00", payload["created_at"])
        self.assertEqual("Charm Pandora", payload["title"])
        self.assertEqual("Descrizione completa", payload["description"])
        self.assertEqual("12,50", payload["price"])
        self.assertEqual("Braccialetti", payload["category"])
        self.assertEqual("No Label", payload["brand"])
        self.assertEqual("Ottime", payload["condition"])
        self.assertEqual("Acciaio", payload["material"])
        self.assertEqual([str(photo.resolve())], payload["photo_paths"])

    def test_parse_vinted_ai_discord_payload_supports_json(self) -> None:
        with TemporaryDirectory() as temp_dir:
            photo = Path(temp_dir) / "photo.png"
            photo.write_text("x", encoding="utf-8")
            app = object.__new__(ScraperApp)

            payload = ScraperApp._parse_vinted_ai_discord_payload(
                app,
                (
                    "{"
                    f"\"title\": \"Charm Pandora\", "
                    f"\"description\": \"Silver charm\", "
                    f"\"price\": \"12.50\", "
                    f"\"photo_paths\": [\"{photo}\"]"
                    "}"
                ),
            )

        self.assertEqual("Charm Pandora", payload["title"])
        self.assertEqual("Silver charm", payload["description"])
        self.assertEqual("12.50", payload["price"])
        self.assertEqual([str(photo.resolve())], payload["photo_paths"])

    def test_import_vinted_ai_discord_payload_updates_form_and_references(self) -> None:
        with TemporaryDirectory() as temp_dir:
            photo = Path(temp_dir) / "photo.png"
            photo.write_text("x", encoding="utf-8")
            app = object.__new__(ScraperApp)
            app.vinted_ai_discord_payload_widget = self._TextWidget(
                "\n".join(
                    [
                        "title: Charm Pandora",
                        "description: Silver charm with stones",
                        "price: 12.50",
                        "category: Braccialetti",
                        "brand: No Label",
                        "condition: Ottime",
                        "material: Acciaio",
                        f"photo_paths: {photo}",
                    ]
                )
            )
            app.vinted_upload_title_var = self._Var("")
            app.vinted_upload_price_var = self._Var("")
            app.vinted_upload_category_var = self._Var("")
            app.vinted_upload_brand_var = self._Var("")
            app.vinted_upload_condition_var = self._Var("")
            app.vinted_upload_material_var = self._Var("")
            app.vinted_upload_description_widget = self._TextWidget("")
            app.vinted_upload_selected_photo_paths = []
            app.vinted_ai_reference_photo_paths = []
            app.vinted_ai_discord_context = ""
            app.vinted_ai_status_var = self._Var("")
            app.vinted_upload_status_var = self._Var("")
            app._update_vinted_upload_photos_summary = Mock()
            app._update_vinted_ai_reference_photos_summary = Mock()
            app._show_toast = Mock()

            ScraperApp._import_vinted_ai_discord_payload(app)

        self.assertEqual("Charm Pandora", app.vinted_upload_title_var.get())
        self.assertEqual("12.50", app.vinted_upload_price_var.get())
        self.assertEqual("Braccialetti", app.vinted_upload_category_var.get())
        self.assertEqual("No Label", app.vinted_upload_brand_var.get())
        self.assertEqual("Ottime", app.vinted_upload_condition_var.get())
        self.assertEqual("Acciaio", app.vinted_upload_material_var.get())
        self.assertEqual("Silver charm with stones", app.vinted_upload_description_widget.get())
        self.assertEqual([str(photo.resolve())], app.vinted_upload_selected_photo_paths)
        self.assertEqual([str(photo.resolve())], app.vinted_ai_reference_photo_paths)
        self.assertIn("Title: Charm Pandora", app.vinted_ai_discord_context)
        self.assertIn("Description: Silver charm with stones", app.vinted_ai_discord_context)
        app._update_vinted_upload_photos_summary.assert_called_once()
        app._update_vinted_ai_reference_photos_summary.assert_called_once()
        app._show_toast.assert_called_once()

    def test_import_vinted_ai_discord_payload_completes_partial_json_with_ai(self) -> None:
        with TemporaryDirectory() as temp_dir:
            photo = Path(temp_dir) / "photo.png"
            photo.write_text("x", encoding="utf-8")
            app = object.__new__(ScraperApp)
            app.vinted_ai_discord_payload_widget = self._TextWidget(
                json.dumps({"title": "Charm Pandora", "description": "Silver charm", "photo_paths": [str(photo)]})
            )
            app.vinted_upload_title_var = self._Var("")
            app.vinted_upload_price_var = self._Var("")
            app.vinted_upload_category_var = self._Var("")
            app.vinted_upload_brand_var = self._Var("")
            app.vinted_upload_condition_var = self._Var("")
            app.vinted_upload_material_var = self._Var("")
            app.vinted_upload_description_widget = self._TextWidget("")
            app.vinted_upload_selected_photo_paths = []
            app.vinted_ai_reference_photo_paths = []
            app.vinted_ai_discord_context = ""
            app.vinted_ai_status_var = self._Var("")
            app.vinted_upload_status_var = self._Var("")
            app._update_vinted_upload_photos_summary = Mock()
            app._update_vinted_ai_reference_photos_summary = Mock()
            app._show_toast = Mock()
            app._structure_vinted_upload_payload_with_ai = Mock(
                return_value={
                    "title": "Charm Pandora",
                    "description": "Silver charm",
                    "price": "12.50",
                    "category": "Braccialetti",
                    "brand": "No Label",
                    "condition": "Ottime",
                    "material": "Acciaio",
                    "photo_paths": [str(photo.resolve())],
                }
            )

            ScraperApp._import_vinted_ai_discord_payload(app)

        app._structure_vinted_upload_payload_with_ai.assert_called_once()
        self.assertEqual("12.50", app.vinted_upload_price_var.get())
        self.assertEqual("Braccialetti", app.vinted_upload_category_var.get())
        self.assertEqual([str(photo.resolve())], app.vinted_upload_selected_photo_paths)

    def test_import_vinted_ai_discord_payload_reports_ai_error_when_ai_is_unavailable(self) -> None:
        app = object.__new__(ScraperApp)
        app.vinted_ai_discord_payload_widget = self._TextWidget(
            json.dumps({"title": "Charm Pandora", "description": "Silver charm"})
        )
        app.vinted_upload_title_var = self._Var("")
        app.vinted_upload_price_var = self._Var("")
        app.vinted_upload_category_var = self._Var("")
        app.vinted_upload_brand_var = self._Var("")
        app.vinted_upload_condition_var = self._Var("")
        app.vinted_upload_material_var = self._Var("")
        app.vinted_upload_description_widget = self._TextWidget("")
        app.vinted_upload_selected_photo_paths = []
        app.vinted_ai_reference_photo_paths = []
        app.vinted_ai_discord_context = ""
        app.vinted_ai_status_var = self._Var("")
        app.vinted_upload_status_var = self._Var("")
        app._update_vinted_upload_photos_summary = Mock()
        app._update_vinted_ai_reference_photos_summary = Mock()
        app._show_toast = Mock()
        app._structure_vinted_upload_payload_with_ai = Mock(
            side_effect=ValueError("OPENAI_API_KEY non trovato nell'ambiente o nel secret project store.")
        )

        with patch("scraper_app.ui.messagebox.showerror") as mocked_showerror:
            ScraperApp._import_vinted_ai_discord_payload(app)

        mocked_showerror.assert_called_once()
        self.assertIn("OPENAI_API_KEY", mocked_showerror.call_args.args[1])
        self.assertIn("Discord AI structuring failed", app.vinted_ai_status_var.get())
        app._show_toast.assert_called_once()

    def test_clean_vinted_ai_discord_text_strips_command_prefix(self) -> None:
        app = object.__new__(ScraperApp)

        cleaned = ScraperApp._clean_vinted_ai_discord_text(
            app,
            "\n".join(
                [
                    "!upload researcher",
                    "Nome testing",
                    "Descrizione un test fico",
                    "Prezzo 10 euro",
                ]
            ),
        )

        self.assertEqual("Nome testing\nDescrizione un test fico\nPrezzo 10 euro", cleaned)

    def test_parse_vinted_ai_discord_payload_supports_free_text(self) -> None:
        app = object.__new__(ScraperApp)

        payload = ScraperApp._parse_vinted_ai_discord_payload(
            app,
            "\n".join(
                [
                    "upload researcher",
                    "Nome testing descrizione un test fico prezzo 10 euro materiale acciaio categoria gioielli",
                    "Immagine",
                ]
            ),
        )

        self.assertEqual("testing", payload["title"])
        self.assertEqual("un test fico", payload["description"])
        self.assertEqual("10 euro", payload["price"])
        self.assertEqual("gioielli", payload["category"])
        self.assertEqual("No Label", payload["brand"])
        self.assertEqual("Ottime", payload["condition"])
        self.assertEqual("acciaio", payload["material"])

    def test_build_vinted_deal_hunter_search_specs_uses_category_and_dedupes_terms(self) -> None:
        specs = build_vinted_deal_hunter_search_specs(
            "charm, pandora, charm",
            "Gioielli donna",
            250,
            max_price=19.9,
        )

        self.assertEqual(2, len(specs))
        self.assertEqual("charm | Gioielli donna", specs[0]["display_search"])
        self.assertEqual(
            "https://www.vinted.it/catalog/21-jewellery?search_text=charm",
            specs[0]["search"],
        )
        self.assertEqual(250, specs[0]["max_results"])
        self.assertEqual(19.9, specs[0]["max_price"])

    def test_build_vinted_deal_hunter_search_specs_uses_category_only_when_terms_empty(self) -> None:
        specs = build_vinted_deal_hunter_search_specs(
            "",
            "Elettronica",
            250,
            max_price=19.9,
        )

        self.assertEqual(1, len(specs))
        self.assertEqual("https://www.vinted.it/catalog/2994-electronics", specs[0]["search"])
        self.assertEqual("Elettronica", specs[0]["display_search"])

    def test_build_vinted_deal_hunter_search_specs_expands_monitoring_category(self) -> None:
        specs = build_vinted_deal_hunter_search_specs(
            "charm, monitor",
            "Monitoraggio",
            120,
            max_price=25.0,
        )

        self.assertEqual(12, len(specs))
        self.assertEqual(
            "https://www.vinted.it/catalog/1187-accessories?search_text=charm",
            specs[0]["search"],
        )
        self.assertEqual(
            "charm | Accessori donna | Monitoraggio",
            specs[0]["display_search"],
        )
        self.assertEqual("Monitoraggio", specs[0]["category_label"])
        self.assertEqual(120, specs[0]["max_results"])
        self.assertEqual(25.0, specs[0]["max_price"])

    def test_build_vinted_deal_hunter_search_specs_expands_monitoring_category_without_terms(self) -> None:
        specs = build_vinted_deal_hunter_search_specs(
            "",
            "Monitoraggio",
            80,
            max_price=12.5,
        )

        self.assertEqual(72, len(specs))
        self.assertEqual("https://www.vinted.it/catalog/1187-accessories?search_text=nike", specs[0]["search"])
        self.assertEqual("nike | Accessori donna | Monitoraggio", specs[0]["display_search"])
        self.assertEqual("Monitoraggio", specs[0]["category_label"])

    def test_submitted_offer_demotes_vinted_hot_row(self) -> None:
        rows = ScraperApp._demote_vinted_rows_with_submitted_offers(
            object(),
            [
                {
                    "offer_already_submitted": True,
                    "evaluation_label": "da valutare assolutamente",
                    "deal_hunter_match": True,
                    "deal_hunter_label": "affare 24h/70+",
                }
            ],
        )

        self.assertEqual("da valutare", rows[0]["evaluation_label"])
        self.assertFalse(rows[0]["deal_hunter_match"])
        self.assertEqual("affare 24h/70+ - offerta gia inviata", rows[0]["deal_hunter_label"])

    def test_submitted_offer_rows_are_sorted_after_other_vinted_rows(self) -> None:
        app = object.__new__(ScraperApp)
        app.result_sort_var = type("SortVar", (), {"get": lambda self: "Valutazione Vinted"})()
        app.result_sort_reverse = False

        rows = [
            {
                "source": "vinted",
                "name": "Ricercato",
                "evaluation_label": "da valutare assolutamente",
                "favorite_count": 40,
                "tag": "ricercato",
                "offer_already_submitted": False,
            },
            {
                "source": "vinted",
                "name": "Offerto",
                "evaluation_label": "da valutare",
                "favorite_count": 99,
                "tag": "",
                "offer_already_submitted": True,
            },
        ]

        ordered = ScraperApp._sorted_result_rows(app, rows)

        self.assertEqual("Ricercato", ordered[0]["name"])
        self.assertEqual("Offerto", ordered[1]["name"])

    def test_validated_vinted_discord_webhook_url_requires_valid_url_when_enabled(self) -> None:
        app = object.__new__(ScraperApp)
        app.vinted_discord_notifications_var = self._Var(True)
        app.vinted_discord_webhook_url_var = self._Var("https://example.com/webhook")

        with self.assertRaises(ValueError):
            ScraperApp._validated_vinted_discord_webhook_url(app)

    @patch("scraper_app.ui.messagebox.showinfo")
    @patch("scraper_app.ui.messagebox.askyesno", return_value=True)
    @patch("scraper_app.ui.send_discord_webhook_message", return_value={"ok": True, "sent_at": "2026-07-21T12:00:00"})
    def test_send_selected_vinted_to_discord_uses_selected_row(
        self,
        mocked_send,
        _mocked_confirm,
        mocked_info,
    ) -> None:
        app = object.__new__(ScraperApp)
        app.vinted_discord_notifications_var = self._Var(True)
        app.vinted_discord_webhook_url_var = self._Var("https://discord.com/api/webhooks/test/token")
        app.vinted_status_var = self._Var("")
        app._append_log = lambda _text: None
        app._get_selected_row = lambda: {
            "source": "vinted",
            "name": "Charm Pandora",
            "link": "https://www.vinted.it/items/1",
        }

        ScraperApp._send_selected_vinted_to_discord(app)

        mocked_send.assert_called_once()
        mocked_info.assert_called_once()
        self.assertEqual("Annuncio Vinted inviato su Discord.", app.vinted_status_var.get())

    @patch("scraper_app.ui.messagebox.showerror")
    @patch("scraper_app.ui.messagebox.askyesno", return_value=True)
    @patch("scraper_app.ui.send_discord_webhook_message", return_value={"ok": False, "error": "HTTP 403: error code: 1010"})
    def test_send_selected_vinted_to_discord_reports_masked_webhook_target_on_failure(
        self,
        _mocked_send,
        _mocked_confirm,
        mocked_error,
    ) -> None:
        logs: list[str] = []
        app = object.__new__(ScraperApp)
        app.vinted_discord_notifications_var = self._Var(True)
        app.vinted_discord_webhook_url_var = self._Var("https://discord.com/api/webhooks/test/abcd1234efgh5678")
        app.vinted_status_var = self._Var("")
        app._append_log = logs.append
        app._get_selected_row = lambda: {
            "source": "vinted",
            "name": "Charm Pandora",
            "link": "https://www.vinted.it/items/1",
        }

        ScraperApp._send_selected_vinted_to_discord(app)

        self.assertTrue(any("discord-webhook:abcd…5678" in line for line in logs))
        mocked_error.assert_called_once()
        self.assertIn("discord-webhook:abcd…5678", mocked_error.call_args.args[1])

    @patch("scraper_app.ui.send_discord_webhook_message", return_value={"ok": True})
    def test_apply_vinted_access_status_sends_discord_notification_once_per_process(self, mocked_send) -> None:
        logs: list[str] = []
        app = object.__new__(ScraperApp)
        app.vinted_discord_notifications_var = self._Var(True)
        app.vinted_discord_webhook_url_var = self._Var("https://discord.com/api/webhooks/test/abcd1234efgh5678")
        app.vinted_status_var = self._Var("")
        app.vinted_profile_access_var = self._Var("")
        app.vinted_access_warning_shown_for_process = False
        app.vinted_login_discord_notified_for_process = False
        app.vinted_last_access_marker_present = None
        app._append_log = logs.append
        app._format_status_timestamp = lambda value: value

        ScraperApp._apply_vinted_access_status(
            app,
            {
                "marker_present": False,
                "expected_alt": "bonaccarla",
                "checked_at": "2026-07-21T11:30:00",
                "current_url": "https://www.vinted.it/catalog/21-jewellery",
            },
        )
        ScraperApp._apply_vinted_access_status(
            app,
            {
                "marker_present": False,
                "expected_alt": "bonaccarla",
                "checked_at": "2026-07-21T11:31:00",
                "current_url": "https://www.vinted.it/catalog/21-jewellery",
            },
        )

        self.assertEqual(1, mocked_send.call_count)
        self.assertTrue(app.vinted_login_discord_notified_for_process)
        self.assertTrue(any("notifica login Vinted inviata" in line for line in logs))
        self.assertIn("attachment_paths", mocked_send.call_args.kwargs)

    @patch("scraper_app.ui.send_discord_webhook_message", return_value={"ok": True})
    def test_notify_scraper_issue_on_discord_sends_attachments_and_dedupes(self, mocked_send) -> None:
        with TemporaryDirectory() as temp_dir:
            screenshot = Path(temp_dir) / "screenshot.png"
            screenshot.write_bytes(b"image")
            app = object.__new__(ScraperApp)
            app.vinted_discord_notifications_var = self._Var(True)
            app.vinted_discord_webhook_url_var = self._Var("https://discord.com/api/webhooks/test/token")
            app.process_kind = "scrape"
            app.current_run_source = "vinted"
            app._discord_error_notification_cache = {}
            app._append_log = lambda _text: None
            app._collect_recent_error_report_attachments = lambda: [str(screenshot.resolve())]

            first = ScraperApp._notify_scraper_issue_on_discord(
                app,
                "Ricerca Vinted fallita",
                "Errore durante il processo.",
                dedupe_key="scrape-failed",
            )
            second = ScraperApp._notify_scraper_issue_on_discord(
                app,
                "Ricerca Vinted fallita",
                "Errore durante il processo.",
                dedupe_key="scrape-failed",
            )

        self.assertTrue(first)
        self.assertTrue(second)
        mocked_send.assert_called_once()
        self.assertEqual([str(screenshot.resolve())], mocked_send.call_args.kwargs["attachment_paths"])

    @patch("scraper_app.ui.send_discord_webhook_message", return_value={"ok": True})
    def test_notify_scraper_issue_on_discord_respects_disabled_notifications(self, mocked_send) -> None:
        app = object.__new__(ScraperApp)
        app.vinted_discord_notifications_var = self._Var(False)
        app.vinted_discord_webhook_url_var = self._Var("https://discord.com/api/webhooks/test/token")
        app.process_kind = ""
        app.current_run_source = ""
        app._discord_error_notification_cache = {}
        app._append_log = lambda _text: None

        result = ScraperApp._notify_scraper_issue_on_discord(app, "Errore", "Messaggio")

        self.assertFalse(result)
        mocked_send.assert_not_called()

    def test_persisted_ui_settings_store_discord_webhook(self) -> None:
        with TemporaryDirectory() as temp_dir:
            app = object.__new__(ScraperApp)
            app.ui_settings_path = Path(temp_dir) / "ui_settings.json"
            app.browser_mode_var = self._Var("sessione_persistente")
            app.browser_user_data_dir_var = self._Var("/Users/test/Library/Application Support/Google/Chrome")
            app.browser_profile_directory_var = self._Var("Profile 2")
            app.vinted_discord_notifications_var = self._Var(True)
            app.vinted_discord_webhook_url_var = self._Var("https://discord.com/api/webhooks/test/token")
            app._persist_ui_settings_after_id = None

            ScraperApp._persist_ui_settings(app)

            payload = app.ui_settings_path.read_text(encoding="utf-8")

        self.assertIn("https://discord.com/api/webhooks/test/token", payload)
        self.assertIn("vinted_discord_notifications_enabled", payload)
        self.assertIn("/Users/test/Library/Application Support/Google/Chrome", payload)
        self.assertIn("\"browser_profile_directory\": \"Profile 2\"", payload)

    def test_load_persisted_ui_settings_restores_discord_webhook(self) -> None:
        with TemporaryDirectory() as temp_dir:
            app = object.__new__(ScraperApp)
            app.ui_settings_path = Path(temp_dir) / "ui_settings.json"
            app.browser_mode_var = self._Var("chrome_normale")
            app.browser_user_data_dir_var = self._Var("")
            app.browser_profile_directory_var = self._Var("Default")
            app.vinted_discord_notifications_var = self._Var(False)
            app.vinted_discord_webhook_url_var = self._Var("")
            app._loading_persisted_ui_settings = False
            app.ui_settings_path.write_text(
                '{"browser_mode": "sessione_persistente", "browser_user_data_dir": "/Users/test/Library/Application Support/Google/Chrome", "browser_profile_directory": "Profile 2", "vinted_discord_notifications_enabled": true, "vinted_discord_webhook_url": "https://discord.com/api/webhooks/test/token"}',
                encoding="utf-8",
            )

            ScraperApp._load_persisted_ui_settings(app)

        self.assertEqual("sessione_persistente", app.browser_mode_var.get())
        self.assertEqual("/Users/test/Library/Application Support/Google/Chrome", app.browser_user_data_dir_var.get())
        self.assertEqual("Profile 2", app.browser_profile_directory_var.get())
        self.assertTrue(app.vinted_discord_notifications_var.get())
        self.assertEqual("https://discord.com/api/webhooks/test/token", app.vinted_discord_webhook_url_var.get())

    def test_handle_window_close_persists_settings_before_destroy(self) -> None:
        with TemporaryDirectory() as temp_dir:
            app = object.__new__(ScraperApp)
            app.root = self._Root()
            app.ui_settings_path = Path(temp_dir) / "ui_settings.json"
            app.browser_mode_var = self._Var("sessione_persistente")
            app.browser_user_data_dir_var = self._Var("/Users/test/Library/Application Support/Google/Chrome")
            app.browser_profile_directory_var = self._Var("Profile 2")
            app.vinted_discord_notifications_var = self._Var(True)
            app.vinted_discord_webhook_url_var = self._Var("https://discord.com/api/webhooks/test/token")
            app._persist_ui_settings_after_id = None

            ScraperApp._handle_window_close(app)

            saved = app.ui_settings_path.read_text(encoding="utf-8")

        self.assertTrue(app.root.destroy_called)
        self.assertIn("https://discord.com/api/webhooks/test/token", saved)


if __name__ == "__main__":
    unittest.main()
