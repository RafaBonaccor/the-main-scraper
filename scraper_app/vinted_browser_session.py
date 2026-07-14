import json
import os
import tempfile
from datetime import datetime
from pathlib import Path


VINTED_BROWSER_SESSION_FILE = Path(tempfile.gettempdir()) / "the_main_scraper_vinted_browser.json"


def get_active_vinted_browser_session() -> dict[str, object] | None:
    session_file = VINTED_BROWSER_SESSION_FILE
    if not session_file.exists():
        return None
    try:
        payload = json.loads(session_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        _clear_vinted_browser_session_file()
        return None
    pid = _safe_int(payload.get("pid"))
    if pid is None or not _process_is_alive(pid):
        _clear_vinted_browser_session_file()
        return None
    payload["pid"] = pid
    return payload


def register_vinted_browser_session(pid: int, url: str, source: str = "detached") -> None:
    payload = {
        "pid": int(pid),
        "url": str(url or "").strip(),
        "source": str(source or "detached"),
        "registered_at": datetime.now().isoformat(timespec="seconds"),
    }
    VINTED_BROWSER_SESSION_FILE.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def clear_vinted_browser_session(pid: int | None = None) -> None:
    session = get_active_vinted_browser_session()
    if session is None:
        _clear_vinted_browser_session_file()
        return
    if pid is not None and int(session.get("pid", -1)) != int(pid):
        return
    _clear_vinted_browser_session_file()


def _clear_vinted_browser_session_file() -> None:
    try:
        VINTED_BROWSER_SESSION_FILE.unlink(missing_ok=True)
    except OSError:
        pass


def _process_is_alive(pid: int) -> bool:
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _safe_int(value: object) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None

