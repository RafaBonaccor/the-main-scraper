import subprocess
import sys
from urllib.parse import urlsplit


def try_reuse_running_chrome(url: str, preferred_host_fragment: str = "") -> dict[str, object]:
    target_url = str(url or "").strip()
    preferred_host = str(preferred_host_fragment or "").strip().lower()
    if not target_url:
        return {"reused": False, "reason": "missing_url"}
    if sys.platform != "darwin":
        return {"reused": False, "reason": "unsupported_platform"}
    if not _is_google_chrome_running():
        return {"reused": False, "reason": "chrome_not_running"}

    script = _build_reuse_chrome_script(target_url, preferred_host)
    completed = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        return {
            "reused": False,
            "reason": "osascript_failed",
            "stderr": str(completed.stderr or "").strip(),
        }

    output = str(completed.stdout or "").strip()
    if output.startswith("REUSED_MATCHING_TAB|"):
        return {
            "reused": True,
            "action": "reused_matching_tab",
            "previous_url": output.split("|", 1)[1].strip(),
            "target_url": target_url,
        }
    if output == "OPENED_NEW_TAB":
        return {
            "reused": True,
            "action": "opened_new_tab",
            "target_url": target_url,
        }
    if output == "OPENED_NEW_WINDOW":
        return {
            "reused": True,
            "action": "opened_new_window",
            "target_url": target_url,
        }
    return {
        "reused": False,
        "reason": "unexpected_output",
        "stdout": output,
    }


def preferred_host_fragment_for_url(url: str) -> str:
    host = urlsplit(str(url or "")).netloc.strip().lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def _is_google_chrome_running() -> bool:
    completed = subprocess.run(
        [
            "osascript",
            "-e",
            'tell application "System Events" to return exists process "Google Chrome"',
        ],
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        return False
    return str(completed.stdout or "").strip().lower() == "true"


def _build_reuse_chrome_script(target_url: str, preferred_host_fragment: str) -> str:
    target_value = _applescript_quote(target_url)
    host_value = _applescript_quote(preferred_host_fragment.lower())
    return f"""
set targetUrl to {target_value}
set preferredHost to {host_value}
tell application "Google Chrome"
    activate
    if (count of windows) is 0 then
        make new window
        set URL of active tab of front window to targetUrl
        return "OPENED_NEW_WINDOW"
    end if
    if preferredHost is not "" then
        repeat with w in windows
            set tabIndex to 0
            repeat with t in tabs of w
                set tabIndex to tabIndex + 1
                set tabUrl to URL of t
                if (tabUrl as text) contains preferredHost then
                    set active tab index of w to tabIndex
                    set index of w to 1
                    set URL of t to targetUrl
                    return "REUSED_MATCHING_TAB|" & (tabUrl as text)
                end if
            end repeat
        end repeat
    end if
    tell front window
        make new tab with properties {{URL:targetUrl}}
    end tell
    return "OPENED_NEW_TAB"
end tell
""".strip()


def _applescript_quote(value: str) -> str:
    return '"' + str(value or "").replace('"', '""') + '"'
