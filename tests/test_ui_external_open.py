import unittest
from unittest.mock import patch

from scraper_app.ui import (
    ScraperApp,
    build_vinted_deal_hunter_search_specs,
    build_vinted_search_target_url,
    detect_vinted_category_label_from_url,
    open_external_target,
    resolve_vinted_category_url,
)


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

    def test_resolve_vinted_category_url_supports_presets(self) -> None:
        self.assertEqual(
            "https://www.vinted.it/catalog/1187-accessories",
            resolve_vinted_category_url("Accessori donna"),
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


if __name__ == "__main__":
    unittest.main()
