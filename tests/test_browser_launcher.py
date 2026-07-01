import unittest
from unittest.mock import patch

from scraper_app.browser_launcher import DEFAULT_BROWSER_URL, open_browser_session


class BrowserLauncherTests(unittest.TestCase):
    @patch("scraper_app.browser_launcher._open_browser_task")
    def test_open_browser_session_passes_profile_and_url(self, mocked_task) -> None:
        mocked_task.return_value = {"ok": True}

        result = open_browser_session(
            url="https://www.subito.it/",
            keep_open_seconds=30,
            browser_mode="sessione_persistente",
            browser_user_data_dir="C:/Chrome/User Data",
            browser_profile_directory="Default",
        )

        self.assertTrue(result["ok"])
        config = mocked_task.call_args.args[0]
        self.assertEqual("https://www.subito.it/", config["url"])
        self.assertEqual(30, config["keep_open_seconds"])
        self.assertEqual("sessione_persistente", config["browser_mode"])

    @patch("scraper_app.browser_launcher._open_browser_task")
    def test_empty_url_uses_google_maps(self, mocked_task) -> None:
        mocked_task.return_value = {"ok": True}

        open_browser_session(url="")

        self.assertEqual(DEFAULT_BROWSER_URL, mocked_task.call_args.args[0]["url"])


if __name__ == "__main__":
    unittest.main()
