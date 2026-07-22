import json
import time
from datetime import datetime

from botasaurus.browser import Driver

from .browser_helpers import current_page_url


VINTED_ACCESS_MARKER_ALT = "bonaccarla"
VINTED_ACCESS_MARKER_SELECTOR = 'img.web_ui__Image__content[alt="bonaccarla"]'
VINTED_PAGE_NOT_FOUND_TEXTS = (
    "page not found",
    "pagina non trovata",
)


def read_vinted_access_status(driver: Driver) -> dict[str, object]:
    payload = driver.run_js(
        f"""
const marker = document.querySelector({json.dumps(VINTED_ACCESS_MARKER_SELECTOR)});
const pageTitle = String(document.title || '');
const bodyText = String(document.body ? (document.body.innerText || document.body.textContent || '') : '');
const combinedText = `${{pageTitle}}\n${{bodyText}}`.toLowerCase();
const pageNotFound = {json.dumps(list(VINTED_PAGE_NOT_FOUND_TEXTS))}.some((needle) => combinedText.includes(String(needle).toLowerCase()));
return {{
  marker_present: !pageNotFound && !!marker,
  marker_alt: marker ? (marker.getAttribute('alt') || '') : '',
  marker_src: marker ? (marker.getAttribute('src') || '') : '',
  page_title: pageTitle,
  page_not_found: !!pageNotFound,
}};
        """
    )
    if not isinstance(payload, dict):
        payload = {}
    return {
        "marker_present": bool(payload.get("marker_present")),
        "marker_alt": str(payload.get("marker_alt", "") or ""),
        "marker_src": str(payload.get("marker_src", "") or ""),
        "page_title": str(payload.get("page_title", "") or ""),
        "page_not_found": bool(payload.get("page_not_found")),
        "expected_alt": VINTED_ACCESS_MARKER_ALT,
        "selector": VINTED_ACCESS_MARKER_SELECTOR,
        "current_url": str(current_page_url(driver) or ""),
        "checked_at": datetime.now().isoformat(timespec="seconds"),
    }


def wait_for_vinted_access_status(
    driver: Driver,
    max_wait_seconds: float = 0.8,
    poll_interval_seconds: float = 0.2,
) -> dict[str, object]:
    status = read_vinted_access_status(driver)
    if bool(status.get("page_not_found")) or bool(status.get("marker_present")):
        return status

    max_wait = max(float(max_wait_seconds or 0), 0.0)
    if max_wait <= 0:
        return status

    poll_interval = max(float(poll_interval_seconds or 0), 0.05)
    deadline = time.monotonic() + max_wait
    while time.monotonic() < deadline:
        time.sleep(min(poll_interval, max(deadline - time.monotonic(), 0.05)))
        status = read_vinted_access_status(driver)
        if bool(status.get("page_not_found")) or bool(status.get("marker_present")):
            return status
    return status


def emit_vinted_access_signal(status: dict[str, object]) -> None:
    print(f"__VINTED_ACCESS__:{json.dumps(status, ensure_ascii=False)}", flush=True)
