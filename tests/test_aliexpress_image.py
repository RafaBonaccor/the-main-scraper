import sqlite3
import tempfile
import unittest
from pathlib import Path

from scraper_app.sources.aliexpress_image import (
    deduplicate_and_rank,
    extract_price,
    extract_sold_count,
    hash_image,
    load_cached_results,
    normalize_product_row,
    persist_search_results,
    resolve_image_inputs,
    resolve_image_paths,
)


class AliExpressImageSearchTests(unittest.TestCase):
    def test_resolves_one_image_and_folder_without_duplicates(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = root / "first.jpg"
            second = root / "second.png"
            ignored = root / "notes.txt"
            first.write_bytes(b"first")
            second.write_bytes(b"second")
            ignored.write_text("ignore", encoding="utf-8")
            paths = resolve_image_paths(image_path=str(first), image_dir=str(root))
            self.assertEqual([first.resolve(), second.resolve()], paths)

    def test_normalizes_price_orders_and_product_id(self):
        row = normalize_product_row(
            {
                "link": "//it.aliexpress.com/item/100500123456.html?spm=test",
                "title": "Charm test",
                "raw_text": "Charm test € 4,89 4.8 2,3K venduti",
                "image_url": "https://example.test/image.jpg",
            }
        )
        self.assertEqual("100500123456", row["product_id"])
        self.assertEqual(4.89, row["price_value"])
        self.assertEqual(2300, row["sold_count"])
        self.assertEqual("https://it.aliexpress.com/item/100500123456.html", row["link"])

    def test_price_and_order_parsers_handle_localized_text(self):
        self.assertEqual((12.5, "12,50 €"), extract_price("A partire da 12,50 € spedizione inclusa"))
        self.assertEqual((12.94, "12,94€"), extract_price("Prodotto 12,94€ 4.1 482 venduto(i)"))
        self.assertEqual(1500, extract_sold_count("1,5K venduti"))
        self.assertEqual(27, extract_sold_count("27 orders"))
        self.assertEqual(289, extract_sold_count("3,81€4.8289 venduto(i)"))
        self.assertEqual(5178, extract_sold_count("15,19€5178 venduto(i)"))

    def test_deduplicates_and_applies_price_cap(self):
        rows = [
            {"product_id": "1", "link": "https://x/1", "price_value": 4.0, "result_score": 10},
            {"product_id": "1", "link": "https://x/1", "price_value": 4.0, "result_score": 20},
            {"product_id": "2", "link": "https://x/2", "price_value": 30.0, "result_score": 100},
        ]
        ranked = deduplicate_and_rank(rows, max_price="10")
        self.assertEqual(["1"], [row["product_id"] for row in ranked])
        self.assertEqual(1, ranked[0]["result_rank"])
        self.assertEqual(20, ranked[0]["result_score"])

    def test_persists_and_reuses_results_by_image_hash(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            image = root / "product.jpg"
            image.write_bytes(b"product-image")
            image_hash = hash_image(image)
            row = {
                "query_image_hash": image_hash,
                "product_id": "10",
                "link": "https://it.aliexpress.com/item/10.html",
                "title": "Candidate",
                "image_url": "https://example.test/10.jpg",
                "price_value": 3.5,
                "price": "3,50 €",
                "rating": 4.8,
                "sold_count": 200,
                "result_score": 80.0,
            }
            db_path = root / "sourcing.db"
            search = {"image_hash": image_hash, "results_count": 1}
            persist_search_results(str(db_path), [image], [row], [search])
            cached = load_cached_results(str(db_path), image_hash=image_hash, max_age_hours=24)
            self.assertEqual(1, len(cached))
            self.assertTrue(cached[0]["from_cache"])
            with sqlite3.connect(db_path) as connection:
                self.assertEqual(1, connection.execute("SELECT COUNT(*) FROM aliexpress_image_search_runs").fetchone()[0])

    def test_persists_marketplace_origin_separately_from_matches(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            image = root / "source.jpg"
            image.write_bytes(b"source")
            image_hash = hash_image(image)
            db_path = root / "sourcing.db"
            persist_search_results(
                str(db_path),
                [image],
                [],
                [{"image_hash": image_hash, "results_count": 0}],
                origins_by_path={
                    str(image.resolve()): {
                        "source": "vinted",
                        "source_item_id": "777",
                        "source_link": "https://www.vinted.it/items/777-test",
                        "source_title": "Source product",
                    }
                },
            )
            with sqlite3.connect(db_path) as connection:
                record = connection.execute(
                    "SELECT source_item_id, source_link FROM aliexpress_source_items"
                ).fetchone()
            self.assertEqual(("777", "https://www.vinted.it/items/777-test"), record)

    def test_resolves_selected_items_manifest_and_preserves_origin(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            image = root / "archived.png"
            image.write_bytes(b"product")
            manifest = root / "selected.json"
            manifest.write_text(
                '{"items":[{"source":"vinted","item_id":"123","link":"https://www.vinted.it/items/123-test","name":"Test charm","local_image_path":"'
                + str(image)
                + '"}]}',
                encoding="utf-8",
            )
            paths, origins = resolve_image_inputs(items_file=str(manifest), cache_dir=root / "cache")
            self.assertEqual([image.resolve()], paths)
            self.assertEqual("123", origins[str(image.resolve())]["source_item_id"])
            self.assertEqual("Test charm", origins[str(image.resolve())]["source_title"])

    def test_batch_ranking_keeps_same_product_for_distinct_source_images(self):
        rows = [
            {"query_image_hash": "a", "product_id": "1", "link": "https://x/1", "result_score": 10},
            {"query_image_hash": "b", "product_id": "1", "link": "https://x/1", "result_score": 9},
        ]
        self.assertEqual(2, len(deduplicate_and_rank(rows)))


if __name__ == "__main__":
    unittest.main()
