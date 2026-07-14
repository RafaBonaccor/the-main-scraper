import unittest
from unittest.mock import Mock, patch

from scraper_app.browser_launcher import open_browser_session
from scraper_app.chrome_reuse import preferred_host_fragment_for_url, try_reuse_running_chrome


class ChromeReuseTests(unittest.TestCase):
    def test_preferred_host_fragment_strips_www(self) -> None:
        self.assertEqual("vinted.it", preferred_host_fragment_for_url("https://www.vinted.it/catalog"))
        self.assertEqual("maps.google.com", preferred_host_fragment_for_url("https://maps.google.com/search"))

    @patch("scraper_app.chrome_reuse.sys.platform", "darwin")
    @patch("scraper_app.chrome_reuse._is_google_chrome_running", return_value=True)
    @patch("scraper_app.chrome_reuse.subprocess.run")
    def test_try_reuse_running_chrome_reuses_matching_tab(self, mocked_run, _mocked_running) -> None:
        mocked_run.return_value = Mock(returncode=0, stdout="REUSED_MATCHING_TAB|https://www.vinted.it/catalog?page=1\n", stderr="")

        result = try_reuse_running_chrome("https://www.vinted.it/catalog?page=2", preferred_host_fragment="vinted.it")

        self.assertTrue(result["reused"])
        self.assertEqual("reused_matching_tab", result["action"])
        self.assertEqual("https://www.vinted.it/catalog?page=1", result["previous_url"])

    @patch("scraper_app.browser_launcher.try_reuse_running_chrome")
    def test_open_browser_session_returns_early_when_running_chrome_is_reused(self, mocked_reuse) -> None:
        mocked_reuse.return_value = {
            "reused": True,
            "action": "opened_new_tab",
            "previous_url": "",
        }

        result = open_browser_session(url="https://www.vinted.it/catalog?search_text=macbook")

        self.assertTrue(result["ok"])
        self.assertTrue(result["reused_running_chrome"])
        self.assertEqual("opened_new_tab", result["reused_running_chrome_action"])


if __name__ == "__main__":
    unittest.main()

