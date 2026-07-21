import unittest

from scraper_app.vinted_deals import (
    VINTED_DEAL_HUNTER_DEFAULT_TERMS,
    VINTED_DEAL_HUNTER_MAX_SHIPPING_PRICE,
    annotate_vinted_deal_hunter_row,
    is_vinted_deal_hunter_candidate,
    is_vinted_deal_hunter_match,
    normalize_vinted_deal_hunter_max_price,
    normalize_vinted_deal_hunter_terms,
    parse_vinted_relative_age_hours,
)


class VintedDealsTests(unittest.TestCase):
    def test_normalize_terms_uses_defaults_when_empty(self) -> None:
        self.assertEqual(list(VINTED_DEAL_HUNTER_DEFAULT_TERMS), normalize_vinted_deal_hunter_terms(""))

    def test_normalize_terms_can_return_empty_when_requested(self) -> None:
        self.assertEqual([], normalize_vinted_deal_hunter_terms("", use_default_when_empty=False))

    def test_normalize_terms_dedupes_and_splits(self) -> None:
        self.assertEqual(
            ["charm", "pandora", "collane"],
            normalize_vinted_deal_hunter_terms("charm, pandora\ncollane, charm"),
        )

    def test_parse_relative_age_hours_supports_italian_values(self) -> None:
        self.assertEqual(24.0, parse_vinted_relative_age_hours("ieri"))
        self.assertEqual(24.0, parse_vinted_relative_age_hours("un giorno fa"))
        self.assertEqual(3.0, parse_vinted_relative_age_hours("3 ore fa"))
        self.assertEqual(48.0, parse_vinted_relative_age_hours("2 giorni fa"))

    def test_deal_hunter_candidate_and_match(self) -> None:
        self.assertTrue(is_vinted_deal_hunter_candidate(70))
        self.assertTrue(is_vinted_deal_hunter_match(95, "12 ore fa", shipping_price_value=2.49))
        self.assertTrue(is_vinted_deal_hunter_match(70, "ieri", shipping_price_value=0.99))
        self.assertTrue(is_vinted_deal_hunter_match(70, "un giorno fa", shipping_price_value=0.99))
        self.assertFalse(is_vinted_deal_hunter_match(69, "2 ore fa"))
        self.assertFalse(is_vinted_deal_hunter_match(95, "2 giorni fa", shipping_price_value=1.99))
        self.assertFalse(
            is_vinted_deal_hunter_match(
                95,
                "2 ore fa",
                shipping_price_value=VINTED_DEAL_HUNTER_MAX_SHIPPING_PRICE + 0.01,
            )
        )
        self.assertFalse(
            is_vinted_deal_hunter_match(
                95,
                "2 ore fa",
                price_value=21.0,
                max_price=20.0,
                shipping_price_value=1.49,
            )
        )

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
                "price_value": 12.0,
                "shipping_price_value": 1.99,
            }
        )

        self.assertTrue(row["deal_hunter_candidate"])
        self.assertTrue(row["deal_hunter_match"])
        self.assertEqual("affare 24h/70+", row["deal_hunter_label"])

    def test_annotate_row_rejects_high_shipping_or_price(self) -> None:
        high_shipping_row = annotate_vinted_deal_hunter_row(
            {
                "favorite_count": 112,
                "published_at": "5 ore fa",
                "price_value": 12.0,
                "shipping_price_value": 3.5,
            },
            max_price=20.0,
        )
        high_price_row = annotate_vinted_deal_hunter_row(
            {
                "favorite_count": 112,
                "published_at": "5 ore fa",
                "price_value": 25.0,
                "shipping_price_value": 1.5,
            },
            max_price=20.0,
        )

        self.assertFalse(high_shipping_row["deal_hunter_match"])
        self.assertIn("spedizione", high_shipping_row["deal_hunter_reason"])
        self.assertFalse(high_price_row["deal_hunter_match"])
        self.assertIn("prezzo", high_price_row["deal_hunter_reason"])

    def test_normalize_vinted_deal_hunter_max_price(self) -> None:
        self.assertEqual(19.9, normalize_vinted_deal_hunter_max_price("19,90 €"))
        self.assertIsNone(normalize_vinted_deal_hunter_max_price(""))


if __name__ == "__main__":
    unittest.main()
