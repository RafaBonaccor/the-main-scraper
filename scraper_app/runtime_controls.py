from __future__ import annotations

import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
RUNTIME_CONTROL_PATH = PROJECT_ROOT / "output" / "_runtime_control.json"


def clear_runtime_control_requests() -> None:
    if RUNTIME_CONTROL_PATH.exists():
        RUNTIME_CONTROL_PATH.unlink()


def request_skip_current_item() -> None:
    _update_control(skip_current=True)


def request_stop_after_current_item() -> None:
    _update_control(stop_after_current=True)


def request_vinted_login_confirmed() -> None:
    _update_control(vinted_login_confirmed=True)


def consume_skip_current_item_request() -> bool:
    state = _read_control()
    if not state.get("skip_current", False):
        return False
    state["skip_current"] = False
    _write_control(state)
    return True


def consume_stop_after_current_item_request() -> bool:
    state = _read_control()
    if not state.get("stop_after_current", False):
        return False
    state["stop_after_current"] = False
    _write_control(state)
    return True


def consume_vinted_login_confirmed_request() -> bool:
    state = _read_control()
    if not state.get("vinted_login_confirmed", False):
        return False
    state["vinted_login_confirmed"] = False
    _write_control(state)
    return True


def _update_control(**values: bool) -> None:
    state = _read_control()
    state.update({key: bool(value) for key, value in values.items()})
    _write_control(state)


def _read_control() -> dict[str, bool]:
    if not RUNTIME_CONTROL_PATH.exists():
        return {}
    try:
        payload = json.loads(RUNTIME_CONTROL_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    return {str(key): bool(value) for key, value in payload.items()}


def _write_control(state: dict[str, bool]) -> None:
    normalized = {key: bool(value) for key, value in state.items() if bool(value)}
    if not normalized:
        clear_runtime_control_requests()
        return
    RUNTIME_CONTROL_PATH.parent.mkdir(parents=True, exist_ok=True)
    RUNTIME_CONTROL_PATH.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")
