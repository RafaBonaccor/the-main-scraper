import unittest

from scraper_app.vinted_deals import (
    VINTED_DEAL_HUNTER_DEFAULT_TERMS,
    annotate_vinted_deal_hunter_row,
    is_vinted_deal_hunter_candidate,
    is_vinted_deal_hunter_match,
    normalize_vinted_deal_hunter_terms,
    parse_vinted_relative_age_hours,
)


class VintedDealsTests(unittest.TestCase):
    def test_normalize_terms_uses_defaults_when_empty(self) -> None:
        self.assertEqual(list(VINTED_DEAL_HUNTER_DEFAULT_TERMS), normalize_vinted_deal_hunter_terms(""))

    def test_normalize_terms_dedupes_and_splits(self) -> None:
        self.assertEqual(
            ["charm", "pandora", "collane"],
            normalize_vinted_deal_hunter_terms("charm, pandora\ncollane, charm"),
        )

    def test_parse_relative_age_hours_supports_italian_values(self) -> None:
        self.assertEqual(24.0, parse_vinted_relative_age_hours("ieri"))
        self.assertEqual(3.0, parse_vinted_relative_age_hours("3 ore fa"))
        self.assertEqual(48.0, parse_vinted_relative_age_hours("2 giorni fa"))

    def test_deal_hunter_candidate_and_match(self) -> None:
        self.assertTrue(is_vinted_deal_hunter_candidate(70))
        self.assertTrue(is_vinted_deal_hunter_match(95, "12 ore fa"))
        self.assertTrue(is_vinted_deal_hunter_match(70, "ieri"))
        self.assertFalse(is_vinted_deal_hunter_match(69, "2 ore fa"))
        self.assertFalse(is_vinted_deal_hunter_match(95, "2 giorni fa"))

    def test_annotate_row_marks_candidate_without_recent_confirmation(self) -> None:
        row = annotate_vinted_deal_hunter_row(
            {
                "favorite_count": 84,
                "published_at": "",
            }
        )

        self.assertTrue(row["deal_hunter_candidate"])
        self.assertFalse(row["deal_hunter_match"])
        self.assertEqual("candidato 70+", row["deal_hunter_label"])

    def test_annotate_row_marks_recent_deal(self) -> None:
        row = annotate_vinted_deal_hunter_row(
            {
                "favorite_count": 112,
                "published_at": "5 ore fa",
            }
        )

        self.assertTrue(row["deal_hunter_candidate"])
        self.assertTrue(row["deal_hunter_match"])
        self.assertEqual("affare 24h/70+", row["deal_hunter_label"])


if __name__ == "__main__":
    unittest.main()
