import os
import re
import shutil
import tempfile
from datetime import datetime
from pathlib import Path


DEFAULT_WINDOWS_CHROME_USER_DATA_DIR = Path.home() / "AppData" / "Local" / "Google" / "Chrome" / "User Data"
DEFAULT_MACOS_CHROME_USER_DATA_DIR = Path.home() / "Library" / "Application Support" / "Google" / "Chrome"
DEFAULT_LINUX_CHROME_USER_DATA_DIRS = (
    Path.home() / ".config" / "google-chrome",
    Path.home() / ".config" / "chromium",
)
WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_PROFILES_DIR = Path(tempfile.gettempdir()) / "the_main_scraper_profiles"
PERSISTENT_PROFILES_DIR = WORKSPACE_ROOT / ".bp"
PROFILE_CACHE_DIR_NAMES = {
    "Cache",
    "Code Cache",
    "GPUCache",
    "GrShaderCache",
    "ShaderCache",
    "DawnCache",
    "Crashpad",
    "BrowserMetrics",
    "OptimizationHints",
    "AutofillStates",
    "Extension Rules",
    "Extension Scripts",
    "Feature Engagement Tracker",
    "commerce_subscription_db",
    "Site Characteristics Database",
    "Media Cache",
    "VideoDecodeStats",
}
PROFILE_SNAPSHOT_DIR_NAMES = {
    "IndexedDB",
    "Local Storage",
    "Network",
    "Session Storage",
    "Sessions",
    "WebStorage",
}
PROFILE_SNAPSHOT_FILE_NAMES = {
    "Bookmarks",
    "Cookies",
    "Cookies-journal",
    "Favicons",
    "Favicons-journal",
    "History",
    "History-journal",
    "Login Data",
    "Login Data For Account",
    "Login Data For Account-journal",
    "Login Data-journal",
    "Preferences",
    "Secure Preferences",
    "Shortcuts",
    "Top Sites",
    "TransportSecurity",
    "Visited Links",
    "Web Data",
    "Web Data-journal",
}
PROFILE_SKIP_DIR_NAMES = PROFILE_CACHE_DIR_NAMES | {
    "Blob Storage",
    "CacheStorage",
    "File System",
    "Service Worker",
}
PROFILE_SKIP_FILE_SUFFIXES = (
    ".tmp",
    ".log",
)
PROFILE_SKIP_FILE_NAMES = {
    "LOCK",
    "lockfile",
    "SingletonCookie",
    "SingletonLock",
    "SingletonSocket",
}


def default_chrome_user_data_dir() -> str:
    if DEFAULT_WINDOWS_CHROME_USER_DATA_DIR.exists():
        return str(DEFAULT_WINDOWS_CHROME_USER_DATA_DIR)
    if DEFAULT_MACOS_CHROME_USER_DATA_DIR.exists():
        return str(DEFAULT_MACOS_CHROME_USER_DATA_DIR)
    for candidate in DEFAULT_LINUX_CHROME_USER_DATA_DIRS:
        if candidate.exists():
            return str(candidate)
    return ""


def normalize_browser_mode(raw_mode: str) -> str:
    normalized = str(raw_mode or "isolated").strip().lower()
    if normalized == "saved_profile":
        return "chrome_normale"
    if normalized == "custom_profile":
        return "profilo_personalizzato"
    if normalized in {"persistent_profile", "persistent_session"}:
        return "sessione_persistente"
    return normalized


def browser_mode_uses_profile(raw_mode: str) -> bool:
    return normalize_browser_mode(raw_mode) != "isolated"


def browser_mode_requires_custom_dir(raw_mode: str) -> bool:
    return normalize_browser_mode(raw_mode) == "profilo_personalizzato"


def resolve_browser_profile(config: dict) -> str | None:
    browser_mode = normalize_browser_mode(str(config.get("browser_mode", "isolated") or "isolated"))
    if browser_mode == "isolated":
        return None

    cached_profile_root = str(config.get("_resolved_browser_profile_root", "") or "").strip()
    if cached_profile_root:
        return cached_profile_root

    user_data_dir = str(config.get("browser_user_data_dir", "") or "").strip()
    if browser_mode == "profilo_personalizzato" and user_data_dir:
        config["_resolved_browser_profile_root"] = user_data_dir
        return user_data_dir

    if browser_mode == "sessione_persistente":
        persistent_root = prepare_persistent_profile_root(
            source_user_data_dir=user_data_dir or default_chrome_user_data_dir(),
            profile_directory=str(config.get("browser_profile_directory", "") or "Default"),
            refresh=bool(config.get("refresh_browser_profile", False)),
        )
        config["_resolved_browser_profile_root"] = persistent_root
        return persistent_root

    if browser_mode == "chrome_normale":
        source_user_data_dir = user_data_dir or default_chrome_user_data_dir()
        snapshot_root = prepare_chrome_normale_profile_copy(
            source_user_data_dir=source_user_data_dir,
            profile_directory=str(config.get("browser_profile_directory", "") or "Default"),
        )
        config["_resolved_browser_profile_root"] = snapshot_root
        return snapshot_root

    fallback = default_chrome_user_data_dir()
    if fallback:
        config["_resolved_browser_profile_root"] = fallback
    return fallback or None


def resolve_browser_arguments(config: dict) -> list[str]:
    browser_mode = normalize_browser_mode(str(config.get("browser_mode", "isolated") or "isolated"))
    if browser_mode == "isolated":
        return []

    arguments: list[str] = []
    profile_directory = str(config.get("browser_profile_directory", "") or "").strip()
    if profile_directory:
        arguments.append(f"--profile-directory={profile_directory}")

    return arguments


def prepare_chrome_normale_profile_copy(source_user_data_dir: str, profile_directory: str) -> str:
    source_root = Path(source_user_data_dir).expanduser()
    if not source_root.exists():
        raise FileNotFoundError(f"Chrome User Data non trovato: {source_root}")

    profile_name = (profile_directory or "Default").strip() or "Default"
    target_root = RUNTIME_PROFILES_DIR / f"cn_{_slug(profile_name)}"
    _ensure_runtime_target(target_root)

    if target_root.exists():
        shutil.rmtree(target_root)
    target_root.mkdir(parents=True, exist_ok=True)

    for root_file_name in ("Local State", "First Run"):
        source_file = source_root / root_file_name
        if source_file.exists():
            _copy_file_best_effort(source_file, target_root / root_file_name)

    source_profile_dir = source_root / profile_name
    if source_profile_dir.exists():
        _copy_profile_snapshot_best_effort(source_profile_dir, target_root / profile_name)

    return str(target_root)


def prepare_persistent_profile_root(source_user_data_dir: str, profile_directory: str, refresh: bool = False) -> str:
    profile_name = (profile_directory or "Default").strip() or "Default"
    target_root = persistent_profile_root(profile_name)
    _ensure_runtime_target(target_root)
    source_root = Path(source_user_data_dir).expanduser()

    if target_root.exists() and not refresh:
        return str(target_root)

    if target_root.exists() and refresh and not source_root.exists():
        return str(target_root)

    if target_root.exists() and refresh:
        shutil.rmtree(target_root)

    target_root.mkdir(parents=True, exist_ok=True)
    if source_root.exists():
        for root_file_name in ("Local State", "First Run"):
            source_file = source_root / root_file_name
            if source_file.exists():
                _copy_file_best_effort(source_file, target_root / root_file_name)

        source_profile_dir = source_root / profile_name
        if source_profile_dir.exists():
            _copy_profile_snapshot_best_effort(source_profile_dir, target_root / profile_name)

    return str(target_root)


def persistent_profile_root(profile_directory: str) -> Path:
    profile_name = (profile_directory or "Default").strip() or "Default"
    return PERSISTENT_PROFILES_DIR / f"p_{_slug(profile_name)}"


def inspect_persistent_profile(profile_directory: str) -> dict[str, str | bool | int]:
    profile_name = (profile_directory or "Default").strip() or "Default"
    target_root = persistent_profile_root(profile_name)
    profile_root = target_root / profile_name
    tracked_files = {
        "local_state": target_root / "Local State",
        "preferences": profile_root / "Preferences",
        "cookies": profile_root / "Cookies",
        "login_data": profile_root / "Login Data",
        "web_data": profile_root / "Web Data",
    }
    existing_files = [path for path in tracked_files.values() if path.exists()]
    last_updated_at = ""
    if existing_files:
        last_updated_at = datetime.fromtimestamp(
            max(path.stat().st_mtime for path in existing_files)
        ).isoformat(timespec="seconds")
    return {
        "profile_name": profile_name,
        "root_path": str(target_root),
        "profile_path": str(profile_root),
        "root_exists": target_root.exists(),
        "profile_exists": profile_root.exists(),
        "cookies_present": tracked_files["cookies"].exists() and tracked_files["cookies"].stat().st_size > 0,
        "login_data_present": tracked_files["login_data"].exists() and tracked_files["login_data"].stat().st_size > 0,
        "web_data_present": tracked_files["web_data"].exists() and tracked_files["web_data"].stat().st_size > 0,
        "tracked_file_count": len(existing_files),
        "last_updated_at": last_updated_at,
    }


def _copy_profile_snapshot_best_effort(source_dir: Path, target_dir: Path) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    for file_name in PROFILE_SNAPSHOT_FILE_NAMES:
        source_file = source_dir / file_name
        if source_file.exists():
            _copy_file_best_effort(source_file, target_dir / file_name)

    for dir_name in PROFILE_SNAPSHOT_DIR_NAMES:
        source_subdir = source_dir / dir_name
        if source_subdir.exists():
            _copy_profile_tree_best_effort(source_subdir, target_dir / dir_name)


def _copy_profile_tree_best_effort(source_dir: Path, target_dir: Path) -> None:
    for current_root, dir_names, file_names in os.walk(source_dir):
        current_path = Path(current_root)
        relative_path = current_path.relative_to(source_dir)
        destination_root = target_dir / relative_path
        if _path_is_too_long(destination_root):
            dir_names[:] = []
            continue

        try:
            destination_root.mkdir(parents=True, exist_ok=True)
        except OSError:
            dir_names[:] = []
            continue

        dir_names[:] = [name for name in dir_names if name not in PROFILE_SKIP_DIR_NAMES]
        for file_name in file_names:
            if _should_skip_profile_file(file_name):
                continue
            if _path_is_too_long(destination_root / file_name):
                continue
            _copy_file_best_effort(current_path / file_name, destination_root / file_name)


def _copy_file_best_effort(source_file: Path, target_file: Path) -> None:
    target_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(source_file, target_file)
    except (FileNotFoundError, PermissionError, OSError):
        return


def _should_skip_profile_file(file_name: str) -> bool:
    if file_name in PROFILE_SKIP_FILE_NAMES:
        return True
    return file_name.endswith(PROFILE_SKIP_FILE_SUFFIXES)


def _ensure_runtime_target(target_root: Path) -> None:
    if target_root.is_absolute():
        return
    resolved_workspace = WORKSPACE_ROOT.resolve()
    resolved_target = target_root.resolve()
    resolved_target.relative_to(resolved_workspace)


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
    return cleaned or "default"


def _path_is_too_long(path: Path) -> bool:
    return len(str(path)) >= 240
