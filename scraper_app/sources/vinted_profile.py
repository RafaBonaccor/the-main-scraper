import time
import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from botasaurus.browser import Driver, Wait, browser

from ..browser_helpers import DEFAULT_COOKIE_REJECT_TEXTS, click_first_matching_text, current_page_url
from ..browser_runtime import resolve_browser_arguments, resolve_browser_profile
from ..discord_notifications import build_vinted_profile_report_discord_message, send_discord_webhook_message
from ..exporters import write_outcome_json
from ..models import ScrapeOutcome
from ..runtime_controls import consume_stop_after_current_item_request
from ..utils import normalize_whitespace
from ..vinted_database import (
    DEFAULT_VINTED_DB_PATH,
    extract_vinted_item_id_from_link,
    normalize_vinted_member_id,
    save_vinted_profile_snapshot,
)
from .vinted import VINTED_BASE_URL, normalize_vinted_image_url, normalize_vinted_item_url


VINTED_PROFILE_NAVIGATION_TIMEOUT_SECONDS = 20


def _log_vinted_profile(message: str, **fields: object) -> None:
    def safe_log_value(value: object, limit: int = 180) -> str:
        text = str(value)
        text = text.replace("\r", "\\r").replace("\n", "\\n")
        if len(text) > limit:
            return f"{text[:limit]}…"
        return text

    details = " ".join(f"{key}={safe_log_value(value)}" for key, value in fields.items() if value is not None)
    if details:
        print(f"[vinted-profile] {message} | {details}", flush=True)
    else:
        print(f"[vinted-profile] {message}", flush=True)


def run_vinted_profile_monitor(
    profile_url: str,
    profile_urls: list[str] | None = None,
    max_items: int = 0,
    interval_seconds: int = 0,
    db_path: str = str(DEFAULT_VINTED_DB_PATH),
    ui_result_json: str = "",
    browser_mode: str = "chrome_normale",
    browser_user_data_dir: str = "",
    browser_profile_directory: str = "Default",
    keep_browser_open: bool = True,
    refresh_browser_profile: bool = False,
    keep_open_seconds: int = 0,
    slow_mode: bool = False,
    action_delay_seconds: float = 1.5,
    page_settle_seconds: float = 3.0,
    discord_profile_report: bool = False,
    discord_webhook_url: str = "",
) -> ScrapeOutcome:
    normalized_profile_urls = normalize_vinted_profile_urls(profile_urls or profile_url)
    if not normalized_profile_urls:
        raise ValueError("Inserisci un URL profilo Vinted valido, per esempio https://www.vinted.it/member/262102939")
    normalized_interval = max(int(interval_seconds or 0), 0)
    _log_vinted_profile(
        "monitor-start",
        profiles=len(normalized_profile_urls),
        interval_seconds=normalized_interval,
        max_items=max(int(max_items or 0), 0),
        db_path=str(db_path or DEFAULT_VINTED_DB_PATH),
        discord_report=bool(discord_profile_report),
        keep_browser_open=bool(keep_browser_open),
        browser_mode=browser_mode,
    )
    for index, normalized_url in enumerate(normalized_profile_urls, start=1):
        _log_vinted_profile("profile-input", index=index, profile_url=normalized_url)
    latest_outcome: ScrapeOutcome | None = None
    cycle_index = 0
    while True:
        if consume_stop_after_current_item_request():
            break
        cycle_index += 1
        cycle_rows: list[dict] = []
        profile_metas: list[dict] = []
        _log_vinted_profile(
            "cycle-start",
            cycle=cycle_index,
            profiles=len(normalized_profile_urls),
            db_path=str(db_path or DEFAULT_VINTED_DB_PATH),
        )
        for index, current_profile_url in enumerate(normalized_profile_urls, start=1):
            if consume_stop_after_current_item_request():
                break
            _log_vinted_profile(
                "profile-task-start",
                cycle=cycle_index,
                index=index,
                profiles=len(normalized_profile_urls),
                profile_url=current_profile_url,
            )
            payload = _scrape_vinted_profile_task(
                {
                    "profile_url": current_profile_url,
                    "profile_index": index,
                    "profile_count": len(normalized_profile_urls),
                    "max_items": max(int(max_items or 0), 0),
                    "db_path": db_path,
                    "ui_result_json": "",
                    "browser_mode": browser_mode,
                    "browser_user_data_dir": browser_user_data_dir,
                    "browser_profile_directory": browser_profile_directory,
                    "keep_browser_open": bool(keep_browser_open),
                    "refresh_browser_profile": bool(refresh_browser_profile),
                    "keep_open_seconds": max(int(keep_open_seconds or 0), 0),
                    "slow_mode": bool(slow_mode),
                    "action_delay_seconds": _nonnegative_float(action_delay_seconds, 1.5),
                    "page_settle_seconds": _nonnegative_float(page_settle_seconds, 3.0),
                    "cycle_index": cycle_index,
                    "interval_seconds": normalized_interval,
                    "discord_profile_report": bool(discord_profile_report),
                    "discord_webhook_url": str(discord_webhook_url or "").strip(),
                },
                reuse_driver=bool(keep_browser_open),
            )
            cycle_rows.extend(payload["rows"])
            profile_metas.append(payload["meta"])
            profile_meta = payload["meta"]
            _log_vinted_profile(
                "profile-task-finished",
                cycle=cycle_index,
                index=index,
                member_id=profile_meta.get("member_id") or profile_meta.get("profile_member_id") or "",
                rows=len(payload["rows"]),
                active=profile_meta.get("profile_item_count", 0),
                new=profile_meta.get("profile_new_count", 0),
                gone=profile_meta.get("profile_gone_count", 0),
                discord_sent=profile_meta.get("discord_profile_report_sent", False),
                current_url=profile_meta.get("current_url", ""),
            )
        aggregate_meta = _build_vinted_profile_aggregate_meta(
            profile_urls=normalized_profile_urls,
            profile_metas=profile_metas,
            cycle_index=cycle_index,
            interval_seconds=normalized_interval,
            db_path=db_path,
        )
        latest_outcome = ScrapeOutcome(source="vinted_profile", rows=cycle_rows, meta=aggregate_meta)
        _log_vinted_profile(
            "cycle-finished",
            cycle=cycle_index,
            rows=len(cycle_rows),
            active=aggregate_meta.get("profile_item_count", 0),
            new=aggregate_meta.get("profile_new_count", 0),
            gone=aggregate_meta.get("profile_gone_count", 0),
            db_path=aggregate_meta.get("db_path", ""),
        )
        if ui_result_json:
            ui_result_path = Path(ui_result_json).expanduser()
            ui_result_path.parent.mkdir(parents=True, exist_ok=True)
            write_outcome_json(ui_result_path, latest_outcome)
            _log_vinted_profile("ui-result-written", path=str(ui_result_path), rows=len(cycle_rows))
        if normalized_interval <= 0:
            return latest_outcome
        print(
            f"[vinted-profile] ciclo {cycle_index} completato: "
            f"{aggregate_meta.get('profile_count', 0)} profili, "
            f"{aggregate_meta.get('profile_item_count', 0)} attivi, "
            f"{aggregate_meta.get('profile_new_count', 0)} nuovi, "
            f"{aggregate_meta.get('profile_gone_count', 0)} spariti. "
            f"Pausa {normalized_interval}s.",
            flush=True,
        )
        deadline = time.monotonic() + normalized_interval
        while time.monotonic() < deadline:
            if consume_stop_after_current_item_request():
                return latest_outcome
            time.sleep(min(1.0, max(deadline - time.monotonic(), 0.0)))
    if latest_outcome is not None:
        return latest_outcome
    raise RuntimeError("Monitor profilo Vinted interrotto prima di completare il primo ciclo.")


@browser(
    profile=resolve_browser_profile,
    add_arguments=resolve_browser_arguments,
    wait_for_complete_page_load=False,
)
def _scrape_vinted_profile_task(driver: Driver, config: dict) -> dict:
    profile_url = str(config["profile_url"])
    member_id = normalize_vinted_member_id(profile_url)
    db_path = str(config.get("db_path", "") or DEFAULT_VINTED_DB_PATH)
    _log_vinted_profile(
        "browser-open-profile",
        cycle=config.get("cycle_index", 1),
        index=config.get("profile_index", 1),
        profile_url=profile_url,
        member_id=member_id,
        db_path=db_path,
    )
    driver.get(profile_url, wait=Wait.SHORT, timeout=VINTED_PROFILE_NAVIGATION_TIMEOUT_SECONDS)
    time.sleep(min(max(float(config.get("page_settle_seconds", 3.0) or 0), 0.5), 5.0))
    cookie_action = click_first_matching_text(driver, DEFAULT_COOKIE_REJECT_TEXTS)
    if cookie_action:
        time.sleep(min(float(config.get("action_delay_seconds", 1.5) or 0), 0.35))
    _log_vinted_profile(
        "browser-profile-loaded",
        profile_url=profile_url,
        member_id=member_id,
        current_url=str(current_page_url(driver) or ""),
        cookie_action=cookie_action or "",
    )
    rows = _scroll_and_read_vinted_profile_items(
        driver,
        max_items=max(int(config.get("max_items", 0) or 0), 0),
        page_settle_seconds=float(config.get("page_settle_seconds", 3.0) or 0),
    )
    missing_identity_before = sum(
        1
        for row in rows
        if not str(row.get("profile_url", "") or "").strip() or not str(row.get("member_id", "") or "").strip()
    )
    _log_vinted_profile(
        "profile-items-read",
        profile_url=profile_url,
        member_id=member_id,
        rows=len(rows),
        missing_identity_before=missing_identity_before,
    )
    rows = _attach_vinted_profile_identity(rows, profile_url=profile_url, member_id=member_id)
    missing_identity_after = sum(
        1
        for row in rows
        if not str(row.get("profile_url", "") or "").strip() or not str(row.get("member_id", "") or "").strip()
    )
    if missing_identity_after:
        _log_vinted_profile(
            "profile-items-enriched",
            profile_url=profile_url,
            member_id=member_id,
            rows=len(rows),
            missing_identity_after=missing_identity_after,
        )
    snapshot_meta = save_vinted_profile_snapshot(
        profile_url=profile_url,
        rows=rows,
        db_path=db_path,
    )
    _log_vinted_profile(
        "snapshot-saved",
        profile_url=profile_url,
        member_id=member_id,
        snapshot_id=snapshot_meta.get("profile_snapshot_id", ""),
        active=snapshot_meta.get("profile_item_count", 0),
        new=snapshot_meta.get("profile_new_count", 0),
        gone=snapshot_meta.get("profile_gone_count", 0),
        still=snapshot_meta.get("profile_still_count", 0),
        incomplete=snapshot_meta.get("profile_snapshot_incomplete", False),
        previous=snapshot_meta.get("profile_previous_item_count", ""),
        missing=snapshot_meta.get("profile_missing_item_count", ""),
        db_path=snapshot_meta.get("db_path", db_path),
    )
    gone_rows = list(snapshot_meta.get("profile_gone_rows", []) or [])
    output_rows = _prioritize_profile_rows([*rows, *gone_rows])
    meta = {
        "profile_url": profile_url,
        "member_id": member_id,
        "profile_index": int(config.get("profile_index", 1) or 1),
        "profile_count": int(config.get("profile_count", 1) or 1),
        "current_url": str(current_page_url(driver) or ""),
        "max_items": max(int(config.get("max_items", 0) or 0), 0),
        "cycle_index": int(config.get("cycle_index", 1) or 1),
        "interval_seconds": int(config.get("interval_seconds", 0) or 0),
        "cookie_banner_action": cookie_action or "",
        "row_count": len(output_rows),
        "keep_browser_open": bool(config.get("keep_browser_open", False)),
        "keep_open_seconds": int(config.get("keep_open_seconds", 0) or 0),
        "extracted_at": datetime.now().isoformat(timespec="seconds"),
        **snapshot_meta,
    }
    meta.update(_notify_vinted_profile_report(output_rows, meta, config))
    _log_vinted_profile(
        "profile-output-ready",
        profile_url=profile_url,
        member_id=member_id,
        output_rows=len(output_rows),
        discord_report=meta.get("discord_profile_report", False),
        discord_sent=meta.get("discord_profile_report_sent", False),
        discord_error=meta.get("discord_profile_report_error", ""),
    )
    ui_result_json = str(config.get("ui_result_json", "") or "").strip()
    if ui_result_json:
        ui_result_path = Path(ui_result_json).expanduser()
        ui_result_path.parent.mkdir(parents=True, exist_ok=True)
        write_outcome_json(ui_result_path, ScrapeOutcome(source="vinted_profile", rows=output_rows, meta=meta))
        _log_vinted_profile("profile-ui-result-written", path=str(ui_result_path), output_rows=len(output_rows))
    return {"rows": output_rows, "meta": meta}


def _attach_vinted_profile_identity(rows: list[dict], *, profile_url: str, member_id: str) -> list[dict]:
    enriched_rows: list[dict] = []
    for row in rows:
        enriched = dict(row)
        enriched["profile_url"] = str(enriched.get("profile_url", "") or profile_url)
        enriched["member_id"] = str(enriched.get("member_id", "") or member_id)
        enriched_rows.append(enriched)
    return enriched_rows


def _build_vinted_profile_aggregate_meta(
    *,
    profile_urls: list[str],
    profile_metas: list[dict],
    cycle_index: int,
    interval_seconds: int,
    db_path: str,
) -> dict[str, object]:
    return {
        "profile_urls": profile_urls,
        "profile_count": len(profile_urls),
        "profile_url": profile_urls[0] if len(profile_urls) == 1 else f"{len(profile_urls)} profili Vinted",
        "member_id": "",
        "cycle_index": cycle_index,
        "interval_seconds": interval_seconds,
        "db_path": str(db_path or DEFAULT_VINTED_DB_PATH),
        "row_count": sum(int(meta.get("row_count", 0) or 0) for meta in profile_metas),
        "profile_item_count": sum(int(meta.get("profile_item_count", 0) or 0) for meta in profile_metas),
        "profile_new_count": sum(int(meta.get("profile_new_count", 0) or 0) for meta in profile_metas),
        "profile_gone_count": sum(int(meta.get("profile_gone_count", 0) or 0) for meta in profile_metas),
        "profile_still_count": sum(int(meta.get("profile_still_count", 0) or 0) for meta in profile_metas),
        "profiles": profile_metas,
        "extracted_at": datetime.now().isoformat(timespec="seconds"),
        "db_saved_live": True,
    }


def _notify_vinted_profile_report(rows: list[dict], meta: dict, config: dict) -> dict[str, object]:
    if not bool(config.get("discord_profile_report", False)):
        return {
            "discord_profile_report": False,
            "discord_profile_report_sent": False,
            "discord_profile_report_error": "",
        }
    webhook_url = str(config.get("discord_webhook_url", "") or "").strip()
    if not webhook_url:
        return {
            "discord_profile_report": False,
            "discord_profile_report_sent": False,
            "discord_profile_report_error": "webhook assente",
        }
    result = send_discord_webhook_message(
        webhook_url,
        build_vinted_profile_report_discord_message(meta, rows),
        embeds=_build_vinted_profile_report_embeds(rows),
    )
    if bool(result.get("ok")):
        return {
            "discord_profile_report": True,
            "discord_profile_report_sent": True,
            "discord_profile_report_error": "",
            "discord_profile_report_sent_at": str(result.get("sent_at", "") or ""),
        }
    error = str(result.get("error", "") or "")
    print(f"[vinted-profile][discord] invio report fallito: {error}", flush=True)
    return {
        "discord_profile_report": True,
        "discord_profile_report_sent": False,
        "discord_profile_report_error": error,
    }


def _build_vinted_profile_report_embeds(rows: list[dict]) -> list[dict[str, object]]:
    embeds: list[dict[str, object]] = []
    for row in rows:
        if str(row.get("profile_item_status", "") or "") not in {"new", "gone"}:
            continue
        image_url = normalize_vinted_image_url(str(row.get("image_url", "") or ""))
        if not image_url:
            continue
        title = normalize_whitespace(str(row.get("name", "") or "Vinted"))
        link = str(row.get("link", "") or "").strip()
        embed: dict[str, object] = {
            "title": title[:256] if title else "Vinted",
            "image": {"url": image_url},
        }
        if link:
            embed["url"] = link
        embeds.append(embed)
        if len(embeds) >= 4:
            break
    return embeds


def _scroll_and_read_vinted_profile_items(
    driver: Driver,
    *,
    max_items: int,
    page_settle_seconds: float,
) -> list[dict]:
    rows_by_link: dict[str, dict] = {}
    stable_rounds = 0
    stable_stop_rounds = 8 if max_items <= 0 else 5
    max_rounds = 220 if max_items <= 0 else max(20, min(220, (max_items // 8) + 20))
    last_height = 0
    _log_vinted_profile("scroll-start", max_items=max_items, max_rounds=max_rounds)
    for _round in range(max_rounds):
        for row in _read_vinted_profile_items(driver):
            link = str(row.get("link", "") or "").strip()
            if not link:
                continue
            rows_by_link[link] = row
        if max_items > 0 and len(rows_by_link) >= max_items:
            _log_vinted_profile("scroll-stop-max-items", round=_round + 1, rows=len(rows_by_link), max_items=max_items)
            break
        before_count = len(rows_by_link)
        scroll_state = _scroll_vinted_profile(driver)
        moved = bool(scroll_state.get("moved"))
        at_bottom = bool(scroll_state.get("at_bottom"))
        page_height = int(scroll_state.get("height") or 0)
        height_changed = bool(page_height and page_height != last_height)
        if page_height:
            last_height = page_height
        time.sleep(min(max(float(page_settle_seconds or 0), 0.8), 3.0))
        for row in _read_vinted_profile_items(driver):
            link = str(row.get("link", "") or "").strip()
            if not link:
                continue
            rows_by_link[link] = row
        if len(rows_by_link) <= before_count and not moved and at_bottom and not height_changed:
            stable_rounds += 1
        elif len(rows_by_link) <= before_count and at_bottom and not height_changed:
            stable_rounds += 1
        else:
            stable_rounds = 0
        added_count = max(len(rows_by_link) - before_count, 0)
        if added_count or stable_rounds >= 2 or (_round + 1) % 5 == 0:
            _log_vinted_profile(
                "scroll-round",
                round=_round + 1,
                rows=len(rows_by_link),
                added=added_count,
                moved=moved,
                at_bottom=at_bottom,
                height=page_height,
                height_changed=height_changed,
                stable_rounds=stable_rounds,
            )
        if stable_rounds >= stable_stop_rounds:
            _log_vinted_profile("scroll-stop-stable", round=_round + 1, rows=len(rows_by_link))
            break
    for verify_round in range(1, 4):
        scroll_state = _scroll_vinted_profile(driver)
        time.sleep(min(max(float(page_settle_seconds or 0), 0.8), 2.0))
        before_count = len(rows_by_link)
        for row in _read_vinted_profile_items(driver):
            link = str(row.get("link", "") or "").strip()
            if not link:
                continue
            rows_by_link[link] = row
        added_count = max(len(rows_by_link) - before_count, 0)
        _log_vinted_profile(
            "scroll-verify",
            round=verify_round,
            rows=len(rows_by_link),
            added=added_count,
            at_bottom=bool(scroll_state.get("at_bottom")),
            height=int(scroll_state.get("height") or 0),
        )
        if max_items > 0 and len(rows_by_link) >= max_items:
            break
    rows = list(rows_by_link.values())
    if max_items > 0:
        rows = rows[:max_items]
    _log_vinted_profile("scroll-finished", rows=len(rows))
    return rows


def _read_vinted_profile_items(driver: Driver) -> list[dict]:
    payload = driver.run_js(
        """
const clean = (value) => (value || '').replace(/\\s+/g, ' ').trim();
const links = [...document.querySelectorAll('a[href*="/items/"]')];
return links.map((link) => {
  const root = link.closest('[data-testid^="grid-item"], article')
    || link.parentElement?.parentElement?.parentElement
    || link.parentElement
    || link;
  const title = root.querySelector('[data-testid*="description-title"], [data-testid*="item-title"]');
  const price = root.querySelector('[data-testid*="price-text"], [data-testid*="item-price"]');
  const image = root.querySelector('img[alt]');
  const imageSrc = image ? (
    image.currentSrc ||
    image.src ||
    image.getAttribute('src') ||
    image.getAttribute('data-src') ||
    ((image.getAttribute('srcset') || '').split(',')[0] || '').trim().split(/\\s+/)[0] ||
    ''
  ) : '';
  return {
    link: link.href || link.getAttribute('href') || '',
    title: clean(title ? (title.innerText || title.textContent) : ''),
    price: clean(price ? (price.innerText || price.textContent) : ''),
    image_url: imageSrc,
    image_alt: clean(image ? image.getAttribute('alt') : ''),
    raw_text: clean(root.innerText || root.textContent),
  };
});
        """
    )
    if not isinstance(payload, list):
        return []
    rows: list[dict] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        link = normalize_vinted_item_url(str(item.get("link", "") or ""))
        if not link:
            continue
        item_id = extract_vinted_item_id_from_link(link)
        title = normalize_whitespace(str(item.get("title", "") or ""))
        if not title:
            title = normalize_whitespace(str(item.get("image_alt", "") or ""))
        rows.append(
            {
                "source": "vinted_profile",
                "profile_url": "",
                "member_id": "",
                "item_id": item_id,
                "name": title or link,
                "price": normalize_whitespace(str(item.get("price", "") or "")),
                "link": link,
                "image_url": normalize_vinted_image_url(str(item.get("image_url", "") or "")),
                "raw_text": normalize_whitespace(str(item.get("raw_text", "") or "")),
                "profile_item_status": "current",
                "extracted_at": datetime.now().isoformat(timespec="seconds"),
            }
        )
    return rows


def _scroll_vinted_profile(driver: Driver) -> dict[str, object]:
    try:
        payload = driver.run_js(
            """
const root = document.scrollingElement || document.documentElement || document.body;
const before = window.scrollY || root.scrollTop || 0;
const viewport = window.innerHeight || root.clientHeight || 0;
const heightBefore = Math.max(
  root.scrollHeight || 0,
  document.body ? document.body.scrollHeight || 0 : 0,
  document.documentElement ? document.documentElement.scrollHeight || 0 : 0
);
const target = Math.max(0, heightBefore - viewport);
window.scrollTo({ top: target, behavior: 'instant' });
root.scrollTop = target;
window.dispatchEvent(new Event('scroll'));
document.dispatchEvent(new Event('scroll'));
const after = window.scrollY || root.scrollTop || 0;
const heightAfter = Math.max(
  root.scrollHeight || 0,
  document.body ? document.body.scrollHeight || 0 : 0,
  document.documentElement ? document.documentElement.scrollHeight || 0 : 0
);
const maxScroll = Math.max(0, heightAfter - viewport);
const atBottom = after >= maxScroll - 8;
if (atBottom && maxScroll > 0) {
  window.scrollBy(0, -Math.min(240, viewport * 0.25));
  window.scrollTo({ top: maxScroll, behavior: 'instant' });
  root.scrollTop = maxScroll;
}
return {
  before,
  after: window.scrollY || root.scrollTop || 0,
  height: heightAfter,
  viewport,
  max_scroll: maxScroll,
  moved: Math.abs(after - before) > 2,
  at_bottom: atBottom,
};
            """
        )
        return payload if isinstance(payload, dict) else {"moved": False, "at_bottom": False, "height": 0}
    except Exception:
        return {"moved": False, "at_bottom": False, "height": 0}


def normalize_vinted_profile_url(profile_url: object) -> str:
    value = str(profile_url or "").strip()
    if not value:
        return ""
    if value.isdigit():
        value = f"{VINTED_BASE_URL}/member/{value}"
    parsed = urlsplit(value)
    if not parsed.netloc:
        parsed = urlsplit(f"{VINTED_BASE_URL}/{value.lstrip('/')}")
    if "/member/" not in parsed.path:
        return ""
    return urlunsplit((parsed.scheme or "https", parsed.netloc or urlsplit(VINTED_BASE_URL).netloc, parsed.path, "", ""))


def normalize_vinted_profile_urls(profile_urls: object) -> list[str]:
    def split_profile_url_text(value: object) -> list[str]:
        raw_value = str(value or "")
        raw_value = raw_value.replace("\\n", "\n").replace("\\r", "\n")
        raw_value = re.sub(r"/n(?=https?://|www\.vinted\.|vinted\.|member/|\d)", "\n", raw_value, flags=re.IGNORECASE)
        raw_value = re.sub(r"(?<!^)(?=https?://(?:www\.)?vinted\.)", "\n", raw_value, flags=re.IGNORECASE)
        return [
            part.strip()
            for chunk in raw_value.replace("\r", "\n").replace(";", "\n").split("\n")
            for part in chunk.split(",")
            if part.strip()
        ]

    if isinstance(profile_urls, (list, tuple, set)):
        raw_items = [
            part
            for item in profile_urls
            for part in split_profile_url_text(item)
        ]
    else:
        raw_value = str(profile_urls or "")
        try:
            parsed_json = json.loads(raw_value) if raw_value.strip().startswith("[") else None
        except json.JSONDecodeError:
            parsed_json = None
        if isinstance(parsed_json, list):
            raw_items = [
                part
                for item in parsed_json
                for part in split_profile_url_text(item)
            ]
        else:
            raw_items = split_profile_url_text(raw_value)
    normalized: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        profile_url = normalize_vinted_profile_url(item)
        if not profile_url or profile_url in seen:
            continue
        seen.add(profile_url)
        normalized.append(profile_url)
    return normalized


def _prioritize_profile_rows(rows: list[dict]) -> list[dict]:
    rank = {"new": 0, "gone": 1, "still_active": 2, "current": 3}
    return sorted(
        rows,
        key=lambda row: (
            rank.get(str(row.get("profile_item_status", "") or ""), 9),
            str(row.get("name", "") or "").lower(),
            str(row.get("link", "") or ""),
        ),
    )


def _nonnegative_float(value: object, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return max(float(default), 0.0)
    return max(parsed, 0.0)
