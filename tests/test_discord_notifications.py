import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scraper_app.discord_notifications import (
    build_scraper_error_discord_message,
    build_vinted_deal_discord_message,
    build_vinted_login_required_discord_message,
    build_vinted_profile_report_discord_message,
    send_discord_webhook_message,
)
from scraper_app.vinted_database import load_vinted_notified_deal_keys, save_vinted_deal_notifications


class _FakeWebhookResponse:
    status = 204

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return b""


class DiscordNotificationsTests(unittest.TestCase):
    def test_build_vinted_deal_discord_message_contains_core_fields(self) -> None:
        message = build_vinted_deal_discord_message(
            {
                "name": "Charm Pandora",
                "search_term": "pandora",
                "price": "10,00 €",
                "shipping_price": "1,99 €",
                "total_price": "11,99 €",
                "favorite_count": 85,
                "published_at": "3 ore fa",
                "deal_hunter_reason": "85 like, 3.0h, sped 1.99€",
                "link": "https://www.vinted.it/items/9425130935-charm-pandora",
            }
        )

        self.assertIn("Nuovo affare Vinted", message)
        self.assertIn("Charm Pandora", message)
        self.assertIn("pandora", message)
        self.assertIn("1,99 €", message)
        self.assertIn("Apri annuncio: <https://www.vinted.it/items/9425130935-charm-pandora>", message)

    def test_build_vinted_login_required_discord_message_contains_access_context(self) -> None:
        message = build_vinted_login_required_discord_message(
            {
                "expected_alt": "bonaccarla",
                "checked_at": "2026-07-21T11:30:00",
                "current_url": "https://www.vinted.it/catalog/21-jewellery",
            }
        )

        self.assertIn("Login Vinted richiesto", message)
        self.assertIn("bonaccarla", message)
        self.assertIn("2026-07-21T11:30:00", message)
        self.assertIn("<https://www.vinted.it/catalog/21-jewellery>", message)

    def test_build_vinted_profile_report_includes_sold_after_duration(self) -> None:
        message = build_vinted_profile_report_discord_message(
            {
                "profile_url": "https://www.vinted.it/member/262102939",
                "profile_item_count": 1,
                "profile_new_count": 0,
                "profile_gone_count": 1,
                "profile_still_count": 1,
                "profile_checked_at": "2026-10-04T10:00:00",
            },
            [
                {
                    "profile_item_status": "gone",
                    "name": "Charm cuore",
                    "price": "10,00 €",
                    "profile_sold_after_text": "2g 3h",
                    "link": "https://www.vinted.it/items/100-charm",
                }
            ],
        )

        self.assertIn("Report profilo Vinted", message)
        self.assertIn("Spariti/venduti/rimossi: 1", message)
        self.assertIn("venduto/sparito dopo 2g 3h", message)

    def test_build_scraper_error_discord_message_contains_context(self) -> None:
        message = build_scraper_error_discord_message(
            "Offerta Vinted fallita",
            "Il flusso si e bloccato.",
            level="warning",
            context={"source": "process_done", "phase": "vinted_offer_failed", "current_url": "https://www.vinted.it/items/1"},
        )

        self.assertIn("The Main Scraper warning", message)
        self.assertIn("Offerta Vinted fallita", message)
        self.assertIn("process_done", message)
        self.assertIn("https://www.vinted.it/items/1", message)

    @patch("scraper_app.discord_notifications.urlopen", return_value=_FakeWebhookResponse())
    def test_send_discord_webhook_message_success(self, mocked_urlopen) -> None:
        result = send_discord_webhook_message(
            "https://discord.com/api/webhooks/test/token",
            "ciao",
            embeds=[{"image": {"url": "https://images1.vinted.net/t/test.webp"}}],
        )

        self.assertTrue(result["ok"])
        self.assertEqual(204, result["status_code"])
        request = mocked_urlopen.call_args.args[0]
        self.assertEqual("application/json", request.get_header("Content-type"))
        self.assertIn("https://images1.vinted.net/t/test.webp", request.data.decode("utf-8"))
        self.assertIn("Mozilla/5.0", str(request.get_header("User-agent") or ""))
        self.assertIsNone(request.get_header("Origin"))
        self.assertIsNone(request.get_header("Referer"))

    @patch("scraper_app.discord_notifications.urlopen", return_value=_FakeWebhookResponse())
    def test_send_discord_webhook_message_supports_attachments(self, mocked_urlopen) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            screenshot = Path(temp_dir) / "screenshot.png"
            screenshot.write_bytes(b"fake-image")
            result = send_discord_webhook_message(
                "https://discord.com/api/webhooks/test/token",
                "ciao",
                attachment_paths=[screenshot],
            )

        self.assertTrue(result["ok"])
        self.assertEqual([str(screenshot.resolve())], result["attachments"])
        request = mocked_urlopen.call_args.args[0]
        self.assertIn("multipart/form-data", str(request.get_header("Content-type") or ""))

    def test_save_vinted_deal_notifications_dedupes_by_webhook_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "scraper.db"
            row = {
                "item_id": "9425130935",
                "name": "Charm Pandora",
                "search_term": "pandora",
                "link": "https://www.vinted.it/items/9425130935-charm-pandora",
                "notification_sent_at": "2026-07-20T10:00:00",
            }

            first = save_vinted_deal_notifications(
                [row],
                db_path=db_path,
                webhook_target="https://discord.com/api/webhooks/test/token-a",
            )
            second = save_vinted_deal_notifications(
                [row],
                db_path=db_path,
                webhook_target="https://discord.com/api/webhooks/test/token-a",
            )
            third = save_vinted_deal_notifications(
                [row],
                db_path=db_path,
                webhook_target="https://discord.com/api/webhooks/test/token-b",
            )
            keys_a = load_vinted_notified_deal_keys(
                db_path=db_path,
                webhook_target="https://discord.com/api/webhooks/test/token-a",
            )
            keys_b = load_vinted_notified_deal_keys(
                db_path=db_path,
                webhook_target="https://discord.com/api/webhooks/test/token-b",
            )

        self.assertEqual(1, first["new_deal_notifications"])
        self.assertEqual(1, second["updated_deal_notifications"])
        self.assertEqual(1, third["new_deal_notifications"])
        self.assertIn("id:9425130935", keys_a)
        self.assertIn("id:9425130935", keys_b)


if __name__ == "__main__":
    unittest.main()
