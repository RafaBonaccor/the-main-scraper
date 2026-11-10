from unittest import TestCase
from unittest.mock import Mock, patch
from pathlib import Path
import tempfile

from scraper_app.sources.linkedin_publish import _publish_with_driver


class LinkedInPublishTests(TestCase):
    @patch("scraper_app.sources.linkedin_publish._wait_js")
    def test_editor_mismatch_never_clicks_publish(self, wait):
        wait.side_effect = ["https://www.linkedin.com/feed/", {"ok": True}, ".ql-editor", True]
        driver = Mock()
        driver.run_js.return_value = "Different text"
        result = _publish_with_driver(driver, {"body": "Approved text"})
        self.assertEqual(result["state"], "failed")
        self.assertFalse(result["clicked"])
        self.assertEqual(driver.run_js.call_count, 1)

    @patch("scraper_app.sources.linkedin_publish._wait_js")
    def test_click_without_confirmation_is_unknown(self, wait):
        wait.side_effect = ["https://www.linkedin.com/feed/", {"ok": True}, ".ql-editor", True, None]
        driver = Mock()
        driver.run_js.side_effect = ["Approved text", {"ok": True}]
        result = _publish_with_driver(driver, {"body": "Approved text"})
        self.assertEqual(result["state"], "unknown")
        self.assertTrue(result["clicked"])

    @patch("scraper_app.sources.linkedin_publish._wait_js")
    def test_success_requires_visible_confirmation(self, wait):
        wait.side_effect = ["https://www.linkedin.com/feed/", {"ok": True}, ".ql-editor", True,
                            {"success": True, "link": "https://www.linkedin.com/feed/update/123/"}]
        driver = Mock()
        driver.run_js.side_effect = ["Approved text", {"ok": True}]
        result = _publish_with_driver(driver, {"body": "Approved text"})
        self.assertEqual(result["state"], "published")
        self.assertTrue(result["clicked"])

    @patch("scraper_app.sources.linkedin_publish._upload_via_file_chooser", return_value=(True, ""))
    @patch("scraper_app.sources.linkedin_publish._wait_js")
    def test_archived_image_is_uploaded_and_previewed_before_publish(self, wait, upload):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_source = root / "repo" / "projects" / "main-scraper" / "scraper_app" / "sources" / "linkedin_publish.py"
            archived = root / "repo" / "data" / "linkedin_media" / "source.png"
            fake_source.parent.mkdir(parents=True)
            archived.parent.mkdir(parents=True)
            archived.write_bytes(b"image")
            wait.side_effect = ["https://www.linkedin.com/feed/", {"ok": True}, True,
                                ".ql-editor", True, {"success": True, "link": ""}]
            driver = Mock()
            driver.run_js.side_effect = ["Approved text", {"ok": True}]
            with patch("scraper_app.sources.linkedin_publish.__file__", str(fake_source)):
                result = _publish_with_driver(driver, {"body": "Approved text", "attachment_path": str(archived)})
        self.assertEqual(result["state"], "published")
        upload.assert_called_once()
        self.assertEqual(wait.call_count, 6)
