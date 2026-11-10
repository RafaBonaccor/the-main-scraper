import sqlite3
import json
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch

from scraper_app.sources.linkedin_posts import _is_recoverable_browser_error, _wait_for_feed_growth, is_post_in_age_window, load_known_post_ids, load_qualifying_posts, normalize_post, parse_count, run_linkedin_feed_scan, save_posts


class LinkedInPostsTests(TestCase):
    def test_count_and_identity_normalization(self):
        self.assertEqual(parse_count("1,2k reactions"), 1200)
        self.assertEqual(parse_count("1.234 commenti"), 1234)
        post = normalize_post({
            "urn": "urn:li:activity:1234567890123456789",
            "link": "https://www.linkedin.com/feed/update/urn:li:activity:1234567890123456789/?foo=1",
            "author": "Example", "text": "A post", "reactions": "100 reactions",
            "comments": "20 comments", "reposts": "3 reposts",
        }, "AI", "2026-11-09T12:00:00")
        self.assertEqual(post["post_id"], "1234567890123456789")
        self.assertEqual(post["engagement_score"], 149)
        self.assertNotIn("?", post["link"])

    def test_card_without_permalink_keeps_identity_without_fabricating_url(self):
        post = normalize_post({
            "card_key": "xxyasvLtPPmKm0TGAAyGch5Uq5mS6Aukkc_8GFaIqio",
            "author": "Example", "text": "A post", "reactions": "3 reactions",
        }, "AI", "2026-11-09T12:00:00")
        self.assertEqual(post["post_id"], "card:xxyasvLtPPmKm0TGAAyGch5Uq5mS6Aukkc_8GFaIqio")
        self.assertFalse(post["permalink_available"])
        self.assertEqual(post["link"], "https://www.linkedin.com/search/results/content/?keywords=AI")

    def test_feed_card_without_permalink_uses_feed_url(self):
        post = normalize_post({
            "card_key": "xxyasvLtPPmKm0TGAAyGch5Uq5mS6Aukkc_8GFaIqio",
            "text": "A feed post", "reactions": "1,2K",
        }, "feed", "2026-11-09T12:00:00")
        self.assertEqual(post["link"], "https://www.linkedin.com/feed/")
        self.assertEqual(post["reactions"], 1200)

    def test_dedupes_posts_but_keeps_observation_history(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "linkedin.db"
            first = normalize_post({"urn": "urn:li:activity:1234567890123456789", "text": "Post", "reactions": "10"}, "AI", "2026-11-09T12:00:00")
            second = dict(first, reactions=20, engagement_score=20, observed_at="2026-11-09T13:00:00")
            save_posts(path, [first, second])
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM posts").fetchone()[0], 1)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM post_observations").fetchone()[0], 2)

    def test_saves_image_and_video_candidates_separately(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "linkedin.db"
            post = normalize_post({
                "urn": "urn:li:activity:1234567890123456789", "text": "Post",
                "media": [
                    {"kind": "image", "urls": ["https://media.licdn.com/dms/image/a.jpg"], "poster_url": ""},
                    {"kind": "video", "urls": ["https://media.licdn.com/dms/video/a.mp4"],
                     "poster_url": "https://media.licdn.com/dms/image/poster.jpg"},
                ],
            }, "feed", "2026-11-09T12:00:00")
            save_posts(path, [post])
            with sqlite3.connect(path) as db:
                rows = db.execute("SELECT kind, urls_json, poster_url FROM post_media ORDER BY media_index").fetchall()
            self.assertEqual([row[0] for row in rows], ["image", "video"])
            self.assertEqual(json.loads(rows[1][1]), ["https://media.licdn.com/dms/video/a.mp4"])
            self.assertEqual(rows[1][2], "https://media.licdn.com/dms/image/poster.jpg")

    def test_legacy_avatar_url_is_preserved_outside_post_image(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "linkedin.db"
            avatar = "https://media.licdn.com/dms/image/profile-displayphoto-shrink_100_100/avatar"
            with sqlite3.connect(path) as db:
                db.execute("""CREATE TABLE posts (
                    post_id TEXT PRIMARY KEY, link TEXT NOT NULL, author TEXT NOT NULL DEFAULT '',
                    author_url TEXT NOT NULL DEFAULT '', text TEXT NOT NULL DEFAULT '',
                    published_text TEXT NOT NULL DEFAULT '', image_url TEXT NOT NULL DEFAULT '',
                    first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL
                )""")
                db.execute("INSERT INTO posts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                           ("123", "https://www.linkedin.com/feed/", "Author", "", "Post", "", avatar,
                            "2026-11-09T12:00:00", "2026-11-09T12:00:00"))
            save_posts(path, [])
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute("SELECT image_url, avatar_url FROM posts").fetchone(), ("", avatar))

    def test_partial_reload_does_not_erase_complete_post_fields(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "linkedin.db"
            complete = normalize_post({
                "urn": "urn:li:activity:1234567890123456789", "author": "Author",
                "text": "A complete and useful post", "image_url": "https://media.licdn.com/dms/image/feedshare-image-high-res/example",
            }, "feed", "2026-11-09T12:00:00")
            partial = dict(complete, link="https://www.linkedin.com/feed/", author="",
                           text="A complete", image_url="", observed_at="2026-11-09T13:00:00")
            save_posts(path, [complete, partial])
            with sqlite3.connect(path) as db:
                row = db.execute("SELECT link, author, text, image_url FROM posts").fetchone()
                self.assertEqual(row, (complete["link"], complete["author"],
                                       complete["text"], complete["image_url"]))

    def test_loads_qualifying_feed_posts_from_previous_scan(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "linkedin.db"
            qualifying = normalize_post({
                "urn": "urn:li:activity:1234567890123456789", "text": "Popular post",
                "reactions": "1,250", "published_text": "2d",
            }, "feed", datetime.now().isoformat(timespec="seconds"))
            other = normalize_post({
                "urn": "urn:li:activity:2234567890123456789", "text": "Smaller post",
                "reactions": "400",
                "published_text": "2d",
            }, "feed", datetime.now().isoformat(timespec="seconds"))
            save_posts(path, [qualifying, other])
            posts = load_qualifying_posts(path, 1000, 50)
            self.assertEqual([post["post_id"] for post in posts], [qualifying["post_id"]])
            self.assertEqual(posts[0]["reactions"], 1250)

    def test_age_window_excludes_hours_and_older_days(self):
        now = datetime(2026, 11, 10, 12)
        observed = now.isoformat()
        for label in ("1d", "2d", "3d • Edited", "2 giorni"):
            self.assertTrue(is_post_in_age_window(label, observed, 1, 3, now=now), label)
        for label in ("11h", "4d", "1w", "", "Just now"):
            self.assertFalse(is_post_in_age_window(label, observed, 1, 3, now=now), label)
        self.assertFalse(is_post_in_age_window("3d", "2026-11-09T12:00:00", 1, 3, now=now))

    def test_database_resume_does_not_reuse_expired_posts(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "linkedin.db"
            stale = normalize_post({
                "urn": "urn:li:activity:1234567890123456789", "text": "Old post",
                "reactions": "2,000", "published_text": "3d",
            }, "feed", "2020-01-01T12:00:00")
            fresh = normalize_post({
                "urn": "urn:li:activity:2234567890123456789", "text": "Recent post",
                "reactions": "1,200", "published_text": "1d",
            }, "feed", datetime.now().isoformat(timespec="seconds"))
            save_posts(path, [stale, fresh])
            posts = load_qualifying_posts(path, 1000, 1)
            self.assertEqual([post["post_id"] for post in posts], [fresh["post_id"]])

    @patch("scraper_app.sources.linkedin_posts._scrape_linkedin_feed_task")
    def test_new_run_uses_database_baseline_without_short_circuit(self, scan):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "linkedin.db"
            saved = normalize_post({
                "urn": "urn:li:activity:1234567890123456789", "text": "Existing post",
                "reactions": "1,200", "published_text": "2d",
            }, "feed", datetime.now().isoformat(timespec="seconds"))
            save_posts(path, [saved])
            scan.return_value = {"rows": [], "candidates": [], "meta": {"previous_qualifying": 1}}
            run_linkedin_feed_scan(db_path=str(path), target_popular_posts=1,
                                   target_new_posts=10, scroll_rounds=0)
            config = scan.call_args.args[0]
            self.assertEqual(config["known_post_ids"], {saved["post_id"]})
            self.assertEqual(load_known_post_ids(path), {saved["post_id"]})
            self.assertEqual(config["scroll_rounds"], 0)
            self.assertEqual(config["target_new_posts"], 10)

    @patch("scraper_app.sources.linkedin_posts.time.sleep")
    @patch("scraper_app.sources.linkedin_posts._scrape_linkedin_feed_task")
    def test_timeout_restarts_browser_and_recovers_saved_new_post(self, scan, _sleep):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "linkedin.db"
            new_post = normalize_post({
                "urn": "urn:li:activity:3234567890123456789", "text": "New post",
                "reactions": "1,500", "published_text": "2d",
            }, "feed", datetime.now().isoformat(timespec="seconds"))

            def attempt(config):
                if scan.call_count == 1:
                    save_posts(path, [new_post])
                    raise TimeoutError("Response not received")
                self.assertNotIn(new_post["post_id"], config["known_post_ids"])
                self.assertIn(new_post["post_id"], {p["post_id"] for p in config["seed_posts"]})
                return {"rows": [new_post], "candidates": [], "meta": {"stop_reason": "target_reached"}}

            scan.side_effect = attempt
            result = run_linkedin_feed_scan(db_path=str(path), target_new_posts=2,
                                            scan_started_at="2026-11-10T10:00:00")
            self.assertEqual(scan.call_count, 2)
            self.assertEqual([row["post_id"] for row in result.rows], [new_post["post_id"]])
            self.assertTrue(_is_recoverable_browser_error(TimeoutError("Response not received")))
            self.assertFalse(_is_recoverable_browser_error(ValueError("bad age")))

    @patch("scraper_app.sources.linkedin_posts.time.sleep")
    def test_wait_for_feed_growth_detects_new_cards(self, _sleep):
        driver = Mock()
        driver.run_js.side_effect = [
            {"height": 1000, "keys": "first"},
            {"height": 1500, "keys": "first|second"},
        ]
        self.assertTrue(_wait_for_feed_growth(driver, {"height": 1000, "keys": "first"}, timeout=3))
        self.assertEqual(driver.run_js.call_count, 2)
