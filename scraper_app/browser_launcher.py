import time
from urllib.parse import urlsplit

from botasaurus.browser import Driver, Wait, browser

from .browser_helpers import DEFAULT_COOKIE_REJECT_TEXTS, click_first_matching_text, current_page_url
from .chrome_reuse import preferred_host_fragment_for_url, try_reuse_running_chrome
from .browser_runtime import resolve_browser_arguments, resolve_browser_profile
from .vinted_access import emit_vinted_access_signal, read_vinted_access_status


DEFAULT_BROWSER_URL = "https://www.google.com/maps"


def open_browser_session(
    url: str = DEFAULT_BROWSER_URL,
    keep_open_seconds: int = 0,
    browser_mode: str = "chrome_normale",
    browser_user_data_dir: str = "",
    browser_profile_directory: str = "Default",
    refresh_browser_profile: bool = False,
) -> dict:
    config = {
        "url": str(url or DEFAULT_BROWSER_URL).strip() or DEFAULT_BROWSER_URL,
        "keep_open_seconds": max(int(keep_open_seconds), 0),
        "browser_mode": browser_mode,
        "browser_user_data_dir": browser_user_data_dir,
        "browser_profile_directory": browser_profile_directory,
        "refresh_browser_profile": bool(refresh_browser_profile),
    }
    preferred_host = preferred_host_fragment_for_url(config["url"])
    reused = try_reuse_running_chrome(config["url"], preferred_host_fragment=preferred_host)
    if reused.get("reused"):
        return {
            "ok": True,
            "requested_url": config["url"],
            "last_url": config["url"],
            "cookie_banner_action": "",
            "reused_running_chrome": True,
            "reused_running_chrome_action": str(reused.get("action", "") or ""),
            "reused_running_chrome_previous_url": str(reused.get("previous_url", "") or ""),
            "vinted_access_marker_present": False,
            "vinted_access_current_url": "",
            "vinted_access_checked_at": "",
        }
    return _open_browser_task(config)


@browser(
    profile=resolve_browser_profile,
    add_arguments=resolve_browser_arguments,
    wait_for_complete_page_load=False,
    output=None,
)
def _open_browser_task(driver: Driver, config: dict) -> dict:
    target_url = config["url"]
    try:
        driver.get(target_url, wait=Wait.LONG, timeout=30)
    except TypeError:
        driver.get(target_url)
    cookie_action = click_first_matching_text(driver, DEFAULT_COOKIE_REJECT_TEXTS)
    if cookie_action:
        time.sleep(1.5)
    access_status: dict[str, object] = {}
    current_host = urlsplit(str(target_url or "")).netloc.lower()
    if "vinted." in current_host:
        access_status = read_vinted_access_status(driver)
        emit_vinted_access_signal(access_status)
    current_url = current_page_url(driver)
    navigated = bool(current_url)
    print(f"Browser aperto: {current_url or target_url}", flush=True)
    print("Chiudi la finestra del browser o ferma il processo per terminare.", flush=True)

    keep_open_seconds = int(config.get("keep_open_seconds", 0) or 0)
    started_at = time.monotonic()
    missing_checks = 0
    while keep_open_seconds == 0 or (time.monotonic() - started_at) < keep_open_seconds:
        time.sleep(1)
        current_url = current_page_url(driver)
        if current_url:
            missing_checks = 0
            continue
        missing_checks += 1
        if missing_checks >= 3:
            break

    return {
        "ok": bool(navigated or current_url),
        "requested_url": target_url,
        "last_url": current_url,
        "cookie_banner_action": cookie_action or "",
        "vinted_access_marker_present": bool(access_status.get("marker_present")),
        "vinted_access_current_url": str(access_status.get("current_url", "") or ""),
        "vinted_access_checked_at": str(access_status.get("checked_at", "") or ""),
    }
