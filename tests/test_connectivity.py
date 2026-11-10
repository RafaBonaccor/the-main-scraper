import unittest
from unittest.mock import patch

from scraper_app.connectivity import internet_connection_available, wait_for_internet_connection


class ConnectivityTests(unittest.TestCase):
    @patch("scraper_app.connectivity.urlopen")
    def test_internet_probe_accepts_no_content_response(self, mocked_urlopen) -> None:
        mocked_urlopen.return_value.__enter__.return_value.status = 204

        self.assertTrue(internet_connection_available())
        mocked_urlopen.assert_called_once()

    @patch("scraper_app.connectivity.time.sleep")
    @patch("scraper_app.connectivity.internet_connection_available", side_effect=[False, False, True])
    def test_wait_gate_pauses_then_returns_when_connection_recovers(self, mocked_probe, mocked_sleep) -> None:
        waited = wait_for_internet_connection(label="test", poll_seconds=1)

        self.assertTrue(waited)
        self.assertEqual(3, mocked_probe.call_count)
        mocked_sleep.assert_called_once_with(1.0)

    @patch("scraper_app.connectivity.internet_connection_available", return_value=False)
    def test_wait_gate_can_be_interrupted_while_offline(self, _mocked_probe) -> None:
        self.assertIsNone(
            wait_for_internet_connection(
                label="test",
                should_stop=lambda: True,
            )
        )


if __name__ == "__main__":
    unittest.main()
