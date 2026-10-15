import unittest

from scraper_app.browser_runtime import (
    _remove_conflicting_window_flags,
    browser_should_open_in_background,
    resolve_browser_arguments,
)


class BrowserRuntimeBackgroundTests(unittest.TestCase):
    def test_background_is_default_and_adds_start_minimized(self) -> None:
        config = {"browser_mode": "chrome_normale", "browser_profile_directory": "Default"}

        self.assertTrue(browser_should_open_in_background(config))
        self.assertEqual(
            ["--start-minimized", "--profile-directory=Default"],
            resolve_browser_arguments(config),
        )

    def test_interactive_flow_can_require_foreground(self) -> None:
        config = {
            "browser_mode": "chrome_normale",
            "browser_profile_directory": "Default",
            "browser_requires_foreground": True,
        }

        self.assertFalse(browser_should_open_in_background(config))
        self.assertEqual(["--profile-directory=Default"], resolve_browser_arguments(config))

    def test_minimized_start_removes_botasaurus_maximized_default(self) -> None:
        arguments = ["--start-maximized", "--no-first-run", "--start-minimized"]

        self.assertEqual(
            ["--no-first-run", "--start-minimized"],
            _remove_conflicting_window_flags(arguments),
        )


if __name__ == "__main__":
    unittest.main()
