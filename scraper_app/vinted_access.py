import json
from datetime import datetime

from botasaurus.browser import Driver

from .browser_helpers import current_page_url


VINTED_ACCESS_MARKER_ALT = "bonaccarla"
VINTED_ACCESS_MARKER_SELECTOR = 'img.web_ui__Image__content[alt="bonaccarla"]'


def read_vinted_access_status(driver: Driver) -> dict[str, object]:
    payload = driver.run_js(
        f"""
const marker = document.querySelector({json.dumps(VINTED_ACCESS_MARKER_SELECTOR)});
return {{
  marker_present: !!marker,
  marker_alt: marker ? (marker.getAttribute('alt') || '') : '',
  marker_src: marker ? (marker.getAttribute('src') || '') : '',
  page_title: document.title || '',
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
        "expected_alt": VINTED_ACCESS_MARKER_ALT,
        "selector": VINTED_ACCESS_MARKER_SELECTOR,
        "current_url": str(current_page_url(driver) or ""),
        "checked_at": datetime.now().isoformat(timespec="seconds"),
    }


def emit_vinted_access_signal(status: dict[str, object]) -> None:
    print(f"__VINTED_ACCESS__:{json.dumps(status, ensure_ascii=False)}", flush=True)

