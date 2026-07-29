from __future__ import annotations

import subprocess
import sys
from datetime import datetime
from pathlib import Path

from botasaurus.browser import Driver, Wait, browser

from ..browser_helpers import DEFAULT_COOKIE_REJECT_TEXTS, click_first_matching_text, current_page_url, navigate_with_retries
from ..browser_runtime import resolve_browser_arguments, resolve_browser_profile
from ..utils import normalize_whitespace
from ..vinted_access import emit_vinted_access_signal, wait_for_vinted_access_status
from .vinted import _hold_vinted_browser_if_requested, _wait_for_vinted_login_if_needed


VINTED_UPLOAD_ENTRY_URL = "https://www.vinted.it/items/new"
VINTED_UPLOAD_ENTRY_TEXTS = (
    "Vendi subito",
    "Sell now",
)
VINTED_UPLOAD_PUBLISH_TEXTS = (
    "Carica",
    "Pubblica",
    "Metti in vendita",
    "Upload",
    "Publish",
)
VINTED_UPLOAD_ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
VINTED_UPLOAD_MAX_UPLOAD_ATTEMPTS = 3


def run_vinted_upload_action(
    title: str = "",
    description: str = "",
    price: object = "",
    category: str = "",
    brand: str = "",
    condition: str = "",
    material: str = "",
    photo_paths: list[str] | None = None,
    submit: bool = False,
    keep_browser_open: bool = True,
    keep_open_seconds: int = 0,
    slow_mode: bool = False,
    action_delay_seconds: float = 1.5,
    page_settle_seconds: float = 3.0,
    browser_mode: str = "chrome_normale",
    browser_user_data_dir: str = "",
    browser_profile_directory: str = "Default",
) -> dict:
    item = _normalize_upload_item(
        {
            "title": title,
            "description": description,
            "price": price,
            "category": category,
            "brand": brand,
            "condition": condition,
            "material": material,
            "photo_paths": photo_paths or [],
        }
    )
    config = {
        "item": item,
        "submit": bool(submit),
        "keep_browser_open": bool(keep_browser_open),
        "keep_open_seconds": max(int(keep_open_seconds or 0), 0),
        "slow_mode": bool(slow_mode),
        "action_delay_seconds": float(action_delay_seconds),
        "page_settle_seconds": float(page_settle_seconds),
        "browser_mode": browser_mode,
        "browser_user_data_dir": browser_user_data_dir,
        "browser_profile_directory": browser_profile_directory,
    }
    return _run_vinted_upload_task(config)


def run_vinted_upload_batch(
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
) -> dict:
    normalized_items = [_normalize_upload_item(item) for item in items if isinstance(item, dict)]
    if not normalized_items:
        raise ValueError("Vinted upload batch requires at least one valid item.")
    config = {
        "items": normalized_items,
        "submit": bool(submit),
        "delay_between_seconds": max(int(delay_between_seconds or 0), 0),
        "keep_browser_open": bool(keep_browser_open),
        "keep_open_seconds": max(int(keep_open_seconds or 0), 0),
        "slow_mode": bool(slow_mode),
        "action_delay_seconds": float(action_delay_seconds),
        "page_settle_seconds": float(page_settle_seconds),
        "browser_mode": browser_mode,
        "browser_user_data_dir": browser_user_data_dir,
        "browser_profile_directory": browser_profile_directory,
    }
    return _run_vinted_upload_batch_task(config)


@browser(
    profile=resolve_browser_profile,
    add_arguments=resolve_browser_arguments,
    wait_for_complete_page_load=False,
)
def _run_vinted_upload_task(driver: Driver, config: dict) -> dict:
    result = _upload_single_vinted_item(driver, config)
    _hold_vinted_browser_if_requested(
        driver,
        keep_browser_open=bool(config.get("keep_browser_open", False)),
        keep_open_seconds=int(config.get("keep_open_seconds", 0) or 0),
    )
    return result


@browser(
    profile=resolve_browser_profile,
    add_arguments=resolve_browser_arguments,
    wait_for_complete_page_load=False,
)
def _run_vinted_upload_batch_task(driver: Driver, config: dict) -> dict:
    items = list(config.get("items", []) or [])
    delay_between_seconds = max(int(config.get("delay_between_seconds", 2) or 0), 0)
    if bool(config.get("slow_mode", False)):
        delay_between_seconds = max(delay_between_seconds, 2)
    results: list[dict] = []
    for index, item in enumerate(items):
        try:
            result = _upload_single_vinted_item(driver, {**config, "item": item})
        except Exception as exc:
            result = _build_vinted_upload_error_result(
                driver,
                item,
                error=f"{type(exc).__name__}: {exc}",
            )
        results.append(result)
        if index < len(items) - 1 and delay_between_seconds > 0:
            driver.sleep(delay_between_seconds)
    _hold_vinted_browser_if_requested(
        driver,
        keep_browser_open=bool(config.get("keep_browser_open", False)),
        keep_open_seconds=int(config.get("keep_open_seconds", 0) or 0),
    )
    prepared_count = sum(1 for item in results if item.get("prepared"))
    submitted_count = sum(1 for item in results if item.get("submitted"))
    failed_count = sum(1 for item in results if not item.get("ok"))
    return {
        "ok": submitted_count > 0 if bool(config.get("submit", False)) else prepared_count > 0,
        "items_count": len(items),
        "prepared_count": prepared_count,
        "submitted_count": submitted_count,
        "failed_count": failed_count,
        "submit": bool(config.get("submit", False)),
        "delay_between_seconds": delay_between_seconds,
        "keep_browser_open": bool(config.get("keep_browser_open", False)),
        "keep_open_seconds": int(config.get("keep_open_seconds", 0) or 0),
        "results": results,
    }


def _upload_single_vinted_item(driver: Driver, config: dict) -> dict:
    item = _normalize_upload_item(config.get("item", {}))
    action_delay_seconds = _normalized_delay_seconds(
        config.get("action_delay_seconds", 1.5),
        default=1.5 if bool(config.get("slow_mode", False)) else 0.0,
    )
    page_settle_seconds = _normalized_delay_seconds(
        config.get("page_settle_seconds", 3.0),
        default=3.0 if bool(config.get("slow_mode", False)) else 0.0,
    )

    entry_action, navigated = _open_vinted_sell_page(driver)
    _sleep_if_needed(driver, page_settle_seconds)
    cookie_action = click_first_matching_text(driver, DEFAULT_COOKIE_REJECT_TEXTS) or ""
    if cookie_action:
        _sleep_if_needed(driver, min(action_delay_seconds, 0.35))

    access_status = wait_for_vinted_access_status(
        driver,
        max_wait_seconds=min(max(page_settle_seconds, 0.0), 0.8),
    )
    emit_vinted_access_signal(access_status)
    access_status = _wait_for_vinted_login_if_needed(
        driver,
        access_status,
        revisit_url=VINTED_UPLOAD_ENTRY_URL,
        action_delay_seconds=action_delay_seconds,
        page_settle_seconds=page_settle_seconds,
    )
    _sleep_if_needed(driver, action_delay_seconds)
    _dismiss_vinted_upload_system_error(driver, allow_refresh=True)

    upload_button_action = ""
    upload_mode = ""
    photo_upload_debug: dict[str, str | bool] = {}
    upload_attempts = 0
    upload_errors: list[str] = []
    validated_photo_paths = _validate_vinted_upload_photo_paths(list(item["photo_paths"]))
    for attempt in range(1, VINTED_UPLOAD_MAX_UPLOAD_ATTEMPTS + 1):
        upload_attempts = attempt
        _dismiss_vinted_upload_system_error(driver, allow_refresh=True)
        upload_button_action = _click_upload_photos_button(driver) or upload_button_action
        upload_mode = ""
        try:
            upload_selector = _wait_for_vinted_photo_input(driver, max_wait_seconds=max(action_delay_seconds, 3.0))
            if upload_selector:
                driver.upload_multiple_files(upload_selector, validated_photo_paths, wait=Wait.LONG)
                upload_mode = "dom"
            elif _should_use_macos_finder_upload():
                upload_mode = _upload_vinted_photos_via_macos_finder(driver, validated_photo_paths)
            else:
                raise RuntimeError("Input foto Vinted non trovato sulla pagina di caricamento.")
        except Exception as exc:
            upload_errors.append(f"attempt {attempt}: {type(exc).__name__}: {exc}")
        _sleep_if_needed(driver, max(action_delay_seconds, 1.0))
        _dismiss_vinted_upload_system_error(driver, allow_refresh=True)
        photo_upload_debug = _wait_for_vinted_uploaded_photo(driver, max_wait_seconds=max(page_settle_seconds, 8.0))
        if photo_upload_debug.get("uploaded"):
            break
        if attempt < VINTED_UPLOAD_MAX_UPLOAD_ATTEMPTS:
            _sleep_if_needed(driver, max(action_delay_seconds, 1.0))
    if not photo_upload_debug.get("uploaded"):
        raise RuntimeError(
            "La foto non risulta caricata su Vinted."
            + (f" Tentativi: {upload_attempts}." if upload_attempts else "")
            + (f" Errori: {' || '.join(upload_errors)}." if upload_errors else "")
            + (
                " DOM: "
                f"alt={photo_upload_debug.get('alt','')} "
                f"src={photo_upload_debug.get('src','')} "
                f"visible_count={photo_upload_debug.get('visible_count','')} "
                f"button_present={photo_upload_debug.get('button_present','')} "
                f"remove_controls={photo_upload_debug.get('remove_controls','')} "
                f"input_file_count={photo_upload_debug.get('input_file_count','')} "
                f"input_file_names={photo_upload_debug.get('input_file_names','')}"
                if photo_upload_debug
                else ""
            )
        )

    title_filled = _fill_text_input(driver, '#title[data-testid="title--input"]', str(item["title"]))
    description_filled = _fill_textarea_input(driver, '#description[data-testid="description--input"]', str(item["description"]))
    category_selected = _select_vinted_picker_option(
        driver,
        'input#category[data-testid="catalog-select-dropdown-input"][name="category"]',
        str(item["category"]),
        list_selector='ul[data-testid="category-list"]',
        option_title_selector='.web_ui__Cell__title',
        clickable_option_selector='[role="button"]',
    )
    brand_selected = _select_vinted_picker_option(
        driver,
        'input#brand[data-testid="brand-select-dropdown-input"][name="brand"]',
        str(item["brand"]),
    )
    condition_selected = _select_vinted_picker_option(
        driver,
        'input#condition[data-testid="category-condition-single-list-input"][name="condition"]',
        str(item["condition"]),
    )
    material_selected = _select_vinted_picker_option(
        driver,
        'input#material[data-testid="category-material-multi-list-input"][name="material"]',
        str(item["material"]),
    )
    price_filled = _fill_text_input(driver, '#price[data-testid="price-input--input"]', str(item["price"]))
    field_debug = _collect_vinted_upload_field_debug(driver)
    prepared = bool(
        title_filled
        and description_filled
        and category_selected
        and brand_selected
        and condition_selected
        and material_selected
        and price_filled
    )
    if not prepared:
        failed = [
            name
            for name, ok in (
                ("title", title_filled),
                ("description", description_filled),
                ("category", category_selected),
                ("brand", brand_selected),
                ("condition", condition_selected),
                ("material", material_selected),
                ("price", price_filled),
            )
            if not ok
        ]
        debug_parts = [f"{key}={value}" for key, value in field_debug.items() if value]
        raise RuntimeError(
            "Impossibile compilare tutti i campi richiesti dell'annuncio Vinted."
            + (f" Failed: {', '.join(failed)}." if failed else "")
            + (f" DOM: {' | '.join(debug_parts)}" if debug_parts else "")
        )

    submit_action = ""
    submitted = False
    if bool(config.get("submit", False)):
        _sleep_if_needed(driver, max(action_delay_seconds, 1.0))
        submit_action = _click_vinted_upload_submit(driver) or ""
        submitted = bool(submit_action)
        if not submitted:
            raise RuntimeError("Pulsante finale di pubblicazione Vinted non trovato o non cliccabile.")
        _sleep_if_needed(driver, max(action_delay_seconds, 1.5))

    return {
        "ok": submitted if bool(config.get("submit", False)) else prepared,
        "prepared": prepared,
        "submitted": submitted,
        "title": str(item["title"]),
        "description": str(item["description"]),
        "price": str(item["price"]),
        "category": str(item["category"]),
        "brand": str(item["brand"]),
        "condition": str(item["condition"]),
        "material": str(item["material"]),
        "photo_paths": list(item["photo_paths"]),
        "photos_count": len(list(item["photo_paths"])),
        "entry_action": entry_action,
        "navigated": navigated,
        "current_url": current_page_url(driver),
        "cookie_banner_action": cookie_action,
        "upload_button_action": upload_button_action,
        "upload_mode": upload_mode,
        "upload_attempts": upload_attempts,
        "upload_errors": upload_errors,
        "photo_upload_debug": photo_upload_debug,
        "title_filled": title_filled,
        "description_filled": description_filled,
        "category_selected": category_selected,
        "brand_selected": brand_selected,
        "condition_selected": condition_selected,
        "material_selected": material_selected,
        "price_filled": price_filled,
        "field_debug": field_debug,
        "submit_action": submit_action,
        "login_required": not bool(access_status.get("marker_present")),
        "submitted_at": datetime.now().isoformat(timespec="seconds") if submitted else "",
        "keep_browser_open": bool(config.get("keep_browser_open", False)),
        "keep_open_seconds": int(config.get("keep_open_seconds", 0) or 0),
    }


def _open_vinted_sell_page(driver: Driver) -> tuple[str, bool]:
    current_url = current_page_url(driver)
    if "/items/new" in current_url:
        return "already-open", True
    navigated_home = navigate_with_retries(driver, "https://www.vinted.it", wait=Wait.LONG)
    if click_first_matching_text(driver, VINTED_UPLOAD_ENTRY_TEXTS):
        driver.sleep(1)
        if "/items/new" in current_page_url(driver):
            return "click-vendi-subito", True
    navigated_new = navigate_with_retries(driver, VINTED_UPLOAD_ENTRY_URL, wait=Wait.LONG)
    return ("direct-items-new" if navigated_new else "direct-items-new-fallback"), (navigated_home or navigated_new)


def _click_upload_photos_button(driver: Driver) -> str | None:
    clicked = driver.run_js(
        """
const normalize = (value) => (value || '').replace(/\\s+/g, ' ').trim().toLowerCase();
const isVisible = (element) => {
  const style = window.getComputedStyle(element);
  return style.display !== 'none' && style.visibility !== 'hidden' && element.getClientRects().length > 0;
};
const clickElement = (element) => {
  if (!element || !isVisible(element)) return false;
  try { element.scrollIntoView({block: 'center'}); } catch {}
  try { element.click(); } catch {}
  try { element.dispatchEvent(new MouseEvent('mousedown', { bubbles: true })); } catch {}
  try { element.dispatchEvent(new MouseEvent('mouseup', { bubbles: true })); } catch {}
  try { element.dispatchEvent(new MouseEvent('click', { bubbles: true })); } catch {}
  return true;
};
const uploadTextMatches = (element) => {
  const text = normalize(element.innerText || element.textContent || element.getAttribute('aria-label') || '');
  return (
    text.includes('carica le foto') ||
    text.includes('upload photos') ||
    text.includes('upload photo') ||
    text.includes('aggiungi foto') ||
    text.includes('add photos')
  );
};

const directInput = [...document.querySelectorAll('input[type="file"]')].find((element) => {
  const accept = normalize(element.getAttribute('accept') || '');
  return accept.includes('image') || element.multiple;
});
if (directInput) {
  return 'file-input-present';
}

const uploadButton = [...document.querySelectorAll('button, [role="button"], label, div, span, a')]
  .find((element) => isVisible(element) && uploadTextMatches(element));
if (uploadButton && clickElement(uploadButton)) {
  return 'upload-text-trigger';
}

const plusTrigger = [...document.querySelectorAll('[data-testid="plus"], button:has([data-testid="plus"]), button, [role="button"]')]
  .find((element) => {
    if (!isVisible(element)) return false;
    const text = normalize(element.innerText || element.textContent || element.getAttribute('aria-label') || '');
    return text.includes('carica') || text.includes('upload') || element.querySelector?.('[data-testid="plus"]');
  });
if (plusTrigger && clickElement(plusTrigger)) {
  return 'upload-plus-trigger';
}

const mediaRoot = document.querySelector('div[class*="media-select__input"]') ||
  document.querySelector('div[class*="_media-select-module"]') ||
  [...document.querySelectorAll('div, section')].find((element) => isVisible(element) && normalize(element.innerText || element.textContent).includes('carica le foto'));
if (mediaRoot && clickElement(mediaRoot)) {
  return 'upload-media-root';
}

const inputLabel = [...document.querySelectorAll('label')].find((element) => {
  if (!isVisible(element)) return false;
  const forId = element.getAttribute('for') || '';
  if (!forId) return false;
  const target = document.getElementById(forId);
  return !!target && target.tagName === 'INPUT' && (target.getAttribute('type') || '').toLowerCase() === 'file';
});
if (inputLabel && clickElement(inputLabel)) {
  return 'upload-input-label';
}

return '';
        """
    )
    return str(clicked or "").strip() or None


def _prepare_vinted_photo_input(driver: Driver) -> str:
    selector = str(
        driver.run_js(
            """
const inputs = [...document.querySelectorAll('input[type="file"]')];
const input = inputs.find((element) => {
  const accept = (element.getAttribute('accept') || '').toLowerCase();
  return accept.includes('image') || element.multiple || inputs.length === 1;
});
if (!input) return '';
input.removeAttribute('disabled');
input.style.display = 'block';
input.style.visibility = 'visible';
input.style.opacity = '1';
input.style.pointerEvents = 'auto';
input.style.position = 'fixed';
input.style.left = '0';
input.style.top = '0';
input.style.width = '1px';
input.style.height = '1px';
input.scrollIntoView({block: 'center'});
input.setAttribute('data-vinted-upload-input', 'true');
return 'input[data-vinted-upload-input="true"]';
            """
        )
        or ""
    ).strip()
    return selector


def _wait_for_vinted_photo_input(driver: Driver, max_wait_seconds: float = 3.0) -> str:
    attempts = max(1, int(max_wait_seconds / 0.25))
    last_selector = ""
    for _ in range(attempts):
        last_selector = _prepare_vinted_photo_input(driver)
        if last_selector:
            return last_selector
        driver.sleep(0.25)
    return last_selector


def _validate_vinted_upload_photo_paths(photo_paths: list[str]) -> list[str]:
    valid_paths = [Path(path).expanduser().resolve() for path in photo_paths if str(path or "").strip()]
    if not valid_paths:
        raise RuntimeError("Nessuna foto valida disponibile per l'upload.")
    normalized: list[str] = []
    for source_path in valid_paths:
        if not source_path.exists():
            raise FileNotFoundError(f"Foto non trovata: {source_path}")
        if source_path.suffix.lower() not in VINTED_UPLOAD_ALLOWED_EXTENSIONS:
            raise ValueError(
                f"Formato foto non supportato per Vinted: {source_path.suffix}. "
                "Usa PNG, JPG, JPEG o WEBP."
            )
        normalized.append(str(source_path))
    return normalized


def _should_use_macos_finder_upload() -> bool:
    return sys.platform == "darwin"


def _upload_vinted_photos_via_macos_finder(driver: Driver, photo_paths: list[str]) -> str:
    valid_paths = [Path(path).expanduser().resolve() for path in photo_paths if str(path or "").strip()]
    parent_dir = valid_paths[0].parent
    file_names: list[str] = []
    file_paths: list[str] = []
    for source_path in valid_paths:
        if source_path.parent != parent_dir:
            raise ValueError("Per l'analisi Finder tutte le foto devono stare nella stessa cartella.")
        file_names.append(source_path.name)
        file_paths.append(str(source_path))
    opened = _open_vinted_file_picker_via_click(driver)
    if not opened:
        raise RuntimeError("Pulsante upload foto Vinted non cliccabile.")
    _run_macos_finder_upload_dialog(str(parent_dir), file_names, file_paths)
    return "finder"


def _open_vinted_file_picker_via_click(driver: Driver) -> bool:
    return bool(
        driver.run_js(
            """
const normalize = (value) => (value || '').replace(/\\s+/g, ' ').trim().toLowerCase();
const isVisible = (element) => {
  const style = window.getComputedStyle(element);
  return style.display !== 'none' && style.visibility !== 'hidden' && element.getClientRects().length > 0;
};
const buttons = [...document.querySelectorAll('button, [role="button"]')];
const button = buttons.find((element) => isVisible(element) && normalize(element.innerText || element.textContent).includes('carica le foto'));
if (!button) return false;
button.click();
return true;
            """
        )
    )


def _run_macos_finder_upload_dialog(target_dir: str, file_names: list[str], file_paths: list[str]) -> None:
    quoted_dir = _applescript_quote(str(target_dir))
    first_file = _applescript_quote(file_names[0] if file_names else "")
    first_file_path = _applescript_quote(file_paths[0] if file_paths else "")
    multi_mode = "true" if len(file_names) > 1 else "false"
    script = f"""
set targetFolder to {quoted_dir}
set firstFileName to {first_file}
set firstFilePath to {first_file_path}
set multipleMode to {multi_mode}

on refreshDialogRef()
    tell application "System Events"
        tell process "Google Chrome"
            try
                if exists sheet 1 of window 1 then
                    return sheet 1 of window 1
                else if exists window 1 then
                    return window 1
                end if
            end try
        end tell
    end tell
    return missing value
end refreshDialogRef

on pressGo(dialogRef)
    tell application "System Events"
        tell process "Google Chrome"
            if exists button "Vai" of dialogRef then
                click button "Vai" of dialogRef
            else if exists button "Go" of dialogRef then
                click button "Go" of dialogRef
            else
                key code 36
            end if
        end tell
    end tell
end pressGo

on openButtonEnabled(dialogRef)
    tell application "System Events"
        tell process "Google Chrome"
            try
                if exists button "Apri" of dialogRef then return enabled of button "Apri" of dialogRef
            end try
            try
                if exists button "Open" of dialogRef then return enabled of button "Open" of dialogRef
            end try
        end tell
    end tell
    return false
end openButtonEnabled

on selectFileInDialog(dialogRef, fileName)
    tell application "System Events"
        tell process "Google Chrome"
            try
                if exists outline 1 of scroll area 1 of dialogRef then
                    set fileOutline to outline 1 of scroll area 1 of dialogRef
                    repeat with fileRow in rows of fileOutline
                        try
                            if exists static text 1 of UI element 1 of fileRow then
                                set rowName to value of static text 1 of UI element 1 of fileRow
                                if rowName is fileName then
                                    select fileRow
                                    delay 0.4
                                    click fileRow
                                    return true
                                end if
                            end if
                        end try
                        try
                            if exists static text 1 of fileRow then
                                set rowName to value of static text 1 of fileRow
                                if rowName is fileName then
                                    select fileRow
                                    delay 0.4
                                    click fileRow
                                    return true
                                end if
                            end if
                        end try
                    end repeat
                end if
            end try
            try
                if exists scroll area 1 of dialogRef then
                    click scroll area 1 of dialogRef
                    delay 0.5
                    keystroke fileName
                    delay 1.0
                    return true
                end if
            end try
        end tell
    end tell
    return false
end selectFileInDialog

tell application "Google Chrome" to activate
tell application "System Events"
    tell process "Google Chrome"
        set dialogReady to false
        set dialogRef to missing value
        repeat 40 times
            try
                if exists sheet 1 of window 1 then
                    set dialogRef to sheet 1 of window 1
                else if exists window 1 then
                    set dialogRef to window 1
                else
                    set dialogRef to missing value
                end if
            on error
                set dialogRef to missing value
            end try
            if dialogRef is not missing value then
                set dialogReady to true
                exit repeat
            end if
            delay 0.25
        end repeat
        if dialogReady is false then error "Chrome upload dialog not ready"

        keystroke "G" using {{command down, shift down}}
        delay 0.6
        keystroke targetFolder
        delay 0.4
        my pressGo(dialogRef)
        delay 1.4

        if multipleMode then
            set selectionReady to false
            repeat 3 times
                keystroke "a" using {{command down}}
                delay 1.5
                set dialogRef to my refreshDialogRef()
                if dialogRef is not missing value and my openButtonEnabled(dialogRef) then
                    set selectionReady to true
                    exit repeat
                end if
            end repeat
            if selectionReady is false then error "Finder dialog did not keep multi-file selection active"
        else
            set selectionReady to false
            repeat 3 times
                set dialogRef to my refreshDialogRef()
                if dialogRef is missing value then error "Chrome upload dialog disappeared during file selection"
                set fileSelectionTriggered to my selectFileInDialog(dialogRef, firstFileName)
                if fileSelectionTriggered is false then
                    delay 0.8
                else
                    delay 1.8
                end if
                set dialogRef to my refreshDialogRef()
                if dialogRef is not missing value and my openButtonEnabled(dialogRef) then
                    set selectionReady to true
                    exit repeat
                end if
                delay 0.8
            end repeat
            if selectionReady is false then error "Finder dialog did not keep the requested file selected"
        end if

        set dialogRef to my refreshDialogRef()
        if dialogRef is missing value then error "Chrome upload dialog disappeared before confirmation"
        if my openButtonEnabled(dialogRef) is false then error "Finder dialog open button is still disabled before confirmation"
        delay 2.0
        key code 36
        delay 1.0
    end tell
end tell
""".strip()
    completed = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        stderr = str(completed.stderr or "").strip()
        stdout = str(completed.stdout or "").strip()
        raise RuntimeError(f"Finder upload dialog automation failed: {stderr or stdout or 'unknown error'}")


def _applescript_quote(value: str) -> str:
    return '"' + str(value or "").replace('"', '""') + '"'


def _fill_text_input(driver: Driver, selector: str, value: str) -> bool:
    if not value.strip():
        return False
    return bool(
        driver.run_js(
            """
const element = document.querySelector(args.selector);
if (!element) return false;
const nativeSetter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
element.focus();
if (nativeSetter) {
  nativeSetter.call(element, args.value);
} else {
  element.value = args.value;
}
element.dispatchEvent(new Event('input', { bubbles: true }));
element.dispatchEvent(new Event('change', { bubbles: true }));
const current = (element.value || '').replace(/\\s+/g, ' ').trim();
const target = (args.value || '').replace(/\\s+/g, ' ').trim();
return current === target;
            """,
            {"selector": selector, "value": value},
        )
    )


def _fill_textarea_input(driver: Driver, selector: str, value: str) -> bool:
    if not value.strip():
        return False
    return bool(
        driver.run_js(
            """
const element = document.querySelector(args.selector);
if (!element) return false;
const nativeSetter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')?.set;
element.focus();
if (nativeSetter) {
  nativeSetter.call(element, args.value);
} else {
  element.value = args.value;
}
element.dispatchEvent(new Event('input', { bubbles: true }));
element.dispatchEvent(new Event('change', { bubbles: true }));
const current = (element.value || '').replace(/\\s+/g, ' ').trim();
const target = (args.value || '').replace(/\\s+/g, ' ').trim();
return current === target;
            """,
            {"selector": selector, "value": value},
        )
    )


def _select_vinted_picker_option(
    driver: Driver,
    input_selector: str,
    target_value: str,
    *,
    list_selector: str = "",
    option_title_selector: str = "",
    clickable_option_selector: str = "",
) -> bool:
    return _select_vinted_picker_option_with_config(
        driver,
        input_selector,
        target_value,
        list_selector=list_selector,
        option_title_selector=option_title_selector,
        clickable_option_selector=clickable_option_selector,
    )


def _select_vinted_picker_option_with_config(
    driver: Driver,
    input_selector: str,
    target_value: str,
    *,
    list_selector: str = "",
    option_title_selector: str = "",
    clickable_option_selector: str = "",
) -> bool:
    target = normalize_whitespace(str(target_value or ""))
    if not target:
        return False
    _dismiss_vinted_upload_system_error(driver, allow_refresh=True)
    opened = bool(
        driver.run_js(
            """
const element = document.querySelector(args.selector);
if (!element) return false;
element.click();
element.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
element.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
return true;
            """,
            {"selector": input_selector},
        )
    )
    if not opened:
        return False
    driver.sleep(0.6)
    typed_search = bool(
        driver.run_js(
            """
const root = document;
const target = args.target || '';
const searchInput = root.querySelector('#catalog-search-input, input[name="catalog-search-input"], .input-dropdown input[type="text"]');
if (!searchInput) return false;
searchInput.focus();
searchInput.value = target;
searchInput.dispatchEvent(new Event('input', { bubbles: true }));
searchInput.dispatchEvent(new Event('change', { bubbles: true }));
return true;
            """,
            {"target": target},
        )
    )
    if typed_search:
        driver.sleep(0.8)
    selected = bool(
        driver.run_js(
            """
const normalize = (value) => (value || '').replace(/\\s+/g, ' ').trim().toLowerCase();
const target = normalize(args.target);
const isVisible = (element) => {
  const style = window.getComputedStyle(element);
  return style.display !== 'none' && style.visibility !== 'hidden' && element.getClientRects().length > 0;
};
const listSelector = args.list_selector || '';
const optionTitleSelector = args.option_title_selector || '';
const clickableOptionSelector = args.clickable_option_selector || '';

if (listSelector) {
  const listRoot = document.querySelector(listSelector);
  if (listRoot) {
    const items = [...listRoot.querySelectorAll('li')];
    for (const item of items) {
      const titleNode = optionTitleSelector ? item.querySelector(optionTitleSelector) : item;
      const text = normalize(titleNode ? (titleNode.innerText || titleNode.textContent || '') : '');
      if (text === target || text.includes(target)) {
        const clickable = clickableOptionSelector ? item.querySelector(clickableOptionSelector) : item;
        if (clickable && isVisible(clickable)) {
          clickable.click();
          return true;
        }
      }
    }
  }
}

const dropdownRoots = [
  document.querySelector('[data-testid="catalog-select-dropdown-content"]'),
  document.querySelector('.input-dropdown'),
  document,
].filter(Boolean);

const matchesTarget = (element) => {
  const text = normalize(element.innerText || element.textContent || element.value || '');
  return text === target;
};
const looserMatch = (element) => {
  const text = normalize(element.innerText || element.textContent || element.value || '');
  return text.includes(target);
};
for (const root of dropdownRoots) {
  const candidates = [
    ...root.querySelectorAll('[role="option"]'),
    ...root.querySelectorAll('[role="button"]'),
    ...root.querySelectorAll('li'),
    ...root.querySelectorAll('button'),
    ...root.querySelectorAll('div'),
    ...root.querySelectorAll('span'),
  ];
  let option = candidates.find((element) => isVisible(element) && matchesTarget(element));
  if (!option) {
    option = candidates.find((element) => isVisible(element) && looserMatch(element));
  }
  if (option) {
    option.click();
    return true;
  }
}
return false;
            """,
            {
                "target": target,
                "list_selector": list_selector,
                "option_title_selector": option_title_selector,
                "clickable_option_selector": clickable_option_selector,
            },
        )
    )
    if not selected:
        return False
    driver.sleep(0.8)
    _dismiss_vinted_upload_system_error(driver, allow_refresh=False)
    return bool(
        driver.run_js(
            """
const element = document.querySelector(args.selector);
if (!element) return false;
const current = (element.value || element.getAttribute('value') || '').replace(/\\s+/g, ' ').trim().toLowerCase();
const target = (args.target || '').replace(/\\s+/g, ' ').trim().toLowerCase();
return current === target || current.includes(target);
            """,
            {"selector": input_selector, "target": target},
        )
    )


def _dismiss_vinted_upload_system_error(driver: Driver, *, allow_refresh: bool) -> bool:
    action = str(
        driver.run_js(
            """
const normalize = (value) => (value || '').replace(/\\s+/g, ' ').trim().toLowerCase();
const isVisible = (element) => {
  const style = window.getComputedStyle(element);
  return style.display !== 'none' && style.visibility !== 'hidden' && element.getClientRects().length > 0;
};
const text = normalize(document.body ? (document.body.innerText || document.body.textContent || '') : '');
if (!text.includes('qualcosa è andato storto')) return '';
const buttons = [...document.querySelectorAll('button, [role="button"]')].filter(isVisible);
const refresh = buttons.find((button) => normalize(button.innerText || button.textContent).includes('aggiorna pagina'));
if (refresh) {
  refresh.click();
  return 'refresh';
}
const close = buttons.find((button) => {
  const value = normalize(button.innerText || button.textContent);
  return value === 'chiudi' || value === 'ok' || value.includes('close');
});
if (close) {
  close.click();
  return 'close';
}
return 'detected';
            """
        )
        or ""
    ).strip()
    if not action:
        return False
    if action == "refresh" and allow_refresh:
        driver.sleep(2.0)
        navigate_with_retries(driver, VINTED_UPLOAD_ENTRY_URL, wait=Wait.LONG)
        driver.sleep(2.0)
    return True


def _collect_vinted_upload_field_debug(driver: Driver) -> dict[str, str]:
    payload = driver.run_js(
        """
const read = (selector) => {
  const element = document.querySelector(selector);
  if (!element) return '';
  return String(element.value || element.getAttribute('value') || element.textContent || '').replace(/\\s+/g, ' ').trim();
};
return {
  title: read('#title[data-testid="title--input"]'),
  description: read('#description[data-testid="description--input"]'),
  category: read('input#category[data-testid="catalog-select-dropdown-input"][name="category"]'),
  brand: read('input#brand[data-testid="brand-select-dropdown-input"][name="brand"]'),
  condition: read('input#condition[data-testid="category-condition-single-list-input"][name="condition"]'),
  material: read('input#material[data-testid="category-material-multi-list-input"][name="material"]'),
  price: read('#price[data-testid="price-input--input"]'),
};
        """
    )
    if not isinstance(payload, dict):
        return {}
    return {str(key): normalize_whitespace(str(value or "")) for key, value in payload.items()}


def _wait_for_vinted_uploaded_photo(driver: Driver, max_wait_seconds: float = 8.0) -> dict[str, str | bool]:
    attempts = max(1, int(max_wait_seconds / 0.5))
    for _ in range(attempts):
        payload = _collect_vinted_uploaded_photo_debug(driver)
        if payload.get("uploaded"):
            return payload
        driver.sleep(0.5)
    return _collect_vinted_uploaded_photo_debug(driver)


def _collect_vinted_uploaded_photo_debug(driver: Driver) -> dict[str, str | bool]:
    payload = driver.run_js(
        """
const normalize = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
const mediaRoot =
  document.querySelector('div[class*="media-select__input"]') ||
  document.querySelector('div[class*="_media-select-module"]') ||
  document.querySelector('[data-testid="plus"]')?.closest('div');
if (!mediaRoot) {
  const fileInputs = [...document.querySelectorAll('input[type="file"]')];
  const activeInput = fileInputs.find((input) => (input.files && input.files.length > 0)) || null;
  return {
    uploaded: !!activeInput,
    alt: '',
    src: '',
    visible_count: '0',
    button_present: 'false',
    root_found: 'false',
    input_file_count: activeInput ? String(activeInput.files.length || 0) : '0',
    input_file_names: activeInput ? [...activeInput.files].map((file) => normalize(file.name)).join(' | ') : '',
  };
}
const images = [...mediaRoot.querySelectorAll('img.web_ui__Image__content, img')];
const fileInputs = [...mediaRoot.querySelectorAll('input[type="file"]')];
const activeInput = fileInputs.find((input) => (input.files && input.files.length > 0))
  || [...document.querySelectorAll('input[type="file"]')].find((input) => (input.files && input.files.length > 0))
  || null;
const removeControls = [...mediaRoot.querySelectorAll('button, [role="button"], div, span')].filter((element) => {
  const text = normalize(element.innerText || element.textContent || element.getAttribute('aria-label') || '').toLowerCase();
  const testId = normalize(element.getAttribute('data-testid') || '').toLowerCase();
  return (
    text.includes('copertina') ||
    text.includes('rimuovi') ||
    text.includes('remove') ||
    testId.includes('close') ||
    testId.includes('remove')
  );
});
const uploaded = images.find((image) => {
  const alt = normalize(image.getAttribute('alt')).toLowerCase();
  const src = normalize(image.getAttribute('src'));
  return (
    alt.includes('foto 1 di') ||
    alt.includes('caricata per ridisporre le foto') ||
    alt.includes('uploaded') ||
    src.includes('images') ||
    src.startsWith('blob:')
  );
});
const uploadButton = [...mediaRoot.querySelectorAll('button, [role="button"]')].find((element) =>
  normalize(element.innerText || element.textContent).toLowerCase().includes('carica le foto')
);
if (!uploaded) {
  const uploadedByState = !!activeInput || (!uploadButton && images.length > 0) || removeControls.length > 0;
  return {
    uploaded: uploadedByState,
    alt: '',
    src: '',
    visible_count: String(images.length),
    button_present: uploadButton ? 'true' : 'false',
    root_found: 'true',
    remove_controls: String(removeControls.length),
    input_file_count: activeInput ? String(activeInput.files.length || 0) : '0',
    input_file_names: activeInput ? [...activeInput.files].map((file) => normalize(file.name)).join(' | ') : '',
  };
}
return {
  uploaded: true,
  alt: normalize(uploaded.getAttribute('alt')),
  src: normalize(uploaded.getAttribute('src')),
  visible_count: String(images.length),
  button_present: uploadButton ? 'true' : 'false',
  root_found: 'true',
  remove_controls: String(removeControls.length),
  input_file_count: activeInput ? String(activeInput.files.length || 0) : '0',
  input_file_names: activeInput ? [...activeInput.files].map((file) => normalize(file.name)).join(' | ') : '',
};
        """
    )
    if not isinstance(payload, dict):
        return {"uploaded": False, "alt": "", "src": ""}
    return {
        "uploaded": bool(payload.get("uploaded")),
        "alt": normalize_whitespace(str(payload.get("alt", "") or "")),
        "src": str(payload.get("src", "") or "").strip(),
        "visible_count": str(payload.get("visible_count", "") or "").strip(),
        "button_present": str(payload.get("button_present", "") or "").strip(),
        "root_found": str(payload.get("root_found", "") or "").strip(),
        "remove_controls": str(payload.get("remove_controls", "") or "").strip(),
        "input_file_count": str(payload.get("input_file_count", "") or "").strip(),
        "input_file_names": normalize_whitespace(str(payload.get("input_file_names", "") or "")),
    }


def _click_vinted_upload_submit(driver: Driver) -> str | None:
    for text in VINTED_UPLOAD_PUBLISH_TEXTS:
        clicked = click_first_matching_text(driver, (text,))
        if clicked:
            return clicked
    clicked = driver.run_js(
        """
const normalize = (value) => (value || '').replace(/\\s+/g, ' ').trim().toLowerCase();
const isVisible = (element) => {
  const style = window.getComputedStyle(element);
  return style.display !== 'none' && style.visibility !== 'hidden' && element.getClientRects().length > 0;
};
const candidates = [...document.querySelectorAll('button, [role="button"]')];
const button = candidates.find((element) => {
  if (!isVisible(element)) return false;
  const text = normalize(element.innerText || element.textContent);
  return text === 'carica' || text.includes('pubblica') || text.includes('metti in vendita');
});
if (!button) return '';
button.click();
return normalize(button.innerText || button.textContent);
        """
    )
    return str(clicked or "").strip() or None


def _build_vinted_upload_error_result(driver: Driver, item: dict, *, error: str) -> dict:
    return {
        "ok": False,
        "prepared": False,
        "submitted": False,
        "title": str(item.get("title", "") or ""),
        "description": str(item.get("description", "") or ""),
        "price": str(item.get("price", "") or ""),
        "photo_paths": list(item.get("photo_paths", []) or []),
        "photos_count": len(list(item.get("photo_paths", []) or [])),
        "current_url": current_page_url(driver),
        "error": error,
    }


def _normalize_upload_item(item: dict) -> dict[str, object]:
    if not isinstance(item, dict):
        raise ValueError("Upload item Vinted non valido.")
    title = normalize_whitespace(str(item.get("title", "") or ""))
    description = normalize_whitespace(str(item.get("description", "") or ""))
    price = normalize_whitespace(str(item.get("price", "") or ""))
    category = normalize_whitespace(str(item.get("category", "") or ""))
    brand = normalize_whitespace(str(item.get("brand", "") or ""))
    condition = normalize_whitespace(str(item.get("condition", "") or ""))
    material = normalize_whitespace(str(item.get("material", "") or ""))
    raw_photo_paths = list(item.get("photo_paths", []) or [])
    photo_paths = [str(Path(path).expanduser().resolve()) for path in raw_photo_paths if str(path or "").strip()]
    if not title:
        raise ValueError("Titolo Vinted mancante.")
    if not description:
        raise ValueError("Descrizione Vinted mancante.")
    if not price:
        raise ValueError("Prezzo Vinted mancante.")
    if not category:
        raise ValueError("Categoria Vinted mancante.")
    if not brand:
        raise ValueError("Brand Vinted mancante.")
    if not condition:
        raise ValueError("Condizione Vinted mancante.")
    if not material:
        raise ValueError("Materiale Vinted mancante.")
    if not photo_paths:
        raise ValueError("Foto Vinted mancanti.")
    for path in photo_paths:
        file_path = Path(path)
        if not file_path.exists() or not file_path.is_file():
            raise ValueError(f"Foto Vinted non trovata: {file_path}")
    return {
        "title": title,
        "description": description,
        "price": price,
        "category": category,
        "brand": brand,
        "condition": condition,
        "material": material,
        "photo_paths": photo_paths,
    }


def _normalized_delay_seconds(value: object, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return max(default, 0.0)
    return max(parsed, 0.0)


def _sleep_if_needed(driver: Driver, seconds: float) -> None:
    if seconds > 0:
        driver.sleep(seconds)
