import unittest
from unittest.mock import patch

from scraper_app.ui import open_external_target


class UiExternalOpenTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
