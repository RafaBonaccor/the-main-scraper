import unittest
import subprocess
from pathlib import Path
from unittest.mock import patch

from scraper_app.browser_runtime import (
    _remove_conflicting_window_flags,
    browser_should_open_in_background,
    close_orphaned_job_chrome_instances,
    close_runtime_chrome_instances,
    resolve_browser_profile,
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

    def test_runtime_cleanup_only_closes_chrome_owned_by_current_job(self) -> None:
        output = "\n".join(
            (
                "111 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome "
                "--user-data-dir=/tmp/the_main_scraper_profiles/cn_Default_p1234_aaa",
                "222 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome "
                "--user-data-dir=/tmp/the_main_scraper_profiles/cn_Default_p9876_bbb",
                "333 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome "
                "--user-data-dir=/Users/example/Library/Application Support/Google/Chrome",
            )
        )
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout=output, stderr="")
        with (
            patch("scraper_app.browser_runtime.sys.platform", "darwin"),
            patch("scraper_app.browser_runtime.os.getpid", return_value=1234),
            patch("scraper_app.browser_runtime.RUNTIME_PROFILES_DIR", "/tmp/the_main_scraper_profiles"),
            patch("scraper_app.browser_runtime.subprocess.run", return_value=completed),
            patch("scraper_app.browser_runtime.os.kill") as kill,
            patch("scraper_app.browser_runtime.time.sleep"),
        ):
            closed = close_runtime_chrome_instances()

        self.assertEqual(1, closed)
        self.assertTrue(kill.call_args_list)
        self.assertEqual({111}, {call.args[0] for call in kill.call_args_list})

    def test_orphan_cleanup_preserves_active_jobs_and_normal_chrome(self) -> None:
        output = "\n".join(
            (
                "500 100 01:00:00 /usr/bin/python /workspace/main-scraper/main.py run vinted",
                "501 500 00:10:00 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome --user-data-dir=/tmp/job-profiles/iso_p500_live --remote-debugging-port=50001",
                "601 1 02:00:00 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome --user-data-dir=/tmp/job-profiles/iso_p499_dead --remote-debugging-port=50002",
                "701 1 02:00:00 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome --user-data-dir=/tmp/bota/51020_legacy --remote-debugging-port=51020",
                "801 500 00:05:00 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome --user-data-dir=/tmp/bota/51021_active --remote-debugging-port=51021",
                "901 1 02:00:00 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome --user-data-dir=/Users/example/Library/Application Support/Google/Chrome",
            )
        )
        ps_result = subprocess.CompletedProcess(args=[], returncode=0, stdout=output, stderr="")
        lsof_result = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="")
        with (
            patch("scraper_app.browser_runtime.sys.platform", "darwin"),
            patch("scraper_app.browser_runtime.subprocess.run", side_effect=[ps_result, lsof_result]),
            patch("scraper_app.browser_runtime.RUNTIME_PROFILES_DIR", "/tmp/job-profiles"),
            patch("scraper_app.browser_runtime.WORKSPACE_ROOT", Path("/workspace/main-scraper")),
            patch("scraper_app.browser_runtime.tempfile.gettempdir", return_value="/tmp"),
            patch("scraper_app.browser_runtime.os.kill") as kill,
            patch("scraper_app.browser_runtime.time.sleep"),
        ):
            closed = close_orphaned_job_chrome_instances(stale_botasaurus_seconds=1800)

        self.assertEqual(2, closed)
        self.assertEqual({601, 701}, {call.args[0] for call in kill.call_args_list})

    def test_isolated_browser_uses_a_job_owned_temporary_profile(self) -> None:
        with patch("scraper_app.browser_runtime.RUNTIME_PROFILES_DIR", Path("/tmp/job-profiles")):
            profile = resolve_browser_profile({"browser_mode": "isolated"})

        self.assertIn("/tmp/job-profiles/iso_p", str(profile))


if __name__ == "__main__":
    unittest.main()
