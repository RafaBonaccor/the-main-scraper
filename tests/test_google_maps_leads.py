import unittest
from unittest.mock import patch

from scraper_app.sources.google_maps import (
    _build_search_targets,
    _clean_detail_value,
    _extract_rating_and_reviews,
    _parse_reviews_count,
    _raw_article_to_row,
)
from scraper_app.website_audit import annotate_lead_opportunity, audit_business_website


class _FakeHeaders:
    def get(self, key: str, default: str = "") -> str:
        if key.lower() == "content-type":
            return "text/html; charset=utf-8"
        return default

    def get_content_charset(self) -> str:
        return "utf-8"


class _FakeResponse:
    status = 200
    headers = _FakeHeaders()

    def __init__(self, html: str, url: str) -> None:
        self._html = html.encode("utf-8")
        self._url = url

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc_value, _traceback) -> None:
        return None

    def read(self, _size: int) -> bytes:
        return self._html

    def geturl(self) -> str:
        return self._url


class GoogleMapsLeadTests(unittest.TestCase):
    def test_build_search_targets_combines_categories_and_cities(self) -> None:
        targets = _build_search_targets(
            "ristoranti, dentisti",
            "Roma, Monterotondo",
            "RM",
            "Italia",
        )

        self.assertEqual(4, len(targets))
        self.assertEqual("ristoranti", targets[0]["search"])
        self.assertEqual("Roma", targets[0]["city"])
        self.assertIn("dentisti", targets[-1]["url"])
        self.assertIn("Monterotondo", targets[-1]["label"])

    def test_rating_and_review_parsing_supports_italian_format(self) -> None:
        rating, reviews = _extract_rating_and_reviews(["Dentista", "4,7 (1.234)"])

        self.assertEqual(4.7, rating)
        self.assertEqual(1234, reviews)
        self.assertEqual(987, _parse_reviews_count("987 recensioni"))

    def test_maps_private_use_icons_are_removed_from_details(self) -> None:
        self.assertEqual("Via Flaminia 1", _clean_detail_value("\ue0c8 Via Flaminia 1"))

    def test_missing_website_is_high_priority(self) -> None:
        row = annotate_lead_opportunity({"name": "Attivita senza sito", "website": "", "detail_checked": True})

        self.assertEqual("missing", row["website_status"])
        self.assertEqual("alta", row["lead_priority"])
        self.assertEqual(100, row["opportunity_score"])

    def test_unchecked_maps_detail_is_not_marked_as_missing_website(self) -> None:
        row = annotate_lead_opportunity({"name": "Scheda non aperta", "website": ""})

        self.assertEqual("not_checked", row["website_status"])
        self.assertEqual("media", row["lead_priority"])

    def test_sponsored_card_is_marked_for_exclusion(self) -> None:
        row = _raw_article_to_row(
            "Sponsorizzato\nStudio Demo\n4,5 (20)",
            "https://www.google.com/maps/place/Studio+Demo",
            "https://www.google.com/maps/search/studio",
            "studio",
            "Morlupo",
            "RM",
            "Italia",
        )

        self.assertTrue(row["is_sponsored"])
        self.assertEqual("Studio Demo", row["name"])

    @patch("scraper_app.website_audit.urlopen")
    def test_website_audit_extracts_public_contacts_and_quality_signals(self, mocked_urlopen) -> None:
        html = """
        <html>
          <head>
            <title>Studio Demo</title>
            <meta name="viewport" content="width=device-width">
            <meta name="description" content="Servizi professionali">
          </head>
          <body>
            <a href="mailto:info@studiodemo.it">Email</a>
            <a href="https://www.instagram.com/studiodemo">Instagram</a>
          </body>
        </html>
        """
        mocked_urlopen.return_value = _FakeResponse(html, "https://studiodemo.it/")

        result = audit_business_website("https://studiodemo.it", max_pages=1)

        self.assertEqual("info@studiodemo.it", result["email"])
        self.assertEqual("https://www.instagram.com/studiodemo", result["social_instagram"])
        self.assertEqual("good", result["website_status"])
        self.assertEqual("bassa", result["lead_priority"])
        self.assertLess(result["opportunity_score"], 40)


if __name__ == "__main__":
    unittest.main()
