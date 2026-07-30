from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime
from pathlib import Path

from botasaurus.browser import Driver, browser

from .browser_runtime import resolve_browser_arguments, resolve_browser_profile
from .sources.vinted_upload import _normalize_upload_item, _upload_single_vinted_item


WORKER_ROOT = Path(tempfile.gettempdir()) / "the_main_scraper_vinted_upload_worker"
WORKER_JOBS_DIR = WORKER_ROOT / "jobs"
WORKER_PROCESSING_DIR = WORKER_ROOT / "processing"
WORKER_RESULTS_DIR = WORKER_ROOT / "results"
WORKER_SESSION_FILE = WORKER_ROOT / "session.json"
WORKER_LOG_FILE = WORKER_ROOT / "worker.log"
MAIN_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "main.py"
WORKER_VERSION_FILES = (
    Path(__file__).resolve(),
    Path(__file__).resolve().parent / "sources" / "vinted_upload.py",
    Path(__file__).resolve().parent / "contact_runner.py",
    MAIN_SCRIPT_PATH,
)


def enqueue_vinted_upload_job(
    *,
    items: list[dict],
    submit: bool = False,
    delay_between_seconds: int = 2,
    keep_browser_open: bool = True,
    keep_open_seconds: int = 0,
    slow_mode: bool = False,
    action_delay_seconds: float = 1.5,
    page_settle_seconds: float = 3.0,
    browser_mode: str = "chrome_normale",
    browser_user_data_dir: str = "",
    browser_profile_directory: str = "Default",
    idle_timeout_seconds: int = 900,
) -> dict[str, object]:
    normalized_items = [_normalize_upload_item(item) for item in list(items or []) if isinstance(item, dict)]
    if not normalized_items:
        raise ValueError("Vinted upload worker requires at least one valid item.")

    _ensure_worker_dirs()
    job_id = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    job_payload = {
        "job_id": job_id,
        "created_at": _now_iso(),
        "items": normalized_items,
        "submit": bool(submit),
        "delay_between_seconds": max(int(delay_between_seconds or 0), 0),
        "keep_browser_open": bool(keep_browser_open),
        "keep_open_seconds": max(int(keep_open_seconds or 0), 0),
        "slow_mode": bool(slow_mode),
        "action_delay_seconds": float(action_delay_seconds),
        "page_settle_seconds": float(page_settle_seconds),
        "browser_mode": str(browser_mode or "chrome_normale"),
        "browser_user_data_dir": str(browser_user_data_dir or ""),
        "browser_profile_directory": str(browser_profile_directory or "Default"),
    }
    job_path = WORKER_JOBS_DIR / f"{job_id}.json"
    job_path.write_text(json.dumps(job_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"[vinted-upload-worker] queued job_id={job_id} items={len(normalized_items)} "
        f"submit={bool(submit)} browser_mode={job_payload['browser_mode']}",
        flush=True,
    )

    active_worker = get_active_vinted_upload_worker()
    worker_started = False
    if active_worker is not None and not _worker_session_matches_code(active_worker):
        print(
            f"[vinted-upload-worker] stale worker detected pid={active_worker.get('pid')} "
            f"session_code_version={active_worker.get('code_version')} current_code_version={_current_worker_code_version()}",
            flush=True,
        )
        _stop_existing_worker(active_worker)
        clear_vinted_upload_worker_session()
        active_worker = None
    if active_worker is None:
        worker_started = _start_vinted_upload_worker(job_payload, idle_timeout_seconds=max(int(idle_timeout_seconds or 0), 120))
        active_worker = get_active_vinted_upload_worker()
    if active_worker is None:
        return {
            "ok": False,
            "queued": True,
            "message": "Vinted upload job queued, but the persistent browser worker did not start.",
            "job_id": job_id,
            "items_count": len(normalized_items),
            "submit": bool(submit),
            "keep_browser_open": True,
            "worker_started": False,
            "worker_pid": None,
            "worker_log_file": str(WORKER_LOG_FILE),
        }

    return {
        "ok": True,
        "queued": True,
        "message": "Vinted upload queued on persistent browser worker.",
        "job_id": job_id,
        "items_count": len(normalized_items),
        "submit": bool(submit),
        "keep_browser_open": True,
        "worker_started": worker_started,
        "worker_pid": int(active_worker.get("pid", 0)) if isinstance(active_worker, dict) else None,
        "worker_log_file": str(WORKER_LOG_FILE),
    }


def wait_for_vinted_upload_job_result(
    job_id: str,
    *,
    timeout_seconds: int = 1800,
    poll_seconds: float = 1.0,
) -> dict[str, object]:
    normalized_job_id = str(job_id or "").strip()
    if not normalized_job_id:
        raise ValueError("Missing Vinted upload worker job id.")
    result_path = WORKER_RESULTS_DIR / f"{normalized_job_id}.json"
    deadline = time.time() + max(int(timeout_seconds or 0), 30)
    print(
        f"[vinted-upload-worker] waiting result job_id={normalized_job_id} "
        f"timeout_seconds={max(int(timeout_seconds or 0), 30)} result_path={result_path}",
        flush=True,
    )
    while time.time() < deadline:
        if result_path.exists():
            try:
                payload = json.loads(result_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise RuntimeError(f"Invalid Vinted upload worker result for job {normalized_job_id}: {exc}") from exc
            if not isinstance(payload, dict):
                raise RuntimeError(f"Invalid Vinted upload worker result payload for job {normalized_job_id}.")
            print(
                f"[vinted-upload-worker] result ready job_id={normalized_job_id} "
                f"ok={bool(payload.get('ok'))} failed_count={payload.get('failed_count')}",
                flush=True,
            )
            return payload
        active_worker = get_active_vinted_upload_worker()
        if active_worker is None:
            raise RuntimeError(f"Vinted upload worker stopped before finishing job {normalized_job_id}.")
        time.sleep(max(float(poll_seconds or 0), 0.25))
    raise TimeoutError(f"Timed out waiting for Vinted upload worker job {normalized_job_id}.")


def get_active_vinted_upload_worker() -> dict[str, object] | None:
    if not WORKER_SESSION_FILE.exists():
        return None
    try:
        payload = json.loads(WORKER_SESSION_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        clear_vinted_upload_worker_session()
        return None
    pid = _safe_int(payload.get("pid"))
    if pid is None or not _process_is_alive(pid):
        clear_vinted_upload_worker_session()
        return None
    payload["pid"] = pid
    return payload


def clear_vinted_upload_worker_session() -> None:
    try:
        WORKER_SESSION_FILE.unlink(missing_ok=True)
    except OSError:
        pass


def run_vinted_upload_worker_loop(
    *,
    browser_mode: str,
    browser_user_data_dir: str,
    browser_profile_directory: str,
    slow_mode: bool,
    action_delay_seconds: float,
    page_settle_seconds: float,
    idle_timeout_seconds: int,
) -> dict[str, object]:
    config = {
        "browser_mode": str(browser_mode or "chrome_normale"),
        "browser_user_data_dir": str(browser_user_data_dir or ""),
        "browser_profile_directory": str(browser_profile_directory or "Default"),
        "slow_mode": bool(slow_mode),
        "action_delay_seconds": float(action_delay_seconds),
        "page_settle_seconds": float(page_settle_seconds),
        "idle_timeout_seconds": max(int(idle_timeout_seconds or 0), 120),
    }
    return _run_vinted_upload_worker_task(config)


@browser(
    profile=resolve_browser_profile,
    add_arguments=resolve_browser_arguments,
    wait_for_complete_page_load=False,
)
def _run_vinted_upload_worker_task(driver: Driver, config: dict) -> dict[str, object]:
    _ensure_worker_dirs()
    _register_vinted_upload_worker_session(os.getpid(), config)
    print(
        f"[vinted-upload-worker] started pid={os.getpid()} browser_mode={config.get('browser_mode')} "
        f"profile={config.get('browser_profile_directory')} user_data_dir={config.get('browser_user_data_dir')}",
        flush=True,
    )
    processed_jobs = 0
    processed_items = 0
    last_activity_at = time.time()
    idle_timeout_seconds = max(int(config.get("idle_timeout_seconds", 900) or 0), 120)
    try:
        while True:
            job = _claim_next_job()
            if job is None:
                if (time.time() - last_activity_at) >= idle_timeout_seconds:
                    print(
                        f"[vinted-upload-worker] idle-timeout pid={os.getpid()} "
                        f"processed_jobs={processed_jobs} processed_items={processed_items}",
                        flush=True,
                    )
                    return {
                        "ok": True,
                        "idle_stopped": True,
                        "processed_jobs": processed_jobs,
                        "processed_items": processed_items,
                        "message": "Vinted upload worker stopped after idle timeout.",
                    }
                driver.sleep(1)
                continue

            last_activity_at = time.time()
            print(
                f"[vinted-upload-worker] claimed job_id={job.get('job_id')} items={len(list(job.get('items', []) or []))}",
                flush=True,
            )
            if not _driver_session_is_alive(driver):
                print(
                    f"[vinted-upload-worker] driver-dead-before-job job_id={job.get('job_id')}",
                    flush=True,
                )
                result = {
                    "ok": False,
                    "queued": False,
                    "job_id": str(job.get("job_id", "") or ""),
                    "created_at": str(job.get("created_at", "") or ""),
                    "completed_at": _now_iso(),
                    "items_count": len(list(job.get("items", []) or [])),
                    "prepared_count": 0,
                    "submitted_count": 0,
                    "saved_draft_count": 0,
                    "failed_count": len(list(job.get("items", []) or [])),
                    "submit": bool(job.get("submit", False)),
                    "fatal_session_error": True,
                    "results": [
                        {
                            "ok": False,
                            "prepared": False,
                            "submitted": False,
                            "saved_draft": False,
                            "title": str(item.get("title", "") or ""),
                            "price": str(item.get("price", "") or ""),
                            "photos_count": len(list(item.get("photo_paths", []) or [])),
                            "error": "Persistent Vinted upload browser session is no longer available.",
                        }
                        for item in list(job.get("items", []) or [])
                        if isinstance(item, dict)
                    ],
                }
                _write_worker_result(str(job.get("job_id", "") or ""), result)
                return result
            result = _process_worker_job(driver, config, job)
            processed_jobs += 1
            processed_items += int(result.get("items_count", 0) or 0)
            _write_worker_result(str(job.get("job_id", "") or ""), result)
            print(
                f"[vinted-upload-worker] completed job_id={job.get('job_id')} "
                f"ok={bool(result.get('ok'))} failed_count={result.get('failed_count')} "
                f"fatal_session_error={bool(result.get('fatal_session_error'))}",
                flush=True,
            )
            if bool(result.get("fatal_session_error")):
                print(
                    f"[vinted-upload-worker] stopping after fatal session error job_id={job.get('job_id')}",
                    flush=True,
                )
                return result
    finally:
        print(f"[vinted-upload-worker] stopping pid={os.getpid()}", flush=True)
        clear_vinted_upload_worker_session()


def _process_worker_job(driver: Driver, worker_config: dict, job: dict) -> dict[str, object]:
    items = list(job.get("items", []) or [])
    delay_between_seconds = max(int(job.get("delay_between_seconds", 2) or 0), 0)
    if bool(job.get("slow_mode", worker_config.get("slow_mode", False))):
        delay_between_seconds = max(delay_between_seconds, 2)
    results: list[dict[str, object]] = []
    fatal_session_error = False
    for index, item in enumerate(items):
        item_config = {
            **worker_config,
            "item": item,
            "submit": bool(job.get("submit", False)),
            "slow_mode": bool(job.get("slow_mode", worker_config.get("slow_mode", False))),
            "action_delay_seconds": float(job.get("action_delay_seconds", worker_config.get("action_delay_seconds", 1.5))),
            "page_settle_seconds": float(job.get("page_settle_seconds", worker_config.get("page_settle_seconds", 3.0))),
            "preserve_active_tab": True,
        }
        try:
            result = _upload_single_vinted_item(driver, item_config)
        except Exception as exc:
            error_message = f"{type(exc).__name__}: {exc}"
            lower_error = error_message.lower()
            fatal_session_error = "target tab or iframe is no longer available" in lower_error
            print(
                f"[vinted-upload-worker] item-error job_id={job.get('job_id')} index={index} "
                f"title={str(item.get('title', '') or '')} fatal_session_error={fatal_session_error} error={error_message}",
                flush=True,
            )
            result = {
                "ok": False,
                "prepared": False,
                "submitted": False,
                "saved_draft": False,
                "title": str(item.get("title", "") or ""),
                "price": str(item.get("price", "") or ""),
                "photos_count": len(list(item.get("photo_paths", []) or [])),
                "error": error_message,
            }
        results.append(result)
        if fatal_session_error:
            break
        if index < len(items) - 1 and delay_between_seconds > 0:
            driver.sleep(delay_between_seconds)

    prepared_count = sum(1 for item in results if item.get("prepared"))
    submitted_count = sum(1 for item in results if item.get("submitted"))
    saved_draft_count = sum(1 for item in results if item.get("saved_draft"))
    failed_count = sum(1 for item in results if not item.get("ok"))
    all_succeeded = failed_count == 0 and len(results) == len(items) and len(items) > 0
    expected_success_count = submitted_count if bool(job.get("submit", False)) else saved_draft_count
    return {
        "ok": all_succeeded and expected_success_count == len(items),
        "queued": False,
        "job_id": str(job.get("job_id", "") or ""),
        "created_at": str(job.get("created_at", "") or ""),
        "completed_at": _now_iso(),
        "items_count": len(items),
        "prepared_count": prepared_count,
        "submitted_count": submitted_count,
        "saved_draft_count": saved_draft_count,
        "failed_count": failed_count,
        "submit": bool(job.get("submit", False)),
        "fatal_session_error": fatal_session_error,
        "results": results,
    }


def _claim_next_job() -> dict[str, object] | None:
    _ensure_worker_dirs()
    job_paths = sorted(WORKER_JOBS_DIR.glob("*.json"))
    for job_path in job_paths:
        processing_path = WORKER_PROCESSING_DIR / job_path.name
        try:
            os.replace(job_path, processing_path)
        except OSError:
            continue
        try:
            payload = json.loads(processing_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = None
        finally:
            try:
                processing_path.unlink(missing_ok=True)
            except OSError:
                pass
        if isinstance(payload, dict):
            return payload
    return None


def _write_worker_result(job_id: str, payload: dict[str, object]) -> None:
    if not job_id:
        return
    _ensure_worker_dirs()
    result_path = WORKER_RESULTS_DIR / f"{job_id}.json"
    result_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"[vinted-upload-worker] wrote result job_id={job_id} path={result_path} ok={bool(payload.get('ok'))}",
        flush=True,
    )


def _start_vinted_upload_worker(config: dict[str, object], idle_timeout_seconds: int) -> bool:
    _ensure_worker_dirs()
    with WORKER_LOG_FILE.open("a", encoding="utf-8") as log_handle:
        command = [
            sys.executable,
            str(MAIN_SCRIPT_PATH),
            "vinted-upload-worker",
            "--browser-mode",
            str(config.get("browser_mode", "chrome_normale") or "chrome_normale"),
            "--browser-user-data-dir",
            str(config.get("browser_user_data_dir", "") or ""),
            "--browser-profile-directory",
            str(config.get("browser_profile_directory", "Default") or "Default"),
            "--idle-timeout-seconds",
            str(max(int(idle_timeout_seconds or 0), 120)),
        ]
        if bool(config.get("slow_mode", False)):
            command.append("--slow-mode")
        command.extend(
            [
                "--action-delay-seconds",
                str(float(config.get("action_delay_seconds", 1.5) or 1.5)),
                "--page-settle-seconds",
                str(float(config.get("page_settle_seconds", 3.0) or 3.0)),
            ]
        )
        subprocess.Popen(
            command,
            cwd=str(MAIN_SCRIPT_PATH.parent),
            stdout=log_handle,
            stderr=log_handle,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    print(
        f"[vinted-upload-worker] spawning worker command={' '.join(command)} log_file={WORKER_LOG_FILE}",
        flush=True,
    )

    deadline = time.time() + 8.0
    while time.time() < deadline:
        active = get_active_vinted_upload_worker()
        if active is not None:
            return True
        time.sleep(0.25)
    return False


def _register_vinted_upload_worker_session(pid: int, config: dict[str, object]) -> None:
    _ensure_worker_dirs()
    payload = {
        "pid": int(pid),
        "started_at": _now_iso(),
        "code_version": _current_worker_code_version(),
        "browser_mode": str(config.get("browser_mode", "chrome_normale") or "chrome_normale"),
        "browser_user_data_dir": str(config.get("browser_user_data_dir", "") or ""),
        "browser_profile_directory": str(config.get("browser_profile_directory", "Default") or "Default"),
        "log_file": str(WORKER_LOG_FILE),
    }
    WORKER_SESSION_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"[vinted-upload-worker] session registered pid={pid} session_file={WORKER_SESSION_FILE}",
        flush=True,
    )


def _ensure_worker_dirs() -> None:
    WORKER_JOBS_DIR.mkdir(parents=True, exist_ok=True)
    WORKER_PROCESSING_DIR.mkdir(parents=True, exist_ok=True)
    WORKER_RESULTS_DIR.mkdir(parents=True, exist_ok=True)


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


def _current_worker_code_version() -> str:
    mtimes: list[str] = []
    for path in WORKER_VERSION_FILES:
        try:
            mtimes.append(f"{path.name}:{int(path.stat().st_mtime_ns)}")
        except OSError:
            mtimes.append(f"{path.name}:missing")
    return "|".join(mtimes)


def _worker_session_matches_code(session: dict[str, object]) -> bool:
    return str(session.get("code_version", "") or "") == _current_worker_code_version()


def _stop_existing_worker(session: dict[str, object]) -> None:
    pid = _safe_int(session.get("pid"))
    if pid is None:
        return
    try:
        os.kill(pid, signal.SIGTERM)
        print(f"[vinted-upload-worker] sent SIGTERM to stale worker pid={pid}", flush=True)
    except OSError as exc:
        print(f"[vinted-upload-worker] failed to stop stale worker pid={pid} error={exc}", flush=True)


def _driver_session_is_alive(driver: Driver) -> bool:
    try:
        payload = driver.run_js(
            """
return {
  href: String(window.location.href || ''),
  title: String(document.title || ''),
};
            """
        )
    except Exception as exc:
        print(f"[vinted-upload-worker] driver healthcheck failed error={type(exc).__name__}: {exc}", flush=True)
        return False
    print(
        f"[vinted-upload-worker] driver healthcheck ok href={str((payload or {}).get('href', ''))}",
        flush=True,
    )
    return isinstance(payload, dict)


def _safe_int(value: object) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")
