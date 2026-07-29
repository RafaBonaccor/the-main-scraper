from __future__ import annotations

import subprocess
import sys
import re
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
    openai_used: bool = False,
    openai_model: str = "",
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
            "openai_used": openai_used,
            "openai_model": openai_model,
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
    saved_draft_count = sum(1 for item in results if item.get("saved_draft"))
    failed_count = sum(1 for item in results if not item.get("ok"))
    return {
        "ok": submitted_count > 0 if bool(config.get("submit", False)) else saved_draft_count > 0,
        "items_count": len(items),
        "prepared_count": prepared_count,
        "submitted_count": submitted_count,
        "saved_draft_count": saved_draft_count,
        "failed_count": failed_count,
        "submit": bool(config.get("submit", False)),
        "delay_between_seconds": delay_between_seconds,
        "keep_browser_open": bool(config.get("keep_browser_open", False)),
        "keep_open_seconds": int(config.get("keep_open_seconds", 0) or 0),
        "openai_used": any(bool(item.get("openai_used")) for item in results),
        "openai_model": next((str(item.get("openai_model", "") or "") for item in results if str(item.get("openai_model", "") or "").strip()), ""),
        "results": results,
    }


def _upload_single_vinted_item(driver: Driver, config: dict) -> dict:
    item = _normalize_upload_item(config.get("item", {}))
    steps: list[dict[str, str]] = []
    action_delay_seconds = _normalized_delay_seconds(
        config.get("action_delay_seconds", 1.5),
        default=1.5 if bool(config.get("slow_mode", False)) else 0.0,
    )
    page_settle_seconds = _normalized_delay_seconds(
        config.get("page_settle_seconds", 3.0),
        default=3.0 if bool(config.get("slow_mode", False)) else 0.0,
    )
    _append_upload_step(
        steps,
        "item.normalized",
        f"title={item['title']} | price={item['price']} | category={item['category']} | brand={item['brand']} | condition={item['condition']} | material={item['material']} | photos={len(list(item['photo_paths']))}",
    )

    entry_action, navigated = _open_vinted_sell_page(driver)
    _append_upload_step(steps, "page.opened", f"entry_action={entry_action} | navigated={navigated} | url={current_page_url(driver)}")
    _sleep_if_needed(driver, page_settle_seconds)
    cookie_action = click_first_matching_text(driver, DEFAULT_COOKIE_REJECT_TEXTS) or ""
    if cookie_action:
        _append_upload_step(steps, "cookie.dismissed", cookie_action)
        _sleep_if_needed(driver, min(action_delay_seconds, 0.35))

    access_status = wait_for_vinted_access_status(
        driver,
        max_wait_seconds=min(max(page_settle_seconds, 0.0), 0.8),
    )
    _append_upload_step(
        steps,
        "access.checked",
        f"marker_present={bool(access_status.get('marker_present'))} | page_not_found={bool(access_status.get('page_not_found'))} | url={current_page_url(driver)}",
    )
    emit_vinted_access_signal(access_status)
    access_status = _wait_for_vinted_login_if_needed(
        driver,
        access_status,
        revisit_url=VINTED_UPLOAD_ENTRY_URL,
        action_delay_seconds=action_delay_seconds,
        page_settle_seconds=page_settle_seconds,
    )
    _append_upload_step(
        steps,
        "access.ready",
        f"marker_present={bool(access_status.get('marker_present'))} | page_not_found={bool(access_status.get('page_not_found'))} | url={current_page_url(driver)}",
    )
    _sleep_if_needed(driver, action_delay_seconds)
    _dismiss_vinted_upload_system_error(driver, allow_refresh=True)

    upload_button_action = ""
    upload_mode = ""
    photo_upload_debug: dict[str, str | bool] = {}
    upload_attempts = 0
    upload_errors: list[str] = []
    validated_photo_paths = _validate_vinted_upload_photo_paths(list(item["photo_paths"]))
    _append_upload_step(steps, "photos.validated", " | ".join(validated_photo_paths))
    for attempt in range(1, VINTED_UPLOAD_MAX_UPLOAD_ATTEMPTS + 1):
        upload_attempts = attempt
        existing_photo_debug = _collect_vinted_uploaded_photo_debug(driver)
        _append_upload_step(
            steps,
            "photos.precheck",
            f"attempt={attempt} | uploaded={bool(existing_photo_debug.get('uploaded'))} | debug={_format_upload_debug(existing_photo_debug)}",
        )
        if existing_photo_debug.get("uploaded"):
            photo_upload_debug = existing_photo_debug
            upload_mode = upload_mode or "already-present"
            _append_upload_step(steps, "photos.detected", f"attempt={attempt} | mode={upload_mode} | debug={_format_upload_debug(photo_upload_debug)}")
            break
        _dismiss_vinted_upload_system_error(driver, allow_refresh=True)
        upload_button_action = _click_upload_photos_button(driver) or upload_button_action
        _append_upload_step(steps, "photos.triggered", f"attempt={attempt} | action={upload_button_action or 'none'}")
        upload_mode = ""
        try:
            upload_selector = _wait_for_vinted_photo_input(driver, max_wait_seconds=max(action_delay_seconds, 3.0))
            if upload_selector:
                driver.upload_multiple_files(upload_selector, validated_photo_paths, wait=Wait.LONG)
                upload_mode = "dom"
                _append_upload_step(steps, "photos.dom_upload", f"attempt={attempt} | selector={upload_selector}")
            elif _should_use_macos_finder_upload():
                upload_mode = _upload_vinted_photos_via_macos_finder(driver, validated_photo_paths)
                _append_upload_step(steps, "photos.finder_upload", f"attempt={attempt} | mode={upload_mode}")
            else:
                raise RuntimeError("Input foto Vinted non trovato sulla pagina di caricamento.")
        except Exception as exc:
            upload_errors.append(f"attempt {attempt}: {type(exc).__name__}: {exc}")
            _append_upload_step(steps, "photos.upload_error", f"attempt={attempt} | {type(exc).__name__}: {exc}")
        _sleep_if_needed(driver, max(action_delay_seconds, 1.0))
        _dismiss_vinted_upload_system_error(driver, allow_refresh=True)
        photo_upload_debug = _wait_for_vinted_uploaded_photo(driver, max_wait_seconds=max(page_settle_seconds, 8.0))
        _append_upload_step(
            steps,
            "photos.postcheck",
            f"attempt={attempt} | uploaded={bool(photo_upload_debug.get('uploaded'))} | debug={_format_upload_debug(photo_upload_debug)}",
        )
        if photo_upload_debug.get("uploaded"):
            _append_upload_step(steps, "photos.detected", f"attempt={attempt} | mode={upload_mode or 'unknown'} | debug={_format_upload_debug(photo_upload_debug)}")
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
    _append_upload_step(steps, "field.title", f"ok={title_filled} | value={item['title']}")
    description_filled = _fill_textarea_input(driver, '#description[data-testid="description--input"]', str(item["description"]))
    _append_upload_step(steps, "field.description", f"ok={description_filled} | value={item['description']}")
    category_selected = _select_vinted_picker_option(
        driver,
        'input#category[data-testid="catalog-select-dropdown-input"][name="category"]',
        str(item["category"]),
        list_selector='ul[data-testid="category-list"]',
        option_title_selector='.web_ui__Cell__title',
        clickable_option_selector='[role="button"]',
    )
    _append_upload_step(steps, "field.category", f"ok={category_selected} | value={item['category']}")
    brand_selected = _select_vinted_picker_option(
        driver,
        'input#brand[data-testid="brand-select-dropdown-input"][name="brand"]',
        str(item["brand"]),
    )
    _append_upload_step(steps, "field.brand", f"ok={brand_selected} | value={item['brand']}")
    condition_selected = _select_vinted_picker_option(
        driver,
        'input#condition[data-testid="category-condition-single-list-input"][name="condition"]',
        str(item["condition"]),
    )
    _append_upload_step(steps, "field.condition", f"ok={condition_selected} | value={item['condition']}")
    material_selected = _select_vinted_picker_option(
        driver,
        'input#material[data-testid="category-material-multi-list-input"][name="material"]',
        str(item["material"]),
    )
    _append_upload_step(steps, "field.material", f"ok={material_selected} | value={item['material']}")
    price_filled = _fill_vinted_price_input(driver, '#price[data-testid="price-input--input"]', str(item["price"]))
    _append_upload_step(steps, "field.price", f"ok={price_filled} | value={item['price']}")
    field_debug = _collect_vinted_upload_field_debug(driver)
    _append_upload_step(
        steps,
        "fields.snapshot",
        " | ".join(f"{key}={value}" for key, value in field_debug.items()),
    )
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
    _append_upload_step(steps, "fields.ready", "all required fields compiled")

    submit_action = ""
    save_draft_action = ""
    submitted = False
    saved_draft = False
    if bool(config.get("submit", False)):
        _sleep_if_needed(driver, max(action_delay_seconds, 1.0))
        submit_action = _click_vinted_upload_submit(driver) or ""
        submitted = bool(submit_action)
        _append_upload_step(steps, "submit.click", f"ok={submitted} | action={submit_action or 'none'}")
        if not submitted:
            raise RuntimeError("Pulsante finale di pubblicazione Vinted non trovato o non cliccabile.")
        _sleep_if_needed(driver, max(action_delay_seconds, 1.5))
        close_tab_action = _close_vinted_active_tab_after_completion()
        _append_upload_step(steps, "tab.close", f"mode=submit | action={close_tab_action}")
    else:
        _sleep_if_needed(driver, 3.0)
        save_draft_action, save_draft_debug = _click_vinted_upload_save_draft(
            driver,
            max_wait_seconds=12.0,
            action_delay_seconds=max(action_delay_seconds, 1.0),
        )
        save_draft_action = save_draft_action or ""
        saved_draft = bool(save_draft_action)
        _append_upload_step(
            steps,
            "draft.click",
            f"ok={saved_draft} | action={save_draft_action or 'none'} | debug={save_draft_debug or 'none'}",
        )
        if not saved_draft:
            raise RuntimeError("Pulsante 'Salva bozza' non trovato o non cliccabile.")
        _sleep_if_needed(driver, max(action_delay_seconds, 1.2))
        close_tab_action = _close_vinted_active_tab_after_completion()
        _append_upload_step(steps, "tab.close", f"mode=draft | action={close_tab_action}")

    return {
        "ok": submitted if bool(config.get("submit", False)) else saved_draft,
        "prepared": prepared,
        "submitted": submitted,
        "saved_draft": saved_draft,
        "title": str(item["title"]),
        "description": str(item["description"]),
        "price": str(item["price"]),
        "category": str(item["category"]),
        "brand": str(item["brand"]),
        "condition": str(item["condition"]),
        "material": str(item["material"]),
        "photo_paths": list(item["photo_paths"]),
        "openai_used": bool(item.get("openai_used", False)),
        "openai_model": str(item.get("openai_model", "") or ""),
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
        "save_draft_action": save_draft_action,
        "login_required": not bool(access_status.get("marker_present")),
        "submitted_at": datetime.now().isoformat(timespec="seconds") if submitted else "",
        "keep_browser_open": bool(config.get("keep_browser_open", False)),
        "keep_open_seconds": int(config.get("keep_open_seconds", 0) or 0),
        "steps": steps,
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


def _close_vinted_active_tab_after_completion() -> str:
    script = """
tell application "Google Chrome" to activate
tell application "System Events"
    tell process "Google Chrome"
        keystroke "w" using {command down}
        delay 0.2
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
        return f"cmd+w-failed:{stderr or stdout or 'unknown error'}"
    return "cmd+w"


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


def _fill_vinted_price_input(driver: Driver, selector: str, value: str) -> bool:
    normalized_value = _normalize_vinted_price_input(value)
    if not normalized_value:
        return False
    attempts = []
    for candidate in (
        _humanize_vinted_price_input(value),
        _humanize_vinted_price_input(value).replace(".", ","),
        _format_vinted_price_candidate(normalized_value, decimals=2, decimal_separator="."),
        normalized_value,
    ):
        candidate_text = normalize_whitespace(str(candidate or ""))
        if candidate_text and candidate_text not in attempts:
            attempts.append(candidate_text)
    for index, candidate in enumerate(attempts, start=1):
        typed_ok = False
        try:
            driver.click(selector)
            driver.clear(selector)
            driver.type(selector, candidate)
            typed_ok = True
        except Exception as exc:
            print(
                f"[vinted-upload] price typing failed attempt={index} candidate={candidate!r} error={type(exc).__name__}: {exc}",
                flush=True,
            )
            continue
        try:
            driver.run_js(
                """
const element = document.querySelector(args.selector);
if (!element) return false;
try { element.blur(); } catch {}
try { element.dispatchEvent(new Event('change', { bubbles: true })); } catch {}
return true;
                """,
                {"selector": selector},
            )
        except Exception:
            pass
        driver.sleep(0.35)
        current_value = str(
            driver.run_js(
                """
const element = document.querySelector(args.selector);
if (!element) return '';
return String(element.value || element.getAttribute('value') || '').trim();
                """,
                {"selector": selector},
            )
            or ""
        ).strip()
        print(
            f"[vinted-upload] price attempt={index} candidate={candidate!r} typed={typed_ok!r} current={current_value!r}",
            flush=True,
        )
        if _vinted_price_matches_expected(driver, selector, candidate):
            return True
    return _vinted_price_matches_expected(driver, selector, normalized_value)


def _normalize_vinted_price_input(value: object) -> str:
    text = normalize_whitespace(str(value or ""))
    if not text:
        return ""
    cleaned = re.sub(r"(?i)\b(eur|euro|€)\b", "", text)
    cleaned = re.sub(r"[^0-9,.-]+", "", cleaned).strip()
    if not cleaned:
        return ""
    if "," in cleaned and "." in cleaned:
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    else:
        cleaned = cleaned.replace(",", ".")
    try:
        number = float(cleaned)
    except ValueError:
        return cleaned
    if abs(number - round(number)) < 0.0001:
        return str(int(round(number)))
    return f"{number:.2f}".rstrip("0").rstrip(".")


def _humanize_vinted_price_input(value: object) -> str:
    text = normalize_whitespace(str(value or ""))
    if not text:
        return ""
    cleaned = re.sub(r"(?i)\b(eur|euro|€)\b", "", text)
    cleaned = re.sub(r"[^0-9,.-]+", "", cleaned).strip()
    if not cleaned:
        return ""
    if "," in cleaned and "." in cleaned:
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    else:
        cleaned = cleaned.replace(",", ".")
    try:
        number = float(cleaned)
    except ValueError:
        return cleaned
    return f"{number:.2f}"


def _format_vinted_price_candidate(value: object, *, decimals: int = 2, decimal_separator: str = ",") -> str:
    normalized = _normalize_vinted_price_input(value)
    if not normalized:
        return ""
    try:
        number = float(normalized)
    except ValueError:
        return normalized
    formatted = f"{number:.{max(decimals, 0)}f}"
    if decimal_separator == ",":
        formatted = formatted.replace(".", ",")
    return formatted


def _vinted_price_matches_expected(driver: Driver, selector: str, expected_value: str) -> bool:
    return bool(
        driver.run_js(
            """
const element = document.querySelector(args.selector);
if (!element) return false;
const current = String(element.value || element.getAttribute('value') || '').trim();
const target = String(args.value || '').trim();
const parse = (value) => {
  const normalized = String(value || '')
    .replace(/[^0-9,.-]+/g, '')
    .replace(',', '.');
  const parsed = Number(normalized);
  return Number.isFinite(parsed) ? parsed : null;
};
if (current === target) return true;
const currentNumber = parse(current);
const targetNumber = parse(target);
if (currentNumber === null || targetNumber === null) return false;
return Math.abs(currentNumber - targetNumber) < 0.0001;
            """,
            {"selector": selector, "value": expected_value},
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
const nativeSetter =
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set ||
  Object.getOwnPropertyDescriptor(Object.getPrototypeOf(searchInput), 'value')?.set;
searchInput.focus();
if (nativeSetter) {
  nativeSetter.call(searchInput, target);
} else {
  searchInput.value = target;
}
searchInput.dispatchEvent(new Event('input', { bubbles: true }));
searchInput.dispatchEvent(new Event('change', { bubbles: true }));
searchInput.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true, key: 't' }));
searchInput.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, key: 'Enter' }));
searchInput.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true, key: 'Enter' }));
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
const isVisible = (element) => {
  if (!element) return false;
  const style = window.getComputedStyle(element);
  return style.display !== 'none' && style.visibility !== 'hidden' && element.getClientRects().length > 0;
};
const uploadSection =
  document.querySelector('[data-testid="media-upload-grid"]')?.closest('section, div') ||
  document.querySelector('[data-testid="add-photos-icon-button"]')?.closest('section, div') ||
  [...document.querySelectorAll('section, div')].find((element) => {
    if (!isVisible(element)) return false;
    const text = normalize(element.innerText || element.textContent || '').toLowerCase();
    return (
      text.includes('carica le foto') ||
      text.includes('aggiungi foto') ||
      text.includes('clicca e trascina per modificare l\\'ordine delle foto')
    );
  }) ||
  null;
const isLikelyUploadedImage = (image) => {
  if (!image) return false;
  const alt = normalize(image.getAttribute('alt')).toLowerCase();
  const src = normalize(image.getAttribute('src'));
  return (
    alt.includes('foto 1 di') ||
    alt.includes('foto 2 di') ||
    alt.includes('foto 3 di') ||
    alt.includes('foto ') ||
    alt.includes('caricata per ridisporre le foto') ||
    alt.includes('uploaded') ||
    src.startsWith('blob:')
  );
};
const scopedRoot = uploadSection || document;
const allVisibleImages = [...scopedRoot.querySelectorAll('img.web_ui__Image__content, img')].filter((image) => isVisible(image));
const uploadedImagesAcrossPage = allVisibleImages.filter((image) => isLikelyUploadedImage(image));
const uploadGrid = scopedRoot.querySelector('[data-testid="media-upload-grid"]') || document.querySelector('[data-testid="media-upload-grid"]');
const gridImages = uploadGrid
  ? [...uploadGrid.querySelectorAll('img.web_ui__Image__content, img')].filter((image) => isVisible(image))
  : [];
const gridUploadedImages = gridImages.filter((image) => isLikelyUploadedImage(image));
const deleteButtonsAcrossPage = [...scopedRoot.querySelectorAll('[data-testid^="media-select-grid-delete-button-"]')].filter(isVisible);
const rotateButtonsAcrossPage = [...scopedRoot.querySelectorAll('[data-testid^="media-select-grid-rotate-button-"]')].filter(isVisible);
const mainBadgesAcrossPage = [...scopedRoot.querySelectorAll('[data-testid^="media-select-main-badge-"]')].filter(isVisible);
const pageHasUploadedPhotoState =
  gridUploadedImages.length > 0 ||
  uploadedImagesAcrossPage.length > 0 ||
  deleteButtonsAcrossPage.length > 0 ||
  rotateButtonsAcrossPage.length > 0 ||
  mainBadgesAcrossPage.length > 0;
const mediaRootCandidates = [
  uploadGrid,
  uploadSection,
  document.querySelector('div[class*="media-select__input"]'),
  document.querySelector('div[class*="_media-select-module"]'),
  document.querySelector('[data-testid="plus"]')?.closest('div'),
  [...document.querySelectorAll('section, div')].find((element) => {
    if (!isVisible(element)) return false;
    const text = normalize(element.innerText || element.textContent || '').toLowerCase();
    if (!text.includes('foto') && !text.includes('photo') && !text.includes('carica le foto') && !text.includes('upload photos')) {
      return false;
    }
    return !!element.querySelector('img, input[type="file"], button, [role="button"], svg');
  }),
  [...document.querySelectorAll('img')].find((image) => isVisible(image))?.closest('section, div'),
].filter(Boolean);
const mediaRoot = mediaRootCandidates.find((element) => isVisible(element)) || null;
if (!mediaRoot) {
  const fileInputs = [...scopedRoot.querySelectorAll('input[type="file"]'), ...document.querySelectorAll('input[type="file"]')];
  const activeInput = fileInputs.find((input) => (input.files && input.files.length > 0)) || null;
  const visibleImages = uploadedImagesAcrossPage;
  return {
    uploaded: !!activeInput || visibleImages.length > 0 || pageHasUploadedPhotoState,
    alt: '',
    src: '',
    visible_count: String(visibleImages.length),
    button_present: document.querySelector('[data-testid="add-photos-icon-button"]') ? 'true' : 'false',
    root_found: 'false',
    grid_visible_count: String(gridUploadedImages.length),
    grid_delete_controls: String(deleteButtonsAcrossPage.length),
    grid_rotate_controls: String(rotateButtonsAcrossPage.length),
    main_badges: String(mainBadgesAcrossPage.length),
    input_file_count: activeInput ? String(activeInput.files.length || 0) : '0',
    input_file_names: activeInput ? [...activeInput.files].map((file) => normalize(file.name)).join(' | ') : '',
  };
}
const images = [...mediaRoot.querySelectorAll('img.web_ui__Image__content, img')].filter((image) => isVisible(image));
const uploadedImages = images.filter((image) => isLikelyUploadedImage(image));
const fileInputs = [...mediaRoot.querySelectorAll('input[type="file"]')];
const activeInput = fileInputs.find((input) => (input.files && input.files.length > 0))
  || [...document.querySelectorAll('input[type="file"]')].find((input) => (input.files && input.files.length > 0))
  || null;
const removeControls = [...mediaRoot.querySelectorAll('button, [role="button"], div, span')].filter((element) => {
  if (!isVisible(element)) return false;
  const text = normalize(element.innerText || element.textContent || element.getAttribute('aria-label') || '').toLowerCase();
  const testId = normalize(element.getAttribute('data-testid') || '').toLowerCase();
  return (
    text.includes('copertina') ||
    text.includes('rimuovi') ||
    text.includes('remove') ||
    text === 'x' ||
    testId.includes('close') ||
    testId.includes('remove')
  );
});
const uploaded = uploadedImages[0] || gridUploadedImages[0] || uploadedImagesAcrossPage[0] || null;
const uploadButton = [...mediaRoot.querySelectorAll('button, [role="button"]')].find((element) =>
  normalize(element.innerText || element.textContent).toLowerCase().includes('carica le foto')
);
if (!uploaded) {
  const uploadedByState =
    !!activeInput ||
    uploadedImages.length > 0 ||
    gridUploadedImages.length > 0 ||
    uploadedImagesAcrossPage.length > 0 ||
    removeControls.length > 0 ||
    deleteButtonsAcrossPage.length > 0 ||
    rotateButtonsAcrossPage.length > 0 ||
    mainBadgesAcrossPage.length > 0 ||
    (!uploadButton && images.length > 0);
  return {
    uploaded: uploadedByState,
    alt: '',
    src: '',
    visible_count: String(images.length),
    button_present: uploadButton ? 'true' : 'false',
    root_found: 'true',
    remove_controls: String(removeControls.length),
    grid_visible_count: String(gridUploadedImages.length),
    grid_delete_controls: String(deleteButtonsAcrossPage.length),
    grid_rotate_controls: String(rotateButtonsAcrossPage.length),
    main_badges: String(mainBadgesAcrossPage.length),
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
  grid_visible_count: String(gridUploadedImages.length),
  grid_delete_controls: String(deleteButtonsAcrossPage.length),
  grid_rotate_controls: String(rotateButtonsAcrossPage.length),
  main_badges: String(mainBadgesAcrossPage.length),
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
        "grid_visible_count": str(payload.get("grid_visible_count", "") or "").strip(),
        "grid_delete_controls": str(payload.get("grid_delete_controls", "") or "").strip(),
        "grid_rotate_controls": str(payload.get("grid_rotate_controls", "") or "").strip(),
        "main_badges": str(payload.get("main_badges", "") or "").strip(),
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


def _read_vinted_upload_save_draft_state(driver: Driver) -> dict[str, str | bool]:
    payload = driver.run_js(
        """
const normalize = (value) => (value || '').replace(/\\s+/g, ' ').trim().toLowerCase();
const isVisible = (element) => {
  const style = window.getComputedStyle(element);
  return style.display !== 'none' && style.visibility !== 'hidden' && element.getClientRects().length > 0;
};
const candidates = [
  ...document.querySelectorAll('[data-testid="upload-form-save-draft-button"], button, [role="button"]'),
];
const bodyText = normalize(document.body ? (document.body.innerText || document.body.textContent || '') : '');
const button = candidates.find((element) => {
  if (!isVisible(element)) return false;
  const testId = normalize(element.getAttribute('data-testid') || '');
  if (testId === 'upload-form-save-draft-button') return true;
  const text = normalize(element.innerText || element.textContent);
  return text === 'salva bozza' || text.includes('salva bozza');
});
if (!button) {
  return {
    found: false,
    visible: false,
    enabled: false,
    text: '',
    test_id: '',
    aria_disabled: '',
    disabled_attr: '',
    class_name: '',
    body_text: '',
    page_title: '',
  };
}
  return {
    found: true,
    visible: isVisible(button),
    enabled: !button.disabled && normalize(button.getAttribute('aria-disabled') || '') !== 'true',
    text: normalize(button.innerText || button.textContent),
  test_id: normalize(button.getAttribute('data-testid') || ''),
  aria_disabled: normalize(button.getAttribute('aria-disabled') || ''),
  disabled_attr: button.disabled ? 'true' : '',
    class_name: normalize(button.className || ''),
    body_text: bodyText,
    page_title: normalize(document.title || ''),
    page_url: normalize(window.location.href || ''),
  };
        """
    )
    if not isinstance(payload, dict):
        return {}
    return {
        "found": bool(payload.get("found")),
        "visible": bool(payload.get("visible")),
        "enabled": bool(payload.get("enabled")),
        "text": normalize_whitespace(str(payload.get("text", "") or "")),
        "test_id": normalize_whitespace(str(payload.get("test_id", "") or "")),
        "aria_disabled": normalize_whitespace(str(payload.get("aria_disabled", "") or "")),
        "disabled_attr": normalize_whitespace(str(payload.get("disabled_attr", "") or "")),
        "class_name": normalize_whitespace(str(payload.get("class_name", "") or "")),
        "body_text": normalize_whitespace(str(payload.get("body_text", "") or "")),
        "page_title": normalize_whitespace(str(payload.get("page_title", "") or "")),
        "page_url": normalize_whitespace(str(payload.get("page_url", "") or "")),
    }


def _click_vinted_upload_save_draft(
    driver: Driver,
    *,
    max_wait_seconds: float = 12.0,
    action_delay_seconds: float = 1.5,
) -> tuple[str | None, str]:
    attempts = min(3, max(2, int(max_wait_seconds / 2.0)))
    last_state: dict[str, str | bool] = {}
    for attempt in range(1, attempts + 1):
        last_state = _read_vinted_upload_save_draft_state(driver)
        print(
            f"[vinted-upload] draft attempt={attempt} before={_format_upload_debug(last_state)}",
            flush=True,
        )
        if not last_state.get("found"):
            driver.sleep(1.0)
            continue
        if not last_state.get("enabled"):
            driver.sleep(1.0)
            continue
        clicked = ""
        click_errors: list[str] = []
        try:
            driver.click('[data-testid="upload-form-save-draft-button"]', wait=Wait.SHORT)
            clicked = "driver.click"
        except Exception as exc:
            click_errors.append(f"driver.click:{type(exc).__name__}")
        if not clicked:
            clicked = driver.run_js(
                """
const normalize = (value) => (value || '').replace(/\\s+/g, ' ').trim().toLowerCase();
const isVisible = (element) => {
  const style = window.getComputedStyle(element);
  return style.display !== 'none' && style.visibility !== 'hidden' && element.getClientRects().length > 0;
};
const allCandidates = [
  ...document.querySelectorAll('[data-testid="upload-form-save-draft-button"], button, [role="button"]'),
];
const button = allCandidates.find((element) => {
  if (!isVisible(element)) return false;
  const testId = normalize(element.getAttribute('data-testid') || '');
  if (testId === 'upload-form-save-draft-button') return true;
  const text = normalize(element.innerText || element.textContent);
  return text === 'salva bozza' || text.includes('salva bozza');
});
if (!button) return '';
if (button.disabled || normalize(button.getAttribute('aria-disabled') || '') === 'true') return '';
try { button.scrollIntoView({ block: 'center' }); } catch {}
try { button.focus(); } catch {}
try { button.click(); } catch {}
const rect = button.getBoundingClientRect();
if (rect && rect.width > 0 && rect.height > 0) {
  const centerX = rect.left + rect.width / 2;
  const centerY = rect.top + rect.height / 2;
  const pointTarget = document.elementFromPoint(centerX, centerY);
  if (pointTarget) {
    try { pointTarget.scrollIntoView({ block: 'center' }); } catch {}
    try { pointTarget.focus(); } catch {}
    try { pointTarget.click(); } catch {}
    for (const type of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
      try {
        pointTarget.dispatchEvent(new MouseEvent(type, {
          bubbles: true,
          cancelable: true,
          clientX: centerX,
          clientY: centerY,
          view: window,
        }));
      } catch {}
    }
  }
}
return normalize(button.innerText || button.textContent) || 'salva bozza';
                """
            )
        if not clicked:
            click_errors.append("js-click")
        if clicked:
            print(
                f"[vinted-upload] draft click trigger={clicked} errors={' | '.join(click_errors) or 'none'}",
                flush=True,
            )
        clicked_text = str(clicked or "").strip() or None
        print(
            f"[vinted-upload] draft attempt={attempt} clicked_target={clicked_text or 'none'}",
            flush=True,
        )
        stable_saved = False
        observed_state: dict[str, str | bool] = last_state
        post_click_waits = max(3, int(max(action_delay_seconds, 1.0) * 2))
        for tick in range(1, post_click_waits + 1):
            driver.sleep(0.5)
            observed_state = _read_vinted_upload_save_draft_state(driver)
            print(
                f"[vinted-upload] draft attempt={attempt} tick={tick} after={_format_upload_debug(observed_state)}",
                flush=True,
            )
            body_text = str(observed_state.get("body_text", "") or "").lower()
            page_title = str(observed_state.get("page_title", "") or "").lower()
            success_markers = (
                "bozza salvata",
                "draft saved",
                "saved draft",
                "bozza creata",
                "salvataggio completato",
            )
            if (
                not observed_state.get("found")
                or not observed_state.get("visible")
                or not observed_state.get("enabled")
                or observed_state.get("class_name") != last_state.get("class_name")
                or observed_state.get("text") != last_state.get("text")
                or observed_state.get("page_url") != last_state.get("page_url")
                or any(marker in body_text for marker in success_markers)
                or any(marker in page_title for marker in success_markers)
            ):
                stable_saved = True
                break
        debug = (
            f"attempt={attempt} | before={_format_upload_debug(last_state)} | "
            f"after={_format_upload_debug(observed_state)}"
        )
        if clicked_text and stable_saved:
            print(f"[vinted-upload] draft saved attempt={attempt}", flush=True)
            return clicked_text, debug
        driver.sleep(1.0)
    print(f"[vinted-upload] draft not saved last_state={_format_upload_debug(last_state)}", flush=True)
    return None, f"attempts_exhausted | last_state={_format_upload_debug(last_state)}"


def _build_vinted_upload_error_result(driver: Driver, item: dict, *, error: str) -> dict:
    return {
        "ok": False,
        "prepared": False,
        "submitted": False,
        "title": str(item.get("title", "") or ""),
        "description": str(item.get("description", "") or ""),
        "price": str(item.get("price", "") or ""),
        "photo_paths": list(item.get("photo_paths", []) or []),
        "openai_used": bool(item.get("openai_used", False)),
        "openai_model": str(item.get("openai_model", "") or ""),
        "photos_count": len(list(item.get("photo_paths", []) or [])),
        "current_url": current_page_url(driver),
        "error": error,
        "steps": [
            {
                "at": datetime.now().isoformat(timespec="seconds"),
                "stage": "error.result",
                "message": error,
            }
        ],
    }


def _append_upload_step(steps: list[dict[str, str]], stage: str, message: str) -> None:
    steps.append(
        {
            "at": datetime.now().isoformat(timespec="seconds"),
            "stage": normalize_whitespace(stage),
            "message": normalize_whitespace(message),
        }
    )


def _format_upload_debug(payload: dict[str, str | bool]) -> str:
    if not isinstance(payload, dict):
        return ""
    ordered_keys = (
        "uploaded",
        "alt",
        "src",
        "visible_count",
        "grid_visible_count",
        "grid_delete_controls",
        "grid_rotate_controls",
        "main_badges",
        "button_present",
        "root_found",
        "remove_controls",
        "input_file_count",
        "input_file_names",
    )
    parts: list[str] = []
    for key in ordered_keys:
        if key not in payload:
            continue
        value = normalize_whitespace(str(payload.get(key, "") or ""))
        if not value:
            continue
        parts.append(f"{key}={value}")
    return " | ".join(parts)


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
        "openai_used": bool(item.get("openai_used", False)),
        "openai_model": normalize_whitespace(str(item.get("openai_model", "") or "")),
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
