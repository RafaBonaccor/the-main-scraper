import unittest
from unittest.mock import Mock, patch

from scraper_app.vinted_access import read_vinted_access_status, wait_for_vinted_access_status


class VintedAccessTests(unittest.TestCase):
    @patch("scraper_app.vinted_access.current_page_url", return_value="https://www.vinted.it/items/123")
    def test_read_vinted_access_status_marks_page_not_found_before_marker(self, _mocked_current_url) -> None:
        driver = Mock()
        driver.run_js.return_value = {
            "marker_present": False,
            "marker_alt": "bonaccarla",
            "marker_src": "https://images1.vinted.net/test.webp",
            "page_title": "Page not found",
            "page_not_found": True,
        }

        status = read_vinted_access_status(driver)

        self.assertTrue(status["page_not_found"])
        self.assertFalse(status["marker_present"])

    @patch("scraper_app.vinted_access.time.sleep")
    @patch("scraper_app.vinted_access.read_vinted_access_status")
    def test_wait_for_vinted_access_status_stops_immediately_on_page_not_found(
        self,
        mocked_read_status,
        mocked_sleep,
    ) -> None:
        mocked_read_status.return_value = {
            "marker_present": False,
            "page_not_found": True,
            "current_url": "https://www.vinted.it/items/123",
        }

        status = wait_for_vinted_access_status(object(), max_wait_seconds=1.0)

        self.assertTrue(status["page_not_found"])
        self.assertEqual(1, mocked_read_status.call_count)
        mocked_sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
