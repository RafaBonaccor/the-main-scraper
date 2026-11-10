import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scraper_app.vinted_image_embeddings import run_embedding_batch
from scraper_app.vinted_image_embedding_report import _preview, create_report


PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"test-image-payload"


class FakeEncoder:
    model_name = "test/clip"
    model_revision = "test-revision"
    device = "cpu"

    def __init__(self):
        self.calls = []

    def encode(self, image_path):
        self.calls.append(image_path)
        return [3.0, 4.0]


def create_source_db(path: Path, archive_path: Path):
    connection = sqlite3.connect(path)
    connection.execute(
        """CREATE TABLE vinted_deal_sale_tracking(
           item_link TEXT, item_id TEXT, item_name TEXT, first_seen_at TEXT,
           sold_at TEXT, status TEXT, payload_json TEXT)"""
    )
    for status, sold_at in (("sold", "2026-10-01T12:00:00"), ("active", "")):
        payload = {
            "archive_image_path": str(archive_path),
            "image_url": "https://example.test/item.webp",
            "price_value": 12.5,
            "favorite_count": 20,
            "seller_member_id": "123",
        }
        connection.execute(
            "INSERT INTO vinted_deal_sale_tracking VALUES(?,?,?,?,?,?,?)",
            (
                f"https://example.test/items/42-{status}",
                "42",
                "Charm test",
                "2026-10-01T10:00:00",
                sold_at,
                status,
                json.dumps(payload),
            ),
        )
    connection.commit()
    connection.close()


class VintedImageEmbeddingsTests(unittest.TestCase):
    def test_human_review_preview_keeps_more_detail_than_grid_thumbnail(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "large.png"
            Image.new("RGB", (1800, 1200), color=(40, 80, 120)).save(source)
            preview_dir = root / "previews"
            preview_dir.mkdir()

            relative_path = _preview(str(source), "abc123", preview_dir)

            self.assertEqual("previews/abc123.jpg", relative_path)
            with Image.open(preview_dir / "abc123.jpg") as preview:
                self.assertEqual((1200, 800), preview.size)

    def test_batch_is_idempotent_and_keeps_source_read_only(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            archive = root / "archive"
            archive.mkdir()
            image = archive / "item.png"
            image.write_bytes(PNG_BYTES)
            source = root / "source.db"
            create_source_db(source, image)
            encoder = FakeEncoder()
            store = root / "embeddings.db"

            first = run_embedding_batch(
                source_db_path=source,
                archive_dir=archive,
                store_db_path=store,
                encoder=encoder,
            )
            second = run_embedding_batch(
                source_db_path=source,
                archive_dir=archive,
                store_db_path=store,
                encoder=encoder,
            )

            self.assertTrue(first["ok"])
            self.assertEqual(first["embedded_assets"], 1)
            self.assertTrue(second["ok"])
            self.assertEqual(second["already_embedded"], 1)
            self.assertEqual(len(encoder.calls), 1)
            connection = sqlite3.connect(store)
            self.assertEqual(connection.execute("SELECT count(*) FROM image_assets").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT count(*) FROM listing_image_observations").fetchone()[0], 2)
            self.assertEqual(connection.execute("SELECT count(*) FROM image_embeddings").fetchone()[0], 1)
            self.assertTrue(connection.execute("SELECT count(*) FROM embedding_runs").fetchone()[0] == 2)
            connection.close()
            source_connection = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
            self.assertEqual(
                source_connection.execute("SELECT count(*) FROM vinted_deal_sale_tracking").fetchone()[0], 2
            )
            source_connection.close()
            self.assertTrue(second["backup_path"])
            self.assertTrue(Path(second["backup_path"]).is_file())
            report_path = root / "report" / "index.html"
            report = create_report(store, report_path)
            self.assertTrue(report["ok"])
            self.assertEqual(report["items"], 1)
            self.assertTrue(report_path.is_file())
            html = report_path.read_text(encoding="utf-8")
            self.assertIn("Similarità visiva", html)
            self.assertIn("Gioco: valuta i vicini", html)
            self.assertIn("Esporta valutazioni JSON", html)

    def test_index_only_does_not_load_encoder(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            archive = root / "archive"
            archive.mkdir()
            image = archive / "item.png"
            image.write_bytes(PNG_BYTES)
            source = root / "source.db"
            create_source_db(source, image)

            result = run_embedding_batch(
                source_db_path=source,
                archive_dir=archive,
                store_db_path=root / "embeddings.db",
                index_only=True,
                limit=1,
            )

            self.assertTrue(result["ok"])
            self.assertEqual(result["indexed_assets"], 1)
            self.assertEqual(result["embedded_assets"], 0)
            connection = sqlite3.connect(root / "embeddings.db")
            self.assertEqual(connection.execute("SELECT count(*) FROM image_embeddings").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT count(*) FROM listing_image_observations").fetchone()[0], 2)
            connection.close()


if __name__ == "__main__":
    unittest.main()
