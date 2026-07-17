import unittest
from unittest.mock import Mock

from scraper_app.sources.vinted_offer import (
    _build_vinted_offer_error_result,
    _first_numeric_price,
    calculate_vinted_offer_value,
    format_vinted_offer_input,
    normalize_vinted_offer_discount_percent,
)


class VintedOfferTests(unittest.TestCase):
    def test_offer_calculation_uses_configured_discount_percent(self) -> None:
        self.assertEqual(11.9, calculate_vinted_offer_value(14.0))
        self.assertEqual(8.42, calculate_vinted_offer_value(9.9))
        self.assertEqual(10.5, calculate_vinted_offer_value(14.0, 25))
        self.assertEqual(7.43, calculate_vinted_offer_value(9.9, 25))

    def test_offer_discount_percent_validation_rejects_invalid_values(self) -> None:
        self.assertEqual(15.0, normalize_vinted_offer_discount_percent("15"))
        self.assertEqual(22.5, normalize_vinted_offer_discount_percent("22,5"))
        with self.assertRaises(ValueError):
            normalize_vinted_offer_discount_percent(-1)
        with self.assertRaises(ValueError):
            normalize_vinted_offer_discount_percent(100)

    def test_offer_input_format_uses_dot_decimal(self) -> None:
        self.assertEqual("11.90", format_vinted_offer_input(11.9))
        self.assertEqual("8.42", format_vinted_offer_input(8.42))

    def test_first_numeric_price_prefers_selected_table_price(self) -> None:
        self.assertEqual(2.5, _first_numeric_price(2.5, 5.29))
        self.assertEqual(2.5, _first_numeric_price("2.50", 5.29))

    def test_offer_error_result_keeps_batch_alive(self) -> None:
        driver = Mock()
        result = _build_vinted_offer_error_result(
            driver,
            {"link": "https://www.vinted.it/items/1", "base_price": "4.50", "submit": True},
            error="RuntimeError: Pulsante non trovato",
        )

        self.assertFalse(result["ok"])
        self.assertFalse(result["prepared"])
        self.assertFalse(result["submitted"])
        self.assertEqual("https://www.vinted.it/items/1", result["link"])
        self.assertEqual(4.5, result["selected_price"])
        self.assertIn("Pulsante non trovato", result["error"])


if __name__ == "__main__":
    unittest.main()
