import time

from botasaurus.browser import Driver, Wait, browser

from .browser_helpers import DEFAULT_COOKIE_REJECT_TEXTS, click_first_matching_text, current_page_url
from .browser_runtime import resolve_browser_arguments, resolve_browser_profile


DEFAULT_BROWSER_URL = "https://www.google.com/maps"


def open_browser_session(
    url: str = DEFAULT_BROWSER_URL,
    keep_open_seconds: int = 0,
    browser_mode: str = "isolated",
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
    }
