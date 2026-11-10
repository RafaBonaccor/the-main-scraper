"""Read-only LinkedIn feed discovery using the user's copied standard Chrome profile.

LinkedIn's markup changes frequently. Missing login or post cards is surfaced as an
error, never recorded as an empty successful search.
"""

from __future__ import annotations

import re
import json
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urlparse

from botasaurus.browser import Driver, Wait, browser

from ..browser_runtime import resolve_browser_arguments, resolve_browser_profile
from ..models import ScrapeOutcome
from ..runtime_controls import consume_stop_after_current_item_request


POST_ID_RE = re.compile(r"(?:urn:li:(?:activity|ugcPost):|/posts/[^/?#]*?activity-)(\d{10,})")
NUMBER_RE = re.compile(r"(\d[\d\s.,]*)([kKmM]?)")
DAY_AGE_RE = re.compile(r"(?<!\w)(\d+)\s*(?:d|days?|giorni?|giorn[io])\b", re.IGNORECASE)


def is_post_in_age_window(
    published_text: str, observed_at: str, min_days: int, max_days: int,
    *, now: datetime | None = None,
) -> bool:
    """Use LinkedIn's approximate day label, aging cached observations forward."""
    match = DAY_AGE_RE.search(published_text or "")
    if not match:
        return False
    try:
        observed = datetime.fromisoformat(observed_at)
    except (TypeError, ValueError):
        return False
    current = now or datetime.now(observed.tzinfo)
    if observed.tzinfo is None and current.tzinfo is not None:
        current = current.astimezone().replace(tzinfo=None)
    elif observed.tzinfo is not None and current.tzinfo is None:
        current = current.astimezone(observed.tzinfo)
    elif observed.tzinfo is not None:
        current = current.astimezone(observed.tzinfo)
    age_days = int(match.group(1)) + max(0, (current - observed).total_seconds()) / 86400
    return min_days <= age_days < max_days + 1


def parse_count(value: object) -> int:
    match = NUMBER_RE.search(str(value or "").replace("\u00a0", " "))
    if not match:
        return 0
    number = match.group(1).replace(" ", "")
    suffix = match.group(2).lower()
    if suffix:
        try:
            return int(float(number.replace(",", ".")) * (1_000 if suffix == "k" else 1_000_000))
        except ValueError:
            return 0
    try:
        return int(number.replace(".", "").replace(",", ""))
    except ValueError:
        return 0


def normalize_post(raw: dict, keyword: str, observed_at: str) -> dict | None:
    link = str(raw.get("link") or "").strip().split("?", 1)[0].rstrip("/")
    urn = str(raw.get("urn") or "").strip()
    match = POST_ID_RE.search(urn) or POST_ID_RE.search(link)
    card_key = str(raw.get("card_key") or "").strip()
    if not match and not re.fullmatch(r"[A-Za-z0-9_+-]{16,120}", card_key):
        return None
    post_id = match.group(1) if match else f"card:{card_key}"
    permalink_available = bool(match)
    if permalink_available and (not link or "linkedin.com" not in urlparse(link).netloc):
        link = f"https://www.linkedin.com/feed/update/{urn or f'urn:li:activity:{post_id}'}/"
    if not permalink_available:
        # The feed may omit post URNs and links entirely. Keep the stable
        # card identity, but never invent a post permalink.
        link = "https://www.linkedin.com/feed/" if keyword == "feed" else "https://www.linkedin.com/search/results/content/?keywords=" + quote(keyword)
    text = str(raw.get("text") or "").strip()
    author = str(raw.get("author") or "").strip()
    if not text and not author:
        return None
    reactions = parse_count(raw.get("reactions"))
    comments = parse_count(raw.get("comments"))
    reposts = parse_count(raw.get("reposts"))
    return {
        "source": "linkedin_posts",
        "post_id": post_id,
        "link": link,
        "permalink_available": permalink_available,
        "keyword": keyword,
        "author": author,
        "author_url": str(raw.get("author_url") or "").strip(),
        "text": text,
        "published_text": str(raw.get("published_text") or "").strip(),
        "image_url": str(raw.get("image_url") or "").strip(),
        "media": [entry for entry in raw.get("media", []) if isinstance(entry, dict)][:12],
        "reactions": reactions,
        "comments": comments,
        "reposts": reposts,
        "engagement_score": reactions + 2 * comments + 3 * reposts,
        "observed_at": observed_at,
    }


def save_posts(db_path: str | Path, posts: list[dict]) -> None:
    path = Path(db_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path, timeout=30) as db:
        db.execute("PRAGMA busy_timeout=30000")
        db.execute("""CREATE TABLE IF NOT EXISTS posts (
            post_id TEXT PRIMARY KEY, link TEXT NOT NULL, author TEXT NOT NULL DEFAULT '',
            author_url TEXT NOT NULL DEFAULT '', text TEXT NOT NULL DEFAULT '',
            published_text TEXT NOT NULL DEFAULT '', image_url TEXT NOT NULL DEFAULT '',
            first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
            avatar_url TEXT NOT NULL DEFAULT ''
        )""")
        columns = {row[1] for row in db.execute("PRAGMA table_info(posts)")}
        if "avatar_url" not in columns:
            db.execute("ALTER TABLE posts ADD COLUMN avatar_url TEXT NOT NULL DEFAULT ''")
        avatar_match = "image_url LIKE '%profile-displayphoto%' OR image_url LIKE '%company-logo%'"
        db.execute(f"UPDATE posts SET avatar_url=image_url WHERE ({avatar_match}) AND avatar_url='' ")
        db.execute(f"UPDATE posts SET image_url='' WHERE {avatar_match}")
        db.execute("""CREATE TABLE IF NOT EXISTS post_observations (
            post_id TEXT NOT NULL, keyword TEXT NOT NULL, observed_at TEXT NOT NULL,
            reactions INTEGER NOT NULL, comments INTEGER NOT NULL, reposts INTEGER NOT NULL,
            engagement_score INTEGER NOT NULL,
            PRIMARY KEY (post_id, keyword, observed_at)
        )""")
        db.execute("CREATE INDEX IF NOT EXISTS idx_linkedin_observations_keyword ON post_observations(keyword, observed_at DESC)")
        db.execute("""CREATE TABLE IF NOT EXISTS post_media (
            post_id TEXT NOT NULL, media_index INTEGER NOT NULL, kind TEXT NOT NULL,
            urls_json TEXT NOT NULL DEFAULT '[]', poster_url TEXT NOT NULL DEFAULT '',
            detected_at TEXT NOT NULL, PRIMARY KEY(post_id, media_index)
        )""")
        for post in posts:
            db.execute("""INSERT INTO posts
                (post_id, link, author, author_url, text, published_text, image_url, first_seen_at, last_seen_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(post_id) DO UPDATE SET
                link=CASE WHEN excluded.link='https://www.linkedin.com/feed/' THEN posts.link ELSE excluded.link END,
                author=CASE WHEN excluded.author='' THEN posts.author ELSE excluded.author END,
                author_url=CASE WHEN excluded.author_url='' THEN posts.author_url ELSE excluded.author_url END,
                text=CASE WHEN length(excluded.text)<length(posts.text) THEN posts.text ELSE excluded.text END,
                published_text=CASE WHEN excluded.published_text='' THEN posts.published_text ELSE excluded.published_text END,
                image_url=CASE WHEN excluded.image_url='' THEN posts.image_url ELSE excluded.image_url END,
                last_seen_at=excluded.last_seen_at""",
                (post["post_id"], post["link"], post["author"], post["author_url"],
                 post["text"], post["published_text"], post["image_url"],
                 post["observed_at"], post["observed_at"]))
            db.execute("""INSERT OR IGNORE INTO post_observations VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (post["post_id"], post["keyword"], post["observed_at"],
                 post["reactions"], post["comments"], post["reposts"], post["engagement_score"]))
            for index, media in enumerate(post.get("media", [])[:12]):
                kind = str(media.get("kind") or "")
                if kind not in {"image", "video"}:
                    continue
                urls = [str(url).strip() for url in media.get("urls", []) if str(url).startswith("https://")][:8]
                db.execute("""INSERT INTO post_media (post_id,media_index,kind,urls_json,poster_url,detected_at)
                    VALUES (?,?,?,?,?,?) ON CONFLICT(post_id,media_index) DO UPDATE SET
                    kind=excluded.kind,
                    urls_json=CASE WHEN excluded.urls_json='[]' THEN post_media.urls_json ELSE excluded.urls_json END,
                    poster_url=CASE WHEN excluded.poster_url='' THEN post_media.poster_url ELSE excluded.poster_url END,
                    detected_at=excluded.detected_at""",
                    (post["post_id"], index, kind, json.dumps(urls), str(media.get("poster_url") or ""), post["observed_at"]))


def load_qualifying_posts(
    db_path: str | Path, min_reactions: int, limit: int,
    min_post_age_days: int = 1, max_post_age_days: int = 3,
) -> list[dict]:
    path = Path(db_path).expanduser().resolve()
    if not path.exists():
        return []
    with sqlite3.connect(path, timeout=30) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=30000")
        try:
            rows = db.execute("""SELECT p.post_id, p.link, p.author, p.author_url,
                    p.text, p.published_text, p.image_url,
                    MAX(o.reactions) AS reactions, MAX(o.comments) AS comments,
                    MAX(o.reposts) AS reposts, MAX(o.observed_at) AS observed_at
                FROM posts p JOIN post_observations o ON o.post_id=p.post_id
                WHERE o.keyword='feed'
                GROUP BY p.post_id
                HAVING MAX(o.reactions) >= ?
                ORDER BY reactions DESC, p.post_id""", (min_reactions,)).fetchall()
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc).lower():
                return []
            raise
    return [{
        "source": "linkedin_posts", "post_id": row["post_id"], "link": row["link"],
        "permalink_available": row["link"] != "https://www.linkedin.com/feed/",
        "keyword": "feed", "author": row["author"], "author_url": row["author_url"],
        "text": row["text"], "published_text": row["published_text"],
        "image_url": row["image_url"], "reactions": row["reactions"],
        "comments": row["comments"], "reposts": row["reposts"],
        "engagement_score": row["reactions"] + 2 * row["comments"] + 3 * row["reposts"],
        "observed_at": row["observed_at"],
    } for row in rows if is_post_in_age_window(
        row["published_text"], row["observed_at"], min_post_age_days, max_post_age_days
    )][:limit]


def load_known_post_ids(db_path: str | Path, *, before: str | None = None) -> set[str]:
    """Capture the baseline before a new discovery run, including low-reaction posts."""
    path = Path(db_path).expanduser().resolve()
    if not path.exists():
        return set()
    with sqlite3.connect(path, timeout=30) as db:
        try:
            if before:
                return {row[0] for row in db.execute(
                    "SELECT post_id FROM posts WHERE first_seen_at < ?", (before,)
                )}
            return {row[0] for row in db.execute("SELECT post_id FROM posts")}
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc).lower():
                return set()
            raise


def run_linkedin_feed_scan(
    *, scroll_rounds: int = 0,
    target_popular_posts: int = 50, min_reactions: int = 1_000,
    target_new_posts: int = 10,
    min_post_age_days: int = 1, max_post_age_days: int = 3,
    db_path: str = "data/linkedin_posts.db", browser_mode: str = "chrome_normale",
    browser_user_data_dir: str = "", browser_profile_directory: str = "Default",
    show_browser: bool = False,
    scan_started_at: str = "",
) -> ScrapeOutcome:
    min_post_age_days = int(min_post_age_days)
    max_post_age_days = int(max_post_age_days)
    if not 1 <= min_post_age_days <= max_post_age_days <= 30:
        raise ValueError("L'intervallo dei giorni deve essere compreso tra 1 e 30, con minimo <= massimo.")
    if int(scroll_rounds) < 0:
        raise ValueError("Gli scorrimenti massimi devono essere 0 (illimitati) o un numero positivo.")
    resuming_scan = bool(scan_started_at)
    scan_started_at = scan_started_at or datetime.now().isoformat(timespec="seconds")
    datetime.fromisoformat(scan_started_at)
    config = {
        "scroll_rounds": min(int(scroll_rounds), 200),
        "target_popular_posts": max(1, min(int(target_popular_posts), 100)),
        "target_new_posts": max(0, min(int(target_new_posts), 100)),
        "min_reactions": max(1, min(int(min_reactions), 10_000_000)),
        "min_post_age_days": min_post_age_days,
        "max_post_age_days": max_post_age_days,
        "db_path": str(Path(db_path).expanduser().resolve()),
        "browser_mode": browser_mode, "browser_user_data_dir": browser_user_data_dir,
        "browser_profile_directory": browser_profile_directory,
        "browser_requires_foreground": bool(show_browser),
        "scan_started_at": scan_started_at,
    }
    config["seed_posts"] = load_qualifying_posts(
        db_path, config["min_reactions"], 1_000_000,
        min_post_age_days, max_post_age_days,
    )
    config["known_post_ids"] = load_known_post_ids(
        db_path, before=scan_started_at if resuming_scan else None
    ) if config["target_new_posts"] else set()
    if not config["target_new_posts"] and len(config["seed_posts"]) >= config["target_popular_posts"]:
        payload = {"rows": config["seed_posts"], "candidates": [],
                   "meta": {"feed_url": "https://www.linkedin.com/feed/", "scroll_attempts": 0,
                            "reloads": 0, "reached_bottom": False, "target_reached": True,
                            "stop_reason": "target_already_met", "previous_qualifying": len(config["seed_posts"])}}
    else:
        retry_count = 0
        while True:
            payload = None
            try:
                payload = _scrape_linkedin_feed_task(config)
                break
            except Exception as exc:
                if not _is_recoverable_browser_error(exc):
                    raise
                retry_count += 1
                if config["scroll_rounds"] and retry_count > 2:
                    raise
                config["seed_posts"] = load_qualifying_posts(
                    db_path, config["min_reactions"], 1_000_000,
                    min_post_age_days, max_post_age_days,
                )
                new_count = sum(
                    post["post_id"] not in config["known_post_ids"]
                    for post in config["seed_posts"]
                )
                print(f"[linkedin-feed] browser timeout/restart={retry_count} saved_new={new_count}/{config['target_new_posts']} error={type(exc).__name__}", flush=True)
                if config["target_new_posts"] and new_count >= config["target_new_posts"]:
                    selected = [
                        post for post in config["seed_posts"]
                        if post["post_id"] not in config["known_post_ids"]
                    ][:config["target_new_posts"]]
                    payload = {"rows": selected, "candidates": [], "meta": {
                        "feed_url": "https://www.linkedin.com/feed/", "scroll_attempts": 0,
                        "reloads": retry_count, "target_reached": True,
                        "stop_reason": "target_reached_after_restart",
                        "previous_qualifying": len(config["seed_posts"]),
                        "new_qualifying": len(selected),
                    }}
                    break
                for _ in range(min(12, retry_count) * 2):
                    if consume_stop_after_current_item_request():
                        selected = [
                            post for post in config["seed_posts"]
                            if post["post_id"] not in config["known_post_ids"]
                        ][:config["target_new_posts"]]
                        payload = {"rows": selected, "candidates": [], "meta": {
                            "feed_url": "https://www.linkedin.com/feed/", "scroll_attempts": 0,
                            "reloads": retry_count, "target_reached": False,
                            "stop_reason": "user_stop", "new_qualifying": len(selected),
                        }}
                        break
                    time.sleep(5)
                if payload and payload.get("meta", {}).get("stop_reason") == "user_stop":
                    break
    if not isinstance(payload, dict) or "rows" not in payload:
        raise RuntimeError("La navigazione LinkedIn non ha restituito post. Verifica il log e la schermata del browser.")
    posts = payload["rows"]
    candidates = payload.get("candidates", posts)
    if candidates:
        save_posts(db_path, candidates)
    posts.sort(key=lambda post: (-post["engagement_score"], post["post_id"]))
    return ScrapeOutcome(source="linkedin_posts", rows=posts, meta={
        **payload["meta"], "db_path": str(Path(db_path).resolve()),
        "row_count": len(posts),
        "candidates_seen": len(candidates),
        "target_popular_posts": config["target_popular_posts"],
        "target_new_posts": config["target_new_posts"],
        "unlimited_scroll": config["scroll_rounds"] == 0,
        "scan_started_at": scan_started_at,
        "min_reactions": config["min_reactions"],
        "min_post_age_days": min_post_age_days,
        "max_post_age_days": max_post_age_days,
        "missing_permalinks": sum(not post["permalink_available"] for post in posts),
        "ranking_note": "Engagement score = reactions + 2×comments + 3×reposts; not normalized by audience size. Post age is estimated from LinkedIn's rounded day label. A feed link is shown when LinkedIn hides the post permalink.",
    })


def _is_recoverable_browser_error(exc: Exception) -> bool:
    if isinstance(exc, (TimeoutError, OSError, ConnectionError)):
        return True
    message = str(exc).lower()
    return any(part in message for part in (
        "response not received", "timed out", "timeout", "target closed",
        "connection closed", "websocket", "chrome disconnected",
    ))


@browser(profile=resolve_browser_profile, add_arguments=resolve_browser_arguments,
         wait_for_complete_page_load=False, close_on_crash=True,
         raise_exception=True, create_error_logs=False)
def _scrape_linkedin_feed_task(driver: Driver, config: dict) -> dict:
    all_rows: dict[str, dict] = {post["post_id"]: post for post in config.get("seed_posts", [])}
    known_post_ids = set(config.get("known_post_ids", set()))
    candidates: dict[str, dict] = {}
    saved_versions: dict[str, tuple] = {}
    url = "https://www.linkedin.com/feed/"
    try:
        driver.get(url, wait=Wait.SHORT, timeout=30)
    except TimeoutError:
        print("[linkedin-feed] initial navigation timed out; checking the loaded DOM", flush=True)
    time.sleep(4)
    page = str(driver.run_js("return location.href"))
    if "/login" in page or "/checkpoint" in page or "/authwall" in page:
        raise RuntimeError("LinkedIn richiede il login nel profilo Chrome standard copiato; nessun dato salvato.")
    scroll_attempts = 0
    reached_bottom = False
    reloads = 0
    stagnant_rounds = 0
    previous_count = 0
    stall_cycles = 0
    stop_reason = "scroll_limit"
    round_index = 0
    while config["scroll_rounds"] == 0 or round_index <= config["scroll_rounds"]:
        round_index += 1
        if consume_stop_after_current_item_request():
            stop_reason = "user_stop"
            break
        expanded = driver.run_js(_EXPAND_POSTS_JS) or 0
        if expanded:
            time.sleep(0.4)
        raw = driver.run_js(_EXTRACT_POSTS_JS) or []
        observed_at = datetime.now().isoformat(timespec="seconds")
        changed_posts = []
        for item in raw:
            post = normalize_post(item, "feed", observed_at)
            if post:
                candidates[post["post_id"]] = post
                version = (post["text"], post["author"], post["image_url"], post["published_text"],
                           json.dumps(post["media"], sort_keys=True),
                           post["reactions"], post["comments"], post["reposts"])
                if saved_versions.get(post["post_id"]) != version:
                    changed_posts.append(post)
                    saved_versions[post["post_id"]] = version
                if not is_post_in_age_window(
                    post["published_text"], post["observed_at"],
                    config["min_post_age_days"], config["max_post_age_days"],
                ):
                    all_rows.pop(post["post_id"], None)
                elif post["reactions"] >= config["min_reactions"] and post["reactions"] >= all_rows.get(post["post_id"], {}).get("reactions", 0):
                    all_rows[post["post_id"]] = post
        if changed_posts:
            # Persist each observed batch before the next navigation/scroll.
            # A timeout or an interrupted browser must not discard discoveries.
            save_posts(config["db_path"], changed_posts)
        if len(candidates) > previous_count:
            stagnant_rounds = 0
            stall_cycles = 0
            previous_count = len(candidates)
        elif reached_bottom:
            stagnant_rounds += 1
        new_qualifying = sum(post_id not in known_post_ids for post_id in all_rows)
        target = config["target_new_posts"] or config["target_popular_posts"]
        progress = new_qualifying if config["target_new_posts"] else len(all_rows)
        print(f"[linkedin-feed] round={round_index} visible={len(raw)} candidates={len(candidates)} qualifying={len(all_rows)} new_qualifying={new_qualifying} target={target}", flush=True)
        if progress >= target:
            stop_reason = "target_reached"
            break
        if len(raw) >= 150:
            reloads += 1
            print(f"[linkedin-feed] refreshing crowded feed cards={len(raw)} saved_candidates={len(candidates)}", flush=True)
            driver.get(url, wait=Wait.SHORT, timeout=30)
            time.sleep(4)
            stagnant_rounds = 0
            reached_bottom = False
            continue
        if stagnant_rounds >= 3 and reloads < 2:
            reloads += 1
            print(f"[linkedin-feed] stalled_at={len(candidates)} reloading={reloads}", flush=True)
            try:
                driver.get(url, wait=Wait.SHORT, timeout=30)
            except TimeoutError:
                print("[linkedin-feed] reload timed out; keeping collected posts", flush=True)
            time.sleep(4)
            stagnant_rounds = 0
            reached_bottom = False
            continue
        if stagnant_rounds >= 10 and reloads >= 2:
            if config["scroll_rounds"] != 0:
                stop_reason = "feed_stalled"
                print(f"[linkedin-feed] no new cards after two reloads; stopping at {len(candidates)} candidates", flush=True)
                break
            stall_cycles += 1
            pause = min(300, 30 * (2 ** min(stall_cycles - 1, 4)))
            print(f"[linkedin-feed] feed stalled; waiting {pause}s before reload, new_qualifying={new_qualifying}/{target}", flush=True)
            for _ in range(pause // 5):
                if consume_stop_after_current_item_request():
                    stop_reason = "user_stop"
                    break
                time.sleep(5)
            if stop_reason == "user_stop":
                break
            reloads += 1
            try:
                driver.get(url, wait=Wait.SHORT, timeout=30)
            except TimeoutError:
                print("[linkedin-feed] reload timed out; keeping collected posts", flush=True)
            time.sleep(4)
            stagnant_rounds = 0
            reached_bottom = False
            continue
        if config["scroll_rounds"] == 0 or round_index <= config["scroll_rounds"]:
            before = driver.run_js(_FEED_STATE_JS) or {}
            scroll = driver.run_js(_SCROLL_RESULTS_JS) or {}
            scroll_attempts += 1
            reached_bottom = bool(scroll.get("at_bottom"))
            print(f"[linkedin-feed] scroll target={scroll.get('target')} before={scroll.get('before')} after={scroll.get('after')} moved={scroll.get('moved')} bottom={reached_bottom}", flush=True)
            # At the bottom, give lazy loading time to change the card set or
            # container height. Else use a shorter pause between scrolls.
            _wait_for_feed_growth(driver, before, timeout=7 if reached_bottom else 2)
    if not candidates and not all_rows:
        body = str(driver.run_js("return (document.body?.innerText || '').slice(0, 1000)"))
        if "Sign in" in body or "Accedi" in body or "Join now" in body:
            raise RuntimeError("Sessione LinkedIn non autenticata nel profilo Chrome standard copiato.")
        raise RuntimeError("Nessun post riconoscibile nel feed LinkedIn. Servono gli elementi Inspect di una card post.")
    selected = sorted((post for post in all_rows.values() if (
        not config["target_new_posts"] or post["post_id"] not in known_post_ids
    ) and is_post_in_age_window(
        post["published_text"], post["observed_at"],
        config["min_post_age_days"], config["max_post_age_days"],
    )), key=lambda post: (-post["reactions"], post["post_id"]))[:config["target_new_posts"] or config["target_popular_posts"]]
    print(f"[linkedin-feed] qualifying_posts={len(selected)} candidates={len(candidates)} scroll_attempts={scroll_attempts} reloads={reloads}", flush=True)
    return {"rows": selected, "candidates": list(candidates.values()),
            "meta": {"feed_url": url, "scroll_attempts": scroll_attempts,
                     "reloads": reloads, "reached_bottom": reached_bottom,
                     "target_reached": len(selected) >= (config["target_new_posts"] or config["target_popular_posts"]),
                     "new_qualifying": len(selected) if config["target_new_posts"] else new_qualifying,
                     "stop_reason": stop_reason,
                     "previous_qualifying": len(config.get("seed_posts", []))}}


def _wait_for_feed_growth(driver: Driver, before: dict, *, timeout: int) -> bool:
    for _ in range(timeout):
        time.sleep(1)
        after = driver.run_js(_FEED_STATE_JS) or {}
        if (after.get("height", 0) > before.get("height", 0) + 20
                or after.get("keys") != before.get("keys")):
            return True
    return False


_FEED_STATE_JS = r"""
const cards = [...document.querySelectorAll('[role="listitem"][componentkey^="update-card-focus"]')];
const main = document.querySelector('main');
return {height: main?.scrollHeight || document.scrollingElement?.scrollHeight || 0,
        keys: cards.length + ':' + cards.slice(-20).map((card) => card.getAttribute('componentkey') || '').join('|')};
"""


_EXTRACT_POSTS_JS = r"""
const clean = (value) => String(value || '').replace(/\s+/g, ' ').trim();
const keyToUrn = window.__agentLabLinkedInKeyToUrn ||= new Map();
const scannedScripts = window.__agentLabLinkedInScannedScripts ||= new WeakSet();
for (const script of document.querySelectorAll('script')) {
  if (scannedScripts.has(script)) continue;
  scannedScripts.add(script);
  const source = script.textContent || '';
  const matches = source.matchAll(/reactionState-(urn:li:(?:activity|ugcPost):\d+)[\s\S]{0,500}?UpdateState([A-Za-z0-9_+-]+)/g);
  for (const match of matches) keyToUrn.set(match[2], match[1]);
}
const candidates = [...document.querySelectorAll('[role="listitem"][componentkey^="update-card-focus"], [data-urn*="activity"], [data-urn*="ugcPost"], article')];
const seen = new Set();
return candidates.map((node) => {
  const cardKey = (node.getAttribute('componentkey') || '').replace(/^update-card-focus/, '').replace(/FeedType_.*$/, '');
  const urn = node.getAttribute('data-urn') || keyToUrn.get(cardKey) || '';
  const postLink = [...node.querySelectorAll('a[href]')]
    .find((a) => /\/feed\/update\/|\/posts\/[^/]*activity-/.test(a.href));
  const link = postLink?.href || '';
  const id = (urn + ' ' + link).match(/(?:urn:li:(?:activity|ugcPost):|activity-)(\d{10,})/);
  const identity = id?.[1] || cardKey;
  if (!identity || seen.has(identity)) return null;
  seen.add(identity);
  const authorLink = [...node.querySelectorAll('a[href*="/in/"], a[href*="/company/"]')]
    .find((a) => clean(a.innerText)) || node.querySelector('a[href*="/in/"], a[href*="/company/"]');
  const lines = (node.innerText || '').split('\n').map(clean).filter(Boolean);
  const feedLabel = lines.findIndex((line) => /^(?:feed post|post nel feed)$/i.test(line));
  const fallbackAuthor = feedLabel >= 0 ? lines[feedLabel + 1] || '' : '';
  const textNode = node.querySelector('[data-testid="expandable-text-box"], .update-components-text, .feed-shared-update-v2__description, [data-test-id="main-feed-activity-card__commentary"], .feed-shared-text');
  const timeNode = node.querySelector('time, .update-components-actor__sub-description, .feed-shared-actor__sub-description');
  const metricText = [...node.querySelectorAll('button[aria-label], a[aria-label], .social-details-social-counts__reactions-count, .social-details-social-counts__comments')]
    .map((el) => clean(el.innerText) + ' ' + clean(el.getAttribute('aria-label')));
  const metric = (words) => metricText.find((text) => words.some((word) => text.toLowerCase().includes(word))) || '';
  const img = [...node.querySelectorAll('img')].find((image) => {
    const source = image.currentSrc || image.src || '';
    // The first <img> in a card is usually the author avatar. Only accept
    // LinkedIn's post-content image variants, never profile/company images
    // or a video poster masquerading as a post photo.
    return /media\.licdn\.com\/dms\/image\//i.test(source)
      && /\/(?:feedshare-(?:image|shrink)|image-shrink_)/i.test(source)
      && !/(?:profile-displayphoto|company-logo|videocover)/i.test(source);
  });
  const media = [];
  const mediaUrls = (element) => {
    const urls = [element.currentSrc, element.src, element.getAttribute('data-delayed-url'), element.getAttribute('data-src')];
    for (const candidate of (element.getAttribute('srcset') || '').split(',')) urls.push(candidate.trim().split(/\s+/)[0]);
    return [...new Set(urls.filter((url) => /^https:\/\//i.test(url || '')))];
  };
  for (const image of node.querySelectorAll('img')) {
    const urls = mediaUrls(image).filter((url) => /(?:media\.licdn\.com|licdn\.com\/dms\/image)/i.test(url)
      && !/(?:profile-displayphoto|company-logo|videocover)/i.test(url));
    if (urls.length) media.push({kind: 'image', urls, poster_url: ''});
  }
  for (const video of node.querySelectorAll('video')) {
    const urls = [...new Set([...mediaUrls(video), ...[...video.querySelectorAll('source')].flatMap(mediaUrls)])];
    media.push({kind: 'video', urls, poster_url: video.poster || ''});
  }
  return {
    urn, link, card_key: cardKey,
    author: (clean(authorLink?.innerText).split(' • ')[0] || fallbackAuthor), author_url: authorLink?.href || '',
    text: clean(textNode?.innerText), published_text: clean(timeNode?.innerText) || (clean(node.innerText).match(/(?:^|\s)(\d+\s*(?:m|h|d|w|mo|yr|giorni|ore|minuti))(?:\s|$)/i)?.[1] || ''),
    image_url: img?.currentSrc || img?.src || '', media: media.slice(0, 12),
    reactions: clean(node.querySelector('button[aria-label^="Reaction button state"]')?.innerText)
      || metric(['reaction', 'reazion', 'like', 'mi piace']),
    comments: clean(node.querySelector('button[aria-label="Comment"]')?.innerText) || metric(['comment']),
    reposts: clean(node.querySelector('button[aria-label="Repost"]')?.innerText) || metric(['repost', 'condivision', 'share'])
  };
}).filter(Boolean);
"""


_EXPAND_POSTS_JS = r"""
let expanded = 0;
for (const card of document.querySelectorAll('[role="listitem"][componentkey^="update-card-focus"], [data-urn*="activity"], [data-urn*="ugcPost"], article')) {
  const textBox = card.querySelector('[data-testid="expandable-text-box"], .update-components-text, .feed-shared-update-v2__description');
  if (!textBox) continue;
  const more = textBox.querySelector('[data-testid="expandable-text-button"]')
    || [...textBox.querySelectorAll('button')].find((button) =>
      /^(?:…|\.\.\.)\s*(?:more|altro)$/i.test((button.innerText || '').trim()));
  if (more) { more.click(); expanded += 1; }
}
return expanded;
"""


_SCROLL_RESULTS_JS = r"""
const cards = [...document.querySelectorAll('[role="listitem"][componentkey^="update-card-focus"], [data-urn*="activity"], [data-urn*="ugcPost"], article')];
const doc = document.scrollingElement;
const main = document.querySelector('main');
const candidates = [doc, main, ...cards.slice(-1).flatMap((card) => {
  const parents = [];
  for (let node = card.parentElement; node && node !== document.body; node = node.parentElement) parents.push(node);
  return parents;
})].filter((node, index, all) => node && all.indexOf(node) === index);
const scrollable = candidates.filter((node) => {
  if (node.scrollHeight <= node.clientHeight + 20) return false;
  return node === doc || /(auto|scroll)/.test(getComputedStyle(node).overflowY);
});
const target = scrollable.sort((a, b) => (b.scrollHeight - b.clientHeight) - (a.scrollHeight - a.clientHeight))[0] || doc;
if (!target) return {moved: false, at_bottom: true, target: 'none'};
const before = target.scrollTop;
const heightBefore = target.scrollHeight;
const delta = Math.max(500, target.clientHeight * 0.8);
if (target === doc) window.scrollBy({top: delta, behavior: 'instant'});
else target.scrollTop = Math.min(target.scrollHeight, before + delta);
if (target.scrollTop <= before + 1 && cards.length) cards[cards.length - 1].scrollIntoView({block: 'end', behavior: 'instant'});
const after = target.scrollTop;
return {moved: after > before + 1, at_bottom: after + target.clientHeight >= heightBefore - 4,
        before, after, height: heightBefore, target: target === doc ? 'document' : target.tagName.toLowerCase()};
"""
