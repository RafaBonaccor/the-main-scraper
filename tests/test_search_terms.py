import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scraper_app.search_terms import delete_saved_search_terms, load_saved_search_terms, save_search_term


class SearchTermsTests(unittest.TestCase):
    def test_save_and_load_terms_for_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "terms.json"
            with patch("scraper_app.search_terms.SEARCH_TERMS_PATH", path):
                save_search_term("vinted", "vestito bianco")

                self.assertEqual(["vestito bianco"], load_saved_search_terms("vinted"))

    def test_save_deduplicates_case_insensitively_and_moves_term_to_top(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "terms.json"
            with patch("scraper_app.search_terms.SEARCH_TERMS_PATH", path):
                save_search_term("vinted", "maglietta")
                save_search_term("vinted", "borsa")
                save_search_term("vinted", "MAGLIETTA")

                self.assertEqual(["MAGLIETTA", "borsa"], load_saved_search_terms("vinted"))

    def test_delete_removes_only_selected_terms(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "terms.json"
            with patch("scraper_app.search_terms.SEARCH_TERMS_PATH", path):
                save_search_term("vinted", "giacca")
                save_search_term("vinted", "scarpe")
                save_search_term("vinted", "borsa")

                remaining = delete_saved_search_terms("vinted", ["scarpe", "giacca"])

                self.assertEqual(["borsa"], remaining)
                self.assertEqual(["borsa"], load_saved_search_terms("vinted"))


if __name__ == "__main__":
    unittest.main()
