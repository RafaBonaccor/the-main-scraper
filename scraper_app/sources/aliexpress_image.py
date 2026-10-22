from __future__ import annotations

import hashlib
import json
import mimetypes
import re
import sqlite3
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

from botasaurus.browser import Driver, Wait, browser

from ..browser_helpers import DEFAULT_COOKIE_REJECT_TEXTS, click_first_matching_text
from ..browser_runtime import resolve_browser_arguments, resolve_browser_profile
from ..models import ScrapeOutcome


ALIEXPRESS_HOME_URL = "https://it.aliexpress.com/"
DEFAULT_DB_PATH = "data/aliexpress_sourcing.db"
SUPPORTED_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".avif", ".gif", ".bmp", ".heic", ".heif"}


def run_aliexpress_image_search(
    *,
    image_path: str = "",
    image_dir: str = "",
    items_file: str = "",
    max_results: int = 100,
    max_price: str | float | None = None,
    db_path: str = DEFAULT_DB_PATH,
    reuse_cache_hours: float = 24.0,
    force_refresh: bool = False,
    watch_interval_seconds: int = 0,
    browser_mode: str = "chrome_normale",
    browser_user_data_dir: str = "",
    browser_profile_directory: str = "Default",
    refresh_browser_profile: bool = False,
    slow_mode: bool = False,
    action_delay_seconds: float = 1.0,
    page_settle_seconds: float = 3.0,
) -> ScrapeOutcome:
    """Run one visual search or continuously process new images from a folder."""
    latest_rows: list[dict] = []
    latest_meta: dict = {}
    cycle = 0
    while True:
        cycle += 1
        image_paths, origins_by_path = resolve_image_inputs(
            image_path=image_path,
            image_dir=image_dir,
            items_file=items_file,
            cache_dir=Path(db_path).expanduser().resolve().parent / "aliexpress_input_cache",
        )
        if not image_paths:
            raise ValueError("Select an image with --image-path or add supported images to --image-dir.")

        cached_rows: list[dict] = []
        pending_paths: list[Path] = []
        for path in image_paths:
            image_hash = hash_image(path)
            cached = [] if force_refresh else load_cached_results(
                db_path, image_hash=image_hash, max_age_hours=reuse_cache_hours
            )
            if cached:
                origin = origins_by_path.get(str(path.resolve()), {})
                cached = [_attach_source_origin(row, origin) for row in cached]
                cached_rows.extend(cached)
                print(f"[aliexpress-image] cache-hit image={path.name} rows={len(cached)}", flush=True)
            else:
                pending_paths.append(path)

        fresh_rows: list[dict] = []
        search_meta: list[dict] = []
        if pending_paths:
            config = {
                "image_paths": [str(path) for path in pending_paths],
                "max_results": max(int(max_results or 0), 0),
                "max_price": parse_price_value(max_price),
                "browser_mode": browser_mode,
                "browser_user_data_dir": browser_user_data_dir,
                "browser_profile_directory": browser_profile_directory,
                "refresh_browser_profile": bool(refresh_browser_profile),
                "slow_mode": bool(slow_mode),
                "action_delay_seconds": max(float(action_delay_seconds or 0), 0.1),
                "page_settle_seconds": max(float(page_settle_seconds or 0), 0.5),
            }
            payload = _search_aliexpress_images_task(config)
            fresh_rows = list(payload.get("rows", []) or [])
            search_meta = list(payload.get("searches", []) or [])
            for row in fresh_rows:
                origin = origins_by_path.get(str(Path(str(row.get("query_image_path", ""))).resolve()), {})
                row.update(_source_origin_fields(origin))
            persist_search_results(
                db_path,
                pending_paths,
                fresh_rows,
                search_meta,
                origins_by_path=origins_by_path,
            )

        latest_rows = deduplicate_and_rank([*fresh_rows, *cached_rows], max_price=max_price)
        if max_results > 0:
            latest_rows = latest_rows[:max_results * len(image_paths)]
        latest_meta = {
            "row_count": len(latest_rows),
            "cycle_index": cycle,
            "images_count": len(image_paths),
            "searched_images_count": len(pending_paths),
            "cached_images_count": len(image_paths) - len(pending_paths),
            "db_path": str(Path(db_path).expanduser().resolve()),
            "watch_interval_seconds": max(int(watch_interval_seconds or 0), 0),
            "searches": search_meta,
            "source_items_count": sum(1 for value in origins_by_path.values() if value),
        }
        if watch_interval_seconds <= 0:
            break
        print(
            f"[aliexpress-image] cycle={cycle} complete rows={len(latest_rows)}; "
            f"next_scan_in={watch_interval_seconds}s",
            flush=True,
        )
        time.sleep(max(int(watch_interval_seconds), 1))
    return ScrapeOutcome(source="aliexpress_image", rows=latest_rows, meta=latest_meta)


def resolve_image_inputs(
    *,
    image_path: str = "",
    image_dir: str = "",
    items_file: str = "",
    cache_dir: Path | None = None,
) -> tuple[list[Path], dict[str, dict]]:
    """Resolve local images and marketplace rows without coupling their runtimes."""
    paths = resolve_image_paths(image_path=image_path, image_dir=image_dir)
    origins_by_path: dict[str, dict] = {str(path.resolve()): {} for path in paths}
    if not str(items_file or "").strip():
        return paths, origins_by_path

    manifest_path = Path(items_file).expanduser().resolve()
    if not manifest_path.is_file():
        raise ValueError(f"Selected-items manifest not found: {manifest_path}")
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid selected-items manifest: {manifest_path}") from exc
    items = payload.get("items", []) if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        raise ValueError("Selected-items manifest must contain a JSON list or an 'items' list.")

    target_dir = (cache_dir or manifest_path.parent / "aliexpress_input_cache").expanduser().resolve()
    seen = {str(path.resolve()) for path in paths}
    failures: list[str] = []
    for index, raw_item in enumerate(items, 1):
        if not isinstance(raw_item, dict):
            failures.append(f"row {index}: invalid object")
            continue
        try:
            local_path = _resolve_source_item_image(raw_item, target_dir=target_dir, index=index)
        except (OSError, ValueError) as exc:
            failures.append(f"row {index}: {exc}")
            continue
        path_key = str(local_path.resolve())
        origins_by_path[path_key] = _normalize_source_origin(raw_item)
        if path_key not in seen:
            paths.append(local_path)
            seen.add(path_key)
    if failures:
        print("[aliexpress-image] input-skipped " + " | ".join(failures), flush=True)
    if not paths:
        detail = "; ".join(failures[:5])
        raise ValueError("No usable images found in selected items." + (f" {detail}" if detail else ""))
    return paths, origins_by_path


def _resolve_source_item_image(item: dict, *, target_dir: Path, index: int) -> Path:
    for key in ("local_image_path", "archive_image_path", "image_path"):
        raw_path = str(item.get(key, "") or "").strip()
        if not raw_path:
            continue
        path = Path(raw_path).expanduser().resolve()
        if path.is_file() and path.suffix.lower() in SUPPORTED_IMAGE_SUFFIXES:
            return path

    image_url = str(item.get("image_url", "") or "").strip()
    if not image_url.lower().startswith(("http://", "https://")):
        raise ValueError("missing local image and valid image_url")
    target_dir.mkdir(parents=True, exist_ok=True)
    item_key = str(item.get("item_id", "") or item.get("id", "") or index).strip()
    url_hash = hashlib.sha256(image_url.encode("utf-8")).hexdigest()[:16]
    suffix = Path(urlparse(image_url).path).suffix.lower()
    if suffix not in SUPPORTED_IMAGE_SUFFIXES:
        suffix = ".jpg"
    destination = target_dir / f"{re.sub(r'[^A-Za-z0-9_-]+', '_', item_key) or index}-{url_hash}{suffix}"
    if destination.is_file() and destination.stat().st_size > 0:
        return destination
    request = Request(
        image_url,
        headers={"User-Agent": "Mozilla/5.0", "Accept": "image/avif,image/webp,image/png,image/jpeg,image/*"},
    )
    with urlopen(request, timeout=25) as response:
        content_type = str(response.headers.get("Content-Type", "") or "").split(";", 1)[0].lower()
        content = response.read(20 * 1024 * 1024 + 1)
    if not content or len(content) > 20 * 1024 * 1024:
        raise ValueError("downloaded image is empty or larger than 20 MB")
    if content_type and not content_type.startswith("image/"):
        raise ValueError(f"image URL returned {content_type}")
    guessed_suffix = mimetypes.guess_extension(content_type) if content_type else None
    if guessed_suffix in SUPPORTED_IMAGE_SUFFIXES and guessed_suffix != destination.suffix:
        destination = destination.with_suffix(guessed_suffix)
    destination.write_bytes(content)
    print(f"[aliexpress-image] source-image-downloaded item={item_key} path={destination}", flush=True)
    return destination


def _normalize_source_origin(item: dict) -> dict:
    return {
        "source": str(item.get("source", "vinted") or "vinted").strip(),
        "source_item_id": str(item.get("item_id", "") or item.get("source_item_id", "") or "").strip(),
        "source_link": str(item.get("link", "") or item.get("source_link", "") or "").strip(),
        "source_title": str(item.get("name", "") or item.get("title", "") or item.get("source_title", "") or "").strip(),
        "source_image_url": str(item.get("image_url", "") or item.get("source_image_url", "") or "").strip(),
    }


def _source_origin_fields(origin: dict) -> dict:
    if not origin:
        return {}
    return {key: value for key, value in _normalize_source_origin(origin).items() if value}


def _attach_source_origin(row: dict, origin: dict) -> dict:
    attached = dict(row)
    attached.update(_source_origin_fields(origin))
    return attached


@browser(
    profile=resolve_browser_profile,
    add_arguments=resolve_browser_arguments,
    wait_for_complete_page_load=False,
    close_on_crash=True,
    max_retry=1,
    retry_wait=1,
    raise_exception=True,
)
def _search_aliexpress_images_task(driver: Driver, config: dict) -> dict:
    all_rows: list[dict] = []
    searches: list[dict] = []
    for raw_path in config["image_paths"]:
        path = Path(raw_path).expanduser().resolve()
        started_at = now_iso()
        print(f"[aliexpress-image] search-start image={path.name}", flush=True)
        driver.get(ALIEXPRESS_HOME_URL, wait=Wait.LONG, timeout=45)
        _wait_seconds(driver, min(float(config["page_settle_seconds"]), 4.0))
        cookie_action = click_first_matching_text(driver, DEFAULT_COOKIE_REJECT_TEXTS)
        upload_selector = _prepare_visual_search_input(driver)
        if not upload_selector:
            debug = _visual_search_debug(driver)
            print(
                "[aliexpress-image] upload-input-missing " + json.dumps(debug, ensure_ascii=False),
                flush=True,
            )
            body_text = str(debug.get("body_preview", "") or "")
            if _looks_blocked(body_text):
                raise RuntimeError("AliExpress blocked the visual-search page with a verification challenge.")
            raise RuntimeError(
                "AliExpress visual-search upload input not found after clicking 'Carica una foto'."
            )
        print(
            f"[aliexpress-image] upload image={path.name} selector={upload_selector}",
            flush=True,
        )
        baseline_state = _visual_results_state(driver)
        driver.upload_file(upload_selector, str(path), wait=Wait.LONG)
        result_state = _wait_for_visual_results(
            driver,
            baseline_state=baseline_state,
            timeout_seconds=max(float(config["page_settle_seconds"]) + 25.0, 30.0),
        )
        print(
            "[aliexpress-image] visual-results-ready " + json.dumps(result_state, ensure_ascii=False),
            flush=True,
        )
        rows = _collect_visual_results(
            driver,
            max_results=int(config["max_results"]),
            max_price=config.get("max_price"),
        )
        image_hash = hash_image(path)
        for row in rows:
            row.update(
                {
                    "source": "aliexpress_image",
                    "query_image_path": str(path),
                    "query_image_name": path.name,
                    "query_image_hash": image_hash,
                    "extracted_at": now_iso(),
                }
            )
        all_rows.extend(rows)
        searches.append(
            {
                "image_path": str(path),
                "image_name": path.name,
                "image_hash": image_hash,
                "started_at": started_at,
                "completed_at": now_iso(),
                "results_count": len(rows),
                "result_url": _current_url(driver),
                "cookie_action": cookie_action or "",
            }
        )
        print(f"[aliexpress-image] search-complete image={path.name} rows={len(rows)}", flush=True)
    return {"rows": all_rows, "searches": searches}


def resolve_image_paths(*, image_path: str = "", image_dir: str = "") -> list[Path]:
    candidates: list[Path] = []
    if str(image_path or "").strip():
        candidates.append(Path(image_path).expanduser().resolve())
    if str(image_dir or "").strip():
        directory = Path(image_dir).expanduser().resolve()
        if not directory.is_dir():
            raise ValueError(f"Image directory not found: {directory}")
        candidates.extend(sorted(path for path in directory.iterdir() if path.is_file()))
    resolved: list[Path] = []
    seen: set[Path] = set()
    for path in candidates:
        if path in seen:
            continue
        if not path.is_file():
            raise ValueError(f"Image not found: {path}")
        if path.suffix.lower() not in SUPPORTED_IMAGE_SUFFIXES:
            if str(image_path or "").strip() and path == Path(image_path).expanduser().resolve():
                raise ValueError(f"Unsupported image format: {path.suffix or '(none)'}")
            continue
        seen.add(path)
        resolved.append(path)
    return resolved


def hash_image(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _prepare_visual_search_input(driver: Driver) -> str:
    selector = str(
        driver.run_js(
            r"""
const normalize = value => (value || '').replace(/\s+/g, ' ').trim().toLowerCase();
const visible = element => {
  const style = window.getComputedStyle(element);
  return style.display !== 'none' && style.visibility !== 'hidden' && element.getClientRects().length > 0;
};
const markInput = () => {
  const inputs = [...document.querySelectorAll('input[type="file"]')];
  const input = inputs.find(node => /image/i.test(node.accept || '')) || inputs[0];
  if (!input) return '';
  input.setAttribute('data-agent-lab-aliexpress-image', 'true');
  return 'input[data-agent-lab-aliexpress-image="true"]';
};
let selector = markInput();
if (selector) return selector;
const phrases = [
  'carica una foto', 'carica foto', 'cerca tramite immagine',
  'upload a photo', 'upload photo', 'search by image',
  'télécharger une photo', 'bild hochladen', 'subir una foto'
];
const nodes = [...document.querySelectorAll('span, button, [role="button"], label, a, div')];
const label = nodes.find(node => visible(node) && phrases.some(text => normalize(node.textContent).includes(text))) ||
  nodes.find(node => {
    if (!visible(node)) return false;
    const hint = normalize([
      node.getAttribute('aria-label'), node.getAttribute('title'), node.getAttribute('data-testid'),
      node.className, node.getAttribute('data-spm-anchor-id')
    ].join(' '));
    return (hint.includes('image') || hint.includes('foto')) && (hint.includes('search') || hint.includes('upload'));
  }) ||
  (() => {
    const image = [...document.querySelectorAll('img')].find(node => /search by image|ricerca per immagine/i.test(node.alt || ''));
    return image ? (image.closest('button, [role="button"], [class*="picture-search"], [class*="picSearch"]') || image) : null;
  })() ||
  document.querySelector('[data-spm-anchor-id*=".i1."], [class*="imageSearch"], [class*="image-search"], [class*="searchByImage"], [class*="picSearch"], [class*="picture-search"]');
if (label) {
  const target = label.closest('button, [role="button"], label, a') || label;
  try { target.click(); } catch (error) {}
}
return markInput();
            """
        )
        or ""
    )
    if selector:
        return selector
    for _ in range(24):
        time.sleep(0.25)
        selector = str(
            driver.run_js(
                """
const inputs = [...document.querySelectorAll('input[type="file"]')];
const input = inputs.find(node => /image/i.test(node.accept || '')) || inputs[0];
if (!input) return '';
input.setAttribute('data-agent-lab-aliexpress-image', 'true');
return 'input[data-agent-lab-aliexpress-image="true"]';
                """
            )
            or ""
        )
        if selector:
            return selector
    return ""


def _visual_search_debug(driver: Driver) -> dict:
    try:
        return dict(
            driver.run_js(
                r"""
const normalize = value => (value || '').replace(/\s+/g, ' ').trim();
const interesting = [...document.querySelectorAll('span,button,[role="button"],label,a,input')]
  .map(node => ({
    tag: node.tagName,
    text: normalize(node.textContent).slice(0, 160),
    aria: node.getAttribute('aria-label') || '',
    title: node.getAttribute('title') || '',
    type: node.getAttribute('type') || '',
    accept: node.getAttribute('accept') || '',
    class_name: String(node.className || '').slice(0, 180),
    spm: node.getAttribute('data-spm-anchor-id') || ''
  }))
  .filter(item => /foto|photo|image|immagine|upload/i.test(
    [item.text, item.aria, item.title, item.accept, item.class_name, item.spm].join(' ')
  ))
  .slice(0, 25);
return {
  url: location.href,
  title: document.title,
  ready_state: document.readyState,
  file_inputs: document.querySelectorAll('input[type="file"]').length,
  iframes: [...document.querySelectorAll('iframe')].map(frame => frame.src || frame.name || '').slice(0, 10),
  interesting,
  body_preview: normalize(document.body ? document.body.innerText : '').slice(0, 1500)
};
                """
            )
            or {}
        )
    except Exception as exc:
        return {"debug_error": f"{type(exc).__name__}: {exc}"}


def _wait_for_visual_results(driver: Driver, *, baseline_state: dict, timeout_seconds: float) -> dict:
    deadline = time.monotonic() + timeout_seconds
    baseline_links = set(baseline_state.get("links", []) or [])
    baseline_url = str(baseline_state.get("url", "") or "")
    last_state: dict = {}
    while time.monotonic() < deadline:
        _select_latest_page_tab(driver)
        state = _visual_results_state(driver)
        last_state = state
        current_links = set(state.get("links", []) or [])
        new_links = current_links - baseline_links
        url_changed = bool(state.get("url")) and str(state.get("url")) != baseline_url
        visual_context = bool(state.get("visual_context"))
        if current_links and (url_changed or visual_context or len(new_links) >= 3):
            return {
                "url": state.get("url", ""),
                "links_count": len(current_links),
                "new_links_count": len(new_links),
                "url_changed": url_changed,
                "visual_context": visual_context,
            }
        if _looks_blocked(str(state.get("text", "") or "")):
            raise RuntimeError("AliExpress requested verification before showing visual-search results.")
        time.sleep(0.35)
    raise TimeoutError(
        f"AliExpress accepted the image but no distinct visual result set loaded within {timeout_seconds:.0f}s. "
        f"Last state: url={last_state.get('url', '')} links={len(last_state.get('links', []) or [])} "
        f"visual_context={bool(last_state.get('visual_context'))}."
    )


def _visual_results_state(driver: Driver) -> dict:
    return dict(
        driver.run_js(
            r"""
const links = [...new Set([...document.querySelectorAll('a[href*="/item/"]')]
  .map(node => (node.href || '').split('?')[0]).filter(Boolean))];
const body = document.body ? document.body.innerText : '';
const visualContext = Boolean(document.querySelector(
  '[class*="image-search-result"], [class*="picture-search-result"], [class*="pic-search-result"], ' +
  '[class*="imageSearchResult"], [data-spm*="image_search"], [data-spm*="pic_search"]'
)) || /search by image results|risultati[^\n]{0,30}immagine/i.test(body);
return { links, text: body.slice(0, 5000), url: location.href, visual_context: visualContext };
            """
        )
        or {}
    )


def _select_latest_page_tab(driver: Driver) -> None:
    try:
        tabs = list(driver._browser.tabs)
        if len(tabs) > 1 and tabs[-1] is not driver._tab:
            driver._tab = tabs[-1]
    except Exception:
        return


def _collect_visual_results(driver: Driver, *, max_results: int, max_price: float | None) -> list[dict]:
    rows_by_link: dict[str, dict] = {}
    stable_rounds = 0
    previous_count = 0
    max_rounds = 50 if max_results <= 0 else max(8, min(50, (max_results // 12) + 8))
    for round_index in range(1, max_rounds + 1):
        for payload in _extract_product_cards(driver):
            row = normalize_product_row(payload)
            if not row["link"]:
                continue
            if max_price is not None and row.get("price_value") is not None and row["price_value"] > max_price:
                continue
            rows_by_link[row["link"]] = row
        if max_results > 0 and len(rows_by_link) >= max_results:
            break
        scroll_state = driver.run_js(
            """
const before = window.scrollY;
window.scrollTo({top: document.documentElement.scrollHeight, behavior: 'instant'});
return {before, after: window.scrollY, height: document.documentElement.scrollHeight};
            """
        ) or {}
        current_count = len(rows_by_link)
        stable_rounds = stable_rounds + 1 if current_count == previous_count else 0
        previous_count = current_count
        print(
            f"[aliexpress-image] scroll round={round_index} rows={current_count} stable={stable_rounds} "
            f"height={scroll_state.get('height', 0)}",
            flush=True,
        )
        if stable_rounds >= 3:
            break
        time.sleep(0.65)
    rows = deduplicate_and_rank(list(rows_by_link.values()), max_price=max_price)
    return rows if max_results <= 0 else rows[:max_results]


def _extract_product_cards(driver: Driver) -> list[dict]:
    return list(
        driver.run_js(
            r"""
const clean = value => (value || '').replace(/\s+/g, ' ').trim();
const links = [...document.querySelectorAll('a[href*="/item/"]')];
return links.map(anchor => {
  const card = anchor.closest('[class*="search-card-item"], [class*="card-out-wrapper"], [class*="product-card"], [data-pl]') || anchor;
  const images = [...card.querySelectorAll('img')].filter(node => {
    const source = node.currentSrc || node.src || node.getAttribute('data-src') || '';
    return source && !/\/(?:48x48|154x64|64x6[45])\.|badge|icon/i.test(source);
  }).sort((left, right) => ((right.naturalWidth || right.width || 0) * (right.naturalHeight || right.height || 0)) -
    ((left.naturalWidth || left.width || 0) * (left.naturalHeight || left.height || 0)));
  const image = images[0] || null;
  const backgroundNode = [...card.querySelectorAll('[style*="background-image"]')].find(node => {
    const background = node.style.backgroundImage || getComputedStyle(node).backgroundImage || '';
    return /https?:\/\//i.test(background);
  });
  const backgroundUrl = backgroundNode
    ? (backgroundNode.style.backgroundImage || getComputedStyle(backgroundNode).backgroundImage || '').replace(/^url\(["']?|["']?\)$/g, '')
    : '';
  const text = clean(card.innerText || card.textContent);
  const title = clean(
    anchor.getAttribute('title') ||
    (image && image.getAttribute('alt')) ||
    (card.querySelector('[title]') && card.querySelector('[title]').getAttribute('title')) ||
    text.split('\n')[0]
  );
  return {
    link: anchor.href || anchor.getAttribute('href') || '',
    title,
    image_url: image ? (image.currentSrc || image.src || image.getAttribute('data-src') || '') : backgroundUrl,
    raw_text: text,
  };
}).filter(row => row.link);
            """
        )
        or []
    )


def normalize_product_row(payload: dict) -> dict:
    link = normalize_product_url(str(payload.get("link", "") or ""))
    raw_text = re.sub(r"\s+", " ", str(payload.get("raw_text", "") or "")).strip()
    price_value, price_text = extract_price(raw_text)
    rating = extract_rating(raw_text)
    sold_count = extract_sold_count(raw_text)
    product_match = re.search(r"/item/(\d+)", link)
    return {
        "product_id": product_match.group(1) if product_match else "",
        "title": re.sub(r"\s+", " ", str(payload.get("title", "") or "")).strip(),
        "link": link,
        "image_url": str(payload.get("image_url", "") or ""),
        "price": price_text,
        "price_value": price_value,
        "rating": rating,
        "sold_count": sold_count,
        "raw_text": raw_text,
        "result_score": score_result(price_value=price_value, rating=rating, sold_count=sold_count),
    }


def normalize_product_url(value: str) -> str:
    absolute = urljoin(ALIEXPRESS_HOME_URL, value.strip())
    parsed = urlparse(absolute)
    return f"{parsed.scheme or 'https'}://{parsed.netloc}{parsed.path}" if parsed.netloc else ""


def extract_price(text: str) -> tuple[float | None, str]:
    patterns = (
        r"([0-9]+(?:[.,][0-9]{1,2})?)\s*(?:€|EUR)",
        r"(?:€|EUR)\s*([0-9]+(?:[.,][0-9]{1,2})?)",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            value = float(match.group(1).replace(",", "."))
            return value, match.group(0).strip()
    return None, ""


def parse_price_value(value: str | float | None) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"[0-9]+(?:[.,][0-9]+)?", str(value))
    return float(match.group(0).replace(",", ".")) if match else None


def extract_rating(text: str) -> float | None:
    match = re.search(
        r"(?:rating|valutazione)\s*[: ]?\s*([0-5](?:[.,][0-9])?)|"
        r"([0-5](?:[.,][0-9])?)\s*(?:/\s*5|stelle|stars)|"
        r"(?:€|EUR)\s*([0-5](?:[.,][0-9]))\s*[0-9]+(?:[.,][0-9]+)?\s*[km]?\s*(?:vendut[ioe]|sold|ordini|orders)",
        text,
        re.IGNORECASE,
    )
    if not match:
        return None
    raw_value = match.group(1) or match.group(2) or match.group(3)
    value = float(raw_value.replace(",", "."))
    return value if 0 <= value <= 5 else None


def extract_sold_count(text: str) -> int:
    combined = re.search(
        r"(?:€|EUR)\s*([0-5](?:[.,][0-9]))([0-9]+(?:[.,][0-9]+)?\s*[km]?)\s*(?:vendut[ioe]|sold|ordini|orders)",
        text,
        re.IGNORECASE,
    )
    if combined:
        rating_candidate = float(combined.group(1).replace(",", "."))
        if rating_candidate <= 5:
            return _parse_compact_count(combined.group(2))
    match = re.search(r"([0-9]+(?:[.,][0-9]+)?\s*[km]?)\s*(?:vendut[ioe]|sold|ordini|orders)", text, re.IGNORECASE)
    if not match:
        return 0
    return _parse_compact_count(match.group(1))


def _parse_compact_count(value: str) -> int:
    raw = value.lower().replace(" ", "").replace(",", ".")
    multiplier = 1000 if raw.endswith("k") else 1_000_000 if raw.endswith("m") else 1
    raw = raw.rstrip("km")
    try:
        return int(float(raw) * multiplier)
    except ValueError:
        return 0


def score_result(*, price_value: float | None, rating: float | None, sold_count: int) -> float:
    score = min(max(sold_count, 0), 10_000) / 100.0
    if rating is not None:
        score += rating * 12.0
    if price_value is not None:
        score += max(25.0 - min(price_value, 25.0), 0.0)
    return round(score, 2)


def deduplicate_and_rank(rows: list[dict], *, max_price: str | float | None = None) -> list[dict]:
    price_cap = parse_price_value(max_price)
    unique: dict[str, dict] = {}
    for row in rows:
        product_key = str(row.get("product_id", "") or row.get("link", "") or "").strip()
        if not product_key:
            continue
        image_hash = str(row.get("query_image_hash", "") or "").strip()
        key = f"{image_hash}:{product_key}" if image_hash else product_key
        price = parse_price_value(row.get("price_value"))
        if price_cap is not None and price is not None and price > price_cap:
            continue
        previous = unique.get(key)
        if previous is None or float(row.get("result_score", 0) or 0) > float(previous.get("result_score", 0) or 0):
            unique[key] = row
    ranked = sorted(unique.values(), key=lambda row: (-float(row.get("result_score", 0) or 0), float(row.get("price_value") or 10**9)))
    for index, row in enumerate(ranked, 1):
        row["result_rank"] = index
    return ranked


def persist_search_results(
    db_path: str,
    image_paths: list[Path],
    rows: list[dict],
    searches: list[dict],
    *,
    origins_by_path: dict[str, dict] | None = None,
) -> None:
    path = Path(db_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    run_id = uuid.uuid4().hex
    with sqlite3.connect(path) as connection:
        _ensure_schema(connection)
        search_by_hash = {str(item.get("image_hash", "")): item for item in searches}
        for image_path in image_paths:
            image_hash = hash_image(image_path)
            search = search_by_hash.get(image_hash, {})
            connection.execute(
                """
                INSERT INTO aliexpress_image_search_runs
                    (run_id, image_hash, image_path, started_at, completed_at, result_url, results_count, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'completed')
                """,
                (
                    run_id,
                    image_hash,
                    str(image_path),
                    search.get("started_at", now_iso()),
                    search.get("completed_at", now_iso()),
                    search.get("result_url", ""),
                    int(search.get("results_count", 0) or 0),
                ),
            )
            origin = (origins_by_path or {}).get(str(image_path.resolve()), {})
            if origin:
                normalized_origin = _normalize_source_origin(origin)
                source_key = normalized_origin["source_item_id"] or normalized_origin["source_link"] or image_hash
                connection.execute(
                    """
                    INSERT INTO aliexpress_source_items
                        (source_key, source, source_item_id, source_link, source_title, source_image_url,
                         image_hash, local_image_path, linked_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(source_key, image_hash) DO UPDATE SET
                        source=excluded.source, source_item_id=excluded.source_item_id,
                        source_link=excluded.source_link, source_title=excluded.source_title,
                        source_image_url=excluded.source_image_url,
                        local_image_path=excluded.local_image_path, linked_at=excluded.linked_at
                    """,
                    (
                        source_key,
                        normalized_origin["source"],
                        normalized_origin["source_item_id"],
                        normalized_origin["source_link"],
                        normalized_origin["source_title"],
                        normalized_origin["source_image_url"],
                        image_hash,
                        str(image_path),
                        now_iso(),
                    ),
                )
        for row in rows:
            connection.execute(
                """
                INSERT INTO aliexpress_image_results
                    (image_hash, product_id, link, title, image_url, price_value, price_text, rating,
                     sold_count, result_score, raw_json, first_seen_at, last_seen_at, seen_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(image_hash, link) DO UPDATE SET
                    title=excluded.title, image_url=excluded.image_url, price_value=excluded.price_value,
                    price_text=excluded.price_text, rating=excluded.rating, sold_count=excluded.sold_count,
                    result_score=excluded.result_score, raw_json=excluded.raw_json,
                    last_seen_at=excluded.last_seen_at, seen_count=aliexpress_image_results.seen_count + 1
                """,
                (
                    row.get("query_image_hash", ""), row.get("product_id", ""), row.get("link", ""),
                    row.get("title", ""), row.get("image_url", ""), row.get("price_value"), row.get("price", ""),
                    row.get("rating"), int(row.get("sold_count", 0) or 0), float(row.get("result_score", 0) or 0),
                    json.dumps(row, ensure_ascii=False), now_iso(), now_iso(),
                ),
            )


def load_cached_results(db_path: str, *, image_hash: str, max_age_hours: float) -> list[dict]:
    path = Path(db_path).expanduser().resolve()
    if max_age_hours <= 0 or not path.exists():
        return []
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=max_age_hours)).isoformat(timespec="seconds")
    with sqlite3.connect(path) as connection:
        _ensure_schema(connection)
        records = connection.execute(
            "SELECT raw_json FROM aliexpress_image_results WHERE image_hash = ? AND last_seen_at >= ?",
            (image_hash, cutoff),
        ).fetchall()
    rows: list[dict] = []
    for (raw_json,) in records:
        try:
            parsed = json.loads(raw_json)
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(parsed, dict):
            parsed["from_cache"] = True
            rows.append(parsed)
    return rows


def _ensure_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS aliexpress_image_search_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            image_hash TEXT NOT NULL,
            image_path TEXT NOT NULL,
            started_at TEXT NOT NULL,
            completed_at TEXT NOT NULL,
            result_url TEXT NOT NULL DEFAULT '',
            results_count INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'completed'
        );
        CREATE INDEX IF NOT EXISTS idx_aliexpress_runs_image
            ON aliexpress_image_search_runs(image_hash, completed_at);
        CREATE TABLE IF NOT EXISTS aliexpress_image_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            image_hash TEXT NOT NULL,
            product_id TEXT NOT NULL DEFAULT '',
            link TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            image_url TEXT NOT NULL DEFAULT '',
            price_value REAL,
            price_text TEXT NOT NULL DEFAULT '',
            rating REAL,
            sold_count INTEGER NOT NULL DEFAULT 0,
            result_score REAL NOT NULL DEFAULT 0,
            raw_json TEXT NOT NULL,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            seen_count INTEGER NOT NULL DEFAULT 1,
            UNIQUE(image_hash, link)
        );
        CREATE INDEX IF NOT EXISTS idx_aliexpress_results_rank
            ON aliexpress_image_results(image_hash, result_score DESC);
        CREATE TABLE IF NOT EXISTS aliexpress_source_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_key TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT '',
            source_item_id TEXT NOT NULL DEFAULT '',
            source_link TEXT NOT NULL DEFAULT '',
            source_title TEXT NOT NULL DEFAULT '',
            source_image_url TEXT NOT NULL DEFAULT '',
            image_hash TEXT NOT NULL,
            local_image_path TEXT NOT NULL DEFAULT '',
            linked_at TEXT NOT NULL,
            UNIQUE(source_key, image_hash)
        );
        CREATE INDEX IF NOT EXISTS idx_aliexpress_source_items_hash
            ON aliexpress_source_items(image_hash, linked_at DESC);
        """
    )


def _looks_blocked(text: str) -> bool:
    normalized = text.lower()
    return any(marker in normalized for marker in ("verify you are human", "security verification", "captcha", "verifica di sicurezza"))


def _current_url(driver: Driver) -> str:
    try:
        return str(driver.run_js("return location.href;") or "")
    except Exception:
        return ""


def _wait_seconds(driver: Driver, seconds: float) -> None:
    if seconds > 0:
        driver.sleep(seconds)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
