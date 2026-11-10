"""Single-shot LinkedIn text publisher using an isolated copy of standard Chrome.

This module must be invoked only after the user has explicitly confirmed a draft.
It never retries the final click, because a lost acknowledgement can mean success.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import sys
from threading import Event, Thread
import time

from botasaurus.browser import Driver, Wait, browser, cdp

from ..browser_runtime import resolve_browser_arguments, resolve_browser_profile


def _normal(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


_OPEN_COMPOSER_JS = r"""
const visible = el => !!(el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden');
const exact = /^(start\s+(?:a\s+)?post|create\s+(?:a\s+)?post|post\s+something|avvia\s+(?:un\s+)?post|crea\s+(?:un\s+)?post|inizia\s+(?:un\s+)?post|scrivi\s+un\s+post)$/i;
const primary = [...new Set(document.querySelectorAll('.share-box-feed-entry__trigger, [data-testid="share-box-feed-entry__trigger"], [data-control-name="share.post"]'))]
  .filter(el => visible(el) && !el.disabled);
const candidates = primary.length ? primary : [...document.querySelectorAll('button, [role="button"]')]
  .filter(el => visible(el) && !el.disabled && [el.innerText, el.getAttribute('aria-label'), el.getAttribute('title')].some(value => exact.test((value || '').trim())));
const unique = [...new Set(candidates)];
if (unique.length !== 1) return {ok:false, count:unique.length, labels:unique.slice(0,5).map(el => (el.innerText || el.getAttribute('aria-label') || el.getAttribute('title') || '').trim())};
unique[0].click();
return {ok:true, count:1};
"""

_FIND_EDITOR_JS = r"""
const visible = el => !!(el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden');
const selectors = ['[role="dialog"] .ql-editor[contenteditable="true"]',
  '[role="dialog"] [role="textbox"][contenteditable="true"]',
  '.share-creation-state__content .ql-editor[contenteditable="true"]',
  '[role="dialog"] [contenteditable="true"]',
  '.share-creation-state__content [contenteditable="true"]'];
for (const selector of selectors) {
  const found = [...document.querySelectorAll(selector)].filter(visible);
  if (found.length === 1) return selector;
}
const candidates = [...new Set(document.querySelectorAll('[contenteditable="true"]'))].filter(visible)
  .filter(el => el.getAttribute('role') === 'textbox' || el.classList.contains('ql-editor') ||
    el.closest('[role="dialog"], .share-creation-state__content') ||
    /post|share|condivid|scrivi/i.test([el.getAttribute('aria-label'), el.getAttribute('data-placeholder'), el.getAttribute('aria-placeholder')].join(' ')));
if (candidates.length === 1) {
  const token = 'agentlab-' + Math.random().toString(36).slice(2);
  candidates[0].setAttribute('data-agentlab-editor', token);
  return `[data-agentlab-editor="${token}"]`;
}
return '';
"""

_CLICK_PUBLISH_JS = r"""
const visible = el => !!(el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden');
let roots = [...document.querySelectorAll('[role="dialog"], .share-creation-state__content')].filter(visible);
const editor = document.querySelector('[data-agentlab-editor], [contenteditable="true"]');
if (!roots.length && editor) { let parent = editor; for (let i=0; i<6 && parent; i++, parent=parent.parentElement) {
  if (parent.querySelector('button,[role="button"]')) { roots = [parent]; break; }
} }
if (!roots.length) return {ok:false, reason:'composer_missing'};
const buttons = [...new Set(roots.flatMap(root => [...root.querySelectorAll('button')]))]
  .filter(el => visible(el) && !el.disabled && /^(post|pubblica)$/i.test((el.innerText || el.getAttribute('aria-label') || '').trim()));
if (buttons.length !== 1) return {ok:false, reason:'publish_button_ambiguous', count:buttons.length};
buttons[0].click();
return {ok:true};
"""

_OPEN_IMAGE_PICKER_JS = r"""
const visible = el => !!(el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden');
const actionables = [...new Set(document.querySelectorAll('button,[role="button"],label,[tabindex],[data-control-name*="media" i],[data-testid*="media" i],[aria-label*="media" i],[title*="media" i]'))];
const labelPattern = /^(photo|foto|image|immagine|media|add media|aggiungi media|add a photo|aggiungi una foto|add photo|aggiungi foto|upload photo|carica foto)$/i;
const matches = el => [el.innerText, el.getAttribute('aria-label'), el.getAttribute('title'), el.getAttribute('data-control-name'), el.getAttribute('data-testid')]
  .filter(Boolean).some(value => labelPattern.test(value.trim()));
const candidates = actionables.filter(el => visible(el) && !el.disabled && matches(el));
for (const textEl of [...document.querySelectorAll('span,div,p')].filter(el => visible(el) && (el.innerText || '').trim().toLowerCase() === 'media')) {
  const action = textEl.closest('button,[role="button"],label,[tabindex]') || textEl;
  if (visible(action) && !action.disabled) candidates.push(action);
}
const unique = [...new Set(candidates)];
if (unique.length !== 1) return {reason:'image_button_ambiguous', count:unique.length,
  labels:actionables.filter(visible).slice(0,20).map(el => (el.innerText || el.getAttribute('aria-label') || el.getAttribute('title') || el.getAttribute('data-control-name') || '').trim())};
const token = 'agentlab-media-' + Math.random().toString(36).slice(2);
unique[0].setAttribute('data-agentlab-media', token);
return ` [data-agentlab-media="${token}"]`.trim();
"""

_IMAGE_PREVIEW_JS = r"""
const roots = [...document.querySelectorAll('[role="dialog"], .share-creation-state__content')]
  .filter(el => el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden');
return roots.some(root => [...root.querySelectorAll('img')].some(img => img.getClientRects().length && img.naturalWidth > 64 && (img.currentSrc || img.src).startsWith('blob:')));
"""

_VERIFY_JS = r"""
const alerts = [...document.querySelectorAll('[role="alert"], [role="status"], .artdeco-toast-item, .share-creation-state__success')]
  .filter(el => el.getClientRects().length).map(el => (el.innerText || '').trim());
const success = alerts.find(text => /(?:post (?:was |has been )?(?:published|posted|shared)|post pubblicato|pubblicato con successo)/i.test(text));
const link = [...document.querySelectorAll('[role="alert"] a[href*="/feed/update/"], .artdeco-toast-item a[href*="/feed/update/"]')]
  .find(el => el.getClientRects().length)?.href || '';
return {success:!!success, link};
"""


def _wait_js(driver: Driver, script: str, predicate, seconds: float):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = driver.run_js(script)
        if predicate(value):
            return value
        time.sleep(0.5)
    return None


def _upload_via_file_chooser(driver: Driver, image_path: Path) -> tuple[bool, str]:
    """Click LinkedIn's Media control and satisfy its native chooser via CDP."""
    received = Event()
    completed = Event()
    outcome: dict[str, str | bool] = {"ok": False, "error": "No file chooser event received"}

    def on_file_chooser_opened(event) -> None:
        received.set()
        if event.backend_node_id is None:
            outcome["error"] = "Chooser event had no file input node"
            completed.set()
            return

        def set_file() -> None:
            try:
                driver._tab.send(cdp.dom.set_file_input_files(
                    files=[str(image_path)], backend_node_id=event.backend_node_id
                ))
                outcome["ok"] = True
                outcome["error"] = ""
            except Exception as exc:
                outcome["error"] = f"CDP file assignment failed ({type(exc).__name__})"
            finally:
                completed.set()

        # CDP callbacks run on the websocket reader thread. Sending a command
        # there would block that same reader while waiting for its response.
        Thread(target=set_file, daemon=True).start()

    handler = cdp.page.FileChooserOpened
    driver._tab.add_handler(handler, on_file_chooser_opened)
    try:
        driver._tab.send(cdp.dom.enable())
        driver._tab.send(cdp.page.set_intercept_file_chooser_dialog(True))
        media_selector = _wait_js(driver, _OPEN_IMAGE_PICKER_JS, bool, 15)
        if not media_selector:
            detail = driver.run_js(_OPEN_IMAGE_PICKER_JS)
            labels = detail.get("labels", []) if isinstance(detail, dict) else []
            return False, "LinkedIn Media control was not uniquely found" + (f"; visible controls: {labels}" if labels else "")
        # Use a trusted pointer event, not element.click(): LinkedIn opens the
        # native chooser only from the actual Media control interaction.
        media_button = driver.select(media_selector, wait=Wait.SHORT)
        media_button.mouse_click()
        if not received.wait(15):
            return False, "Media click did not open a native file chooser"
        if not completed.wait(15):
            return False, "Timed out while assigning the image to the chooser"
        return bool(outcome["ok"]), str(outcome["error"])
    except Exception as exc:
        return False, f"File chooser automation failed ({type(exc).__name__})"
    finally:
        try:
            driver._tab.send(cdp.page.set_intercept_file_chooser_dialog(False))
        except Exception:
            pass
        try:
            driver._tab.handlers[handler].remove(on_file_chooser_opened)
        except (KeyError, ValueError):
            pass


def _publish_with_driver(driver: Driver, config: dict) -> dict:
    attempted = False
    try:
        try:
            driver.get("https://www.linkedin.com/feed/", wait=Wait.SHORT, timeout=30)
        except TimeoutError:
            pass
        page = _wait_js(driver, "return location.href", lambda value: "linkedin.com/feed" in str(value), 20)
        if not page or any(part in str(page) for part in ("/login", "/checkpoint", "/authwall")):
            return {"state": "failed", "clicked": False, "error": "LinkedIn login missing in the copied Chrome profile"}
        opened = _wait_js(driver, _OPEN_COMPOSER_JS, lambda value: isinstance(value, dict) and value.get("ok"), 25)
        if not opened:
            return {"state": "failed", "clicked": False,
                    "error": "Could not find a unique 'Start a post' button; no publication was attempted"}
        attachment_path = str(config.get("attachment_path") or "").strip()
        if attachment_path:
            image_path = Path(attachment_path).resolve()
            archive_root = Path(__file__).resolve().parents[4] / "data" / "linkedin_media"
            if archive_root.resolve() not in image_path.parents or not image_path.is_file() or image_path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"}:
                return {"state": "failed", "clicked": False, "error": "Archived source image is unavailable or invalid; nothing published"}
            uploaded, upload_error = _upload_via_file_chooser(driver, image_path)
            if not uploaded:
                return {"state": "failed", "clicked": False,
                        "error": f"Direct image upload failed: {upload_error}; nothing published"}
            preview = _wait_js(driver, _IMAGE_PREVIEW_JS, bool, 30)
            if not preview:
                return {"state": "failed", "clicked": False, "error": "LinkedIn did not confirm the image preview; nothing published"}
        # LinkedIn hides/removes the Media control once a URL is typed. Attach
        # the image first, then enter and verify the reviewed text and links.
        selector = _wait_js(driver, _FIND_EDITOR_JS, bool, 20)
        if not selector:
            return {"state": "failed", "clicked": False, "error": "LinkedIn composer editor did not appear after image upload" if attachment_path else "LinkedIn composer editor did not appear"}
        driver.click(selector, wait=Wait.SHORT)
        driver.focus(selector, wait=Wait.SHORT)
        focused = _wait_js(
            driver,
            "return document.activeElement === document.querySelector(" + json.dumps(selector) + ")",
            bool,
            5,
        )
        if not focused:
            return {"state": "failed", "clicked": False, "error": "LinkedIn composer editor could not be focused; nothing published"}
        # CDP insertText preserves Unicode and multiline text; simulated key-per-character
        # typing can silently alter curly quotes or line breaks in a reviewed draft.
        driver._tab.send(cdp.input_.insert_text(config["body"]))
        actual = str(driver.run_js("return document.querySelector(" + json.dumps(selector) + ")?.innerText || ''"))
        if _normal(actual) != _normal(config["body"]):
            return {"state": "failed", "clicked": False,
                    "error": f"Editor text verification failed before publishing (expected {len(config['body'])} chars, read {len(actual)}; actual preview: {actual[:180]!r})"}
        attempted = True
        clicked = driver.run_js(_CLICK_PUBLISH_JS)
        if not isinstance(clicked, dict):
            return {"state": "unknown", "clicked": True,
                    "error": "No response after the publish attempt; check LinkedIn before retrying"}
        if not clicked.get("ok"):
            return {"state": "failed", "clicked": False, "error": "Publish button was missing or ambiguous; nothing published"}
        verified = _wait_js(driver, _VERIFY_JS, lambda value: isinstance(value, dict) and value.get("success"), 35)
        if verified:
            return {"state": "published", "clicked": True, "post_url": str(verified.get("link") or "")}
        return {"state": "unknown", "clicked": True,
                "error": "Click sent, but LinkedIn success was not confirmed. Check the profile before retrying"}
    except Exception as exc:
        return {"state": "unknown" if attempted else "failed", "clicked": attempted,
                "error": f"Browser error: {type(exc).__name__}; check LinkedIn before retrying" if attempted
                         else f"Browser error before publishing: {type(exc).__name__}"}


@browser(profile=resolve_browser_profile, add_arguments=resolve_browser_arguments,
         wait_for_complete_page_load=False, close_on_crash=True,
         raise_exception=True, create_error_logs=False)
def _publish_task(driver: Driver, config: dict) -> dict:
    return _publish_with_driver(driver, config)


def main() -> None:
    config = json.load(sys.stdin)
    body = str(config.get("body") or "").strip()
    if not body or len(body) > 3000:
        raise ValueError("Invalid draft length")
    result = _publish_task({**config, "body": body})
    print("LINKEDIN_PUBLISH_RESULT=" + json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
