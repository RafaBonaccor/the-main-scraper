import json
from pathlib import Path

from botasaurus.browser import Driver, Wait, browser

from ..browser_helpers import click_first_matching_text, current_page_url, navigate_with_retries
from ..contact_history import record_contact_result, record_contact_results
from ..browser_runtime import normalize_browser_mode, resolve_browser_arguments, resolve_browser_profile


SUBITO_COOKIE_REJECT_TEXTS = (
    "Continua senza accettare",
)

SUBITO_CONTACT_BUTTON_TEXTS = (
    "Contatta",
    "Contatta subito",
    "Invia messaggio",
    "Chat",
)

SUBITO_ATTACHMENT_BUTTON_TEXTS = (
    "Aggiungi allegato",
    "Aggiungi un allegato",
    "Allega file",
    "Carica allegato",
)

SUBITO_SEND_BUTTON_TEXTS = (
    "Invia",
    "Invia messaggio",
    "Invia candidatura",
    "Manda messaggio",
)
SUBITO_PERSISTENT_PROFILE_NAME = "Subito"


def run_subito_contact_action(
    link: str,
    attachment: str = "",
    message: str = "",
    submit: bool = False,
    keep_open_seconds: int = 120,
    login_wait_seconds: int = 240,
    slow_mode: bool = False,
    action_delay_seconds: float = 1.5,
    page_settle_seconds: float = 3.0,
    browser_mode: str = "sessione_persistente",
    browser_user_data_dir: str = "",
    browser_profile_directory: str = "Default",
) -> dict:
    clean_link = str(link or "").strip()
    if not clean_link:
        raise ValueError("Subito contact action requires a listing link.")
    browser_profile_directory = _normalize_subito_profile_directory(browser_mode, browser_profile_directory)

    attachment_path = Path(attachment).expanduser().resolve() if attachment else None
    if attachment_path and not attachment_path.exists():
        raise ValueError(f"Attachment file not found: {attachment_path}")

    config = {
        "link": clean_link,
        "attachment": str(attachment_path) if attachment_path else "",
        "message": str(message or "").strip(),
        "submit": bool(submit),
        "keep_open_seconds": max(int(keep_open_seconds), 0),
        "login_wait_seconds": max(int(login_wait_seconds), 0),
        "slow_mode": bool(slow_mode),
        "action_delay_seconds": float(action_delay_seconds),
        "page_settle_seconds": float(page_settle_seconds),
        "browser_mode": browser_mode,
        "browser_user_data_dir": browser_user_data_dir,
        "browser_profile_directory": browser_profile_directory,
    }
    return _run_subito_contact_task(config)


def run_subito_bulk_contact_action(
    links: list[str],
    attachment: str = "",
    message: str = "",
    submit: bool = False,
    delay_between_seconds: int = 2,
    keep_open_seconds: int = 120,
    login_wait_seconds: int = 240,
    slow_mode: bool = False,
    action_delay_seconds: float = 1.5,
    page_settle_seconds: float = 3.0,
    browser_mode: str = "sessione_persistente",
    browser_user_data_dir: str = "",
    browser_profile_directory: str = "Default",
) -> dict:
    clean_links = [str(link).strip() for link in links if str(link).strip()]
    if not clean_links:
        raise ValueError("Subito bulk contact action requires at least one listing link.")
    browser_profile_directory = _normalize_subito_profile_directory(browser_mode, browser_profile_directory)

    attachment_path = Path(attachment).expanduser().resolve() if attachment else None
    if attachment_path and not attachment_path.exists():
        raise ValueError(f"Attachment file not found: {attachment_path}")

    config = {
        "links": clean_links,
        "attachment": str(attachment_path) if attachment_path else "",
        "message": str(message or "").strip(),
        "submit": bool(submit),
        "delay_between_seconds": max(int(delay_between_seconds), 0),
        "keep_open_seconds": max(int(keep_open_seconds), 0),
        "login_wait_seconds": max(int(login_wait_seconds), 0),
        "slow_mode": bool(slow_mode),
        "action_delay_seconds": float(action_delay_seconds),
        "page_settle_seconds": float(page_settle_seconds),
        "browser_mode": browser_mode,
        "browser_user_data_dir": browser_user_data_dir,
        "browser_profile_directory": browser_profile_directory,
    }
    return _run_subito_bulk_contact_task(config)


def _normalize_subito_profile_directory(browser_mode: str, browser_profile_directory: str) -> str:
    normalized_mode = normalize_browser_mode(browser_mode)
    clean_value = str(browser_profile_directory or "").strip()
    if normalized_mode == "sessione_persistente" and clean_value in {"", "Default"}:
        return SUBITO_PERSISTENT_PROFILE_NAME
    return clean_value or "Default"


@browser(profile=resolve_browser_profile, add_arguments=resolve_browser_arguments)
def _run_subito_contact_task(driver: Driver, config: dict) -> dict:
    link = config["link"]
    keep_open_seconds = max(int(config.get("keep_open_seconds", 120)), 0)
    result = _contact_single_listing(driver, config)
    record_contact_result(result, source="subito")

    if keep_open_seconds > 0:
        driver.sleep(keep_open_seconds)

    result["keep_open_seconds"] = keep_open_seconds
    return result


@browser(profile=resolve_browser_profile, add_arguments=resolve_browser_arguments)
def _run_subito_bulk_contact_task(driver: Driver, config: dict) -> dict:
    links = list(config.get("links", []) or [])
    delay_between_seconds = max(int(config.get("delay_between_seconds", 2)), 0)
    if bool(config.get("slow_mode", False)):
        delay_between_seconds = max(delay_between_seconds, 6)
    keep_open_seconds = max(int(config.get("keep_open_seconds", 120)), 0)

    results = []
    for index, link in enumerate(links):
        result = _contact_single_listing(driver, {**config, "link": link})
        results.append(result)
        if index < len(links) - 1 and delay_between_seconds > 0:
            driver.sleep(delay_between_seconds)

    record_contact_results(results, source="subito")

    if keep_open_seconds > 0:
        driver.sleep(keep_open_seconds)

    prepared_count = sum(1 for item in results if item.get("prepared"))
    sent_count = sum(1 for item in results if item.get("submitted"))
    return {
        "ok": sent_count > 0 if bool(config.get("submit", False)) else prepared_count > 0,
        "links_count": len(links),
        "prepared_count": prepared_count,
        "sent_count": sent_count,
        "failed_count": len(links) - prepared_count,
        "attachment_path": str(config.get("attachment", "") or ""),
        "message": str(config.get("message", "") or ""),
        "submit": bool(config.get("submit", False)),
        "delay_between_seconds": delay_between_seconds,
        "keep_open_seconds": keep_open_seconds,
        "results": results,
    }


def _contact_single_listing(driver: Driver, config: dict) -> dict:
    link = str(config["link"] or "").strip()
    attachment = str(config.get("attachment", "") or "").strip()
    message = str(config.get("message", "") or "").strip()
    submit = bool(config.get("submit", False))
    login_wait_seconds = max(int(config.get("login_wait_seconds", 240)), 0)
    slow_mode = bool(config.get("slow_mode", False))
    action_delay_seconds = _normalized_delay_seconds(config.get("action_delay_seconds", 1.5), default=1.5 if slow_mode else 0.0)
    page_settle_seconds = _normalized_delay_seconds(config.get("page_settle_seconds", 3.0), default=3.0 if slow_mode else 0.0)

    navigated = navigate_with_retries(driver, link, wait=Wait.LONG)
    _sleep_if_needed(driver, page_settle_seconds)
    cookie_banner_action = click_first_matching_text(driver, SUBITO_COOKIE_REJECT_TEXTS)
    _sleep_if_needed(driver, action_delay_seconds)
    driver.select("body", wait=Wait.VERY_LONG)
    _sleep_if_needed(driver, action_delay_seconds)

    contact_button_action = click_first_matching_text(driver, SUBITO_CONTACT_BUTTON_TEXTS)
    if not contact_button_action:
        return {
            "ok": False,
            "prepared": False,
            "submitted": False,
            "link": link,
            "navigated": navigated,
            "current_url": current_page_url(driver),
            "cookie_banner_action": cookie_banner_action,
            "contact_button_action": "",
            "attachment_button_action": "",
            "attachment_uploaded": False,
            "message_filled": False,
            "login_required": _has_login_gate(driver),
            "send_button_action": "",
            "attachment_path": attachment,
        }

    driver.sleep(max(1.5, action_delay_seconds))
    if _has_login_gate(driver):
        if _wait_for_login_completion(driver, login_wait_seconds):
            driver.sleep(max(1.5, action_delay_seconds))
            if not _has_message_field(driver):
                click_first_matching_text(driver, SUBITO_CONTACT_BUTTON_TEXTS)
                driver.sleep(max(1.5, action_delay_seconds))
        else:
            return {
                "ok": False,
                "prepared": False,
                "submitted": False,
                "link": link,
                "navigated": navigated,
                "current_url": current_page_url(driver),
                "cookie_banner_action": cookie_banner_action,
                "contact_button_action": contact_button_action,
                "attachment_button_action": "",
                "attachment_uploaded": False,
                "message_filled": False,
                "login_required": True,
                "login_wait_seconds": login_wait_seconds,
                "send_button_action": "",
                "attachment_path": attachment,
            }

    if _has_login_gate(driver):
        return {
            "ok": False,
            "prepared": False,
            "submitted": False,
            "link": link,
            "navigated": navigated,
            "current_url": current_page_url(driver),
            "cookie_banner_action": cookie_banner_action,
            "contact_button_action": contact_button_action,
            "attachment_button_action": "",
            "attachment_uploaded": False,
            "message_filled": False,
            "login_required": True,
            "login_wait_seconds": login_wait_seconds,
            "send_button_action": "",
            "attachment_path": attachment,
        }

    message_filled = _fill_message_field(driver, message) if message else False
    attachment_button_action = ""
    attachment_uploaded = False

    if attachment:
        attachment_button_action = click_first_matching_text(driver, SUBITO_ATTACHMENT_BUTTON_TEXTS) or ""
        driver.sleep(max(1.0, action_delay_seconds))
        driver.run_js(
            """
const input = [...document.querySelectorAll("input[type='file']")].find((element) => !element.disabled);
if (!input) {
  return false;
}
input.style.display = "block";
input.style.visibility = "visible";
input.style.opacity = "1";
return true;
            """,
        )
        driver.select("input[type='file']", wait=Wait.LONG)
        driver.upload_file("input[type='file']", attachment, wait=Wait.LONG)
        attachment_uploaded = True
        driver.sleep(max(1.5, action_delay_seconds))

    send_button_action = ""
    submitted = False
    if submit:
        _sleep_if_needed(driver, action_delay_seconds)
        send_button_action = click_first_matching_text(driver, SUBITO_SEND_BUTTON_TEXTS) or _click_submit_button(driver) or ""
        submitted = bool(send_button_action)
        if submitted:
            driver.sleep(max(1.5, action_delay_seconds))

    return {
        "ok": submitted if submit else True,
        "prepared": True,
        "submitted": submitted,
        "link": link,
        "navigated": navigated,
        "current_url": current_page_url(driver),
        "cookie_banner_action": cookie_banner_action,
        "contact_button_action": contact_button_action,
        "attachment_button_action": attachment_button_action,
        "attachment_uploaded": attachment_uploaded,
        "message_filled": message_filled,
        "login_required": False,
        "login_wait_seconds": login_wait_seconds,
        "send_button_action": send_button_action,
        "attachment_path": attachment,
    }


def _fill_message_field(driver: Driver, message: str) -> bool:
    value_json = json.dumps(str(message or ""))
    script = """
const value = VALUE_HERE;
const isVisible = (element) => {
  const style = window.getComputedStyle(element);
  return style.display !== "none" && style.visibility !== "hidden" && element.getClientRects().length > 0;
};
const setNativeValue = (element, text) => {
  const prototype = element.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  const descriptor = Object.getOwnPropertyDescriptor(prototype, "value");
  descriptor?.set?.call(element, text);
};
const field =
  [...document.querySelectorAll("textarea, input[type='text'], input:not([type]), [contenteditable='true']")]
    .find((element) => !element.disabled && isVisible(element));
if (!field) {
  return false;
}
if (field.isContentEditable) {
  field.focus();
  field.textContent = value;
  field.dispatchEvent(new InputEvent("input", { bubbles: true, data: value, inputType: "insertText" }));
} else {
  setNativeValue(field, value);
  field.dispatchEvent(new Event("input", { bubbles: true }));
  field.dispatchEvent(new Event("change", { bubbles: true }));
}
field.dispatchEvent(new Event("blur", { bubbles: true }));
return true;
    """.replace("VALUE_HERE", value_json)
    return bool(
        driver.run_js(
            script,
        )
    )


def _has_login_gate(driver: Driver) -> bool:
    return bool(
        driver.run_js(
            """
const selectors = [
  "iframe[title='logininplace-iframe']",
  "a[href*='areariservata.subito.it/login_form']",
  "a[href*='areariservata.subito.it/form']",
];
if (selectors.some((selector) => document.querySelector(selector))) {
  return true;
}
const bodyText = document.body?.innerText || "";
return /Accedi\\s+su\\s+Subito|Accedi|Registrati/i.test(bodyText) && !!document.querySelector("[role='dialog'], iframe[title='logininplace-iframe']");
            """,
        )
    )


def _has_message_field(driver: Driver) -> bool:
    return bool(
        driver.run_js(
            """
const isVisible = (element) => {
  const style = window.getComputedStyle(element);
  return style.display !== "none" && style.visibility !== "hidden" && element.getClientRects().length > 0;
};
return !![...document.querySelectorAll("textarea, input[type='text'], input:not([type]), [contenteditable='true']")]
  .find((element) => !element.disabled && isVisible(element));
            """,
        )
    )


def _wait_for_login_completion(driver: Driver, login_wait_seconds: int) -> bool:
    if login_wait_seconds <= 0:
        return False

    elapsed = 0
    interval = 2
    while elapsed < login_wait_seconds:
        if not _has_login_gate(driver):
            return True
        driver.sleep(interval)
        elapsed += interval

    return not _has_login_gate(driver)


def _click_submit_button(driver: Driver) -> str | None:
    clicked = bool(
        driver.run_js(
            """
const isVisible = (element) => {
  const style = window.getComputedStyle(element);
  return style.display !== "none" && style.visibility !== "hidden" && element.getClientRects().length > 0;
};
const button =
  [...document.querySelectorAll("button[type='submit'], input[type='submit'], button, [role='button']")]
    .find((element) => !element.disabled && isVisible(element));
if (!button) {
  return false;
}
button.click();
return true;
            """,
        )
    )
    return "submit" if clicked else None


def _normalized_delay_seconds(value: float | int | str, default: float = 0.0) -> float:
    try:
        return max(float(value), 0.0)
    except (TypeError, ValueError):
        return max(default, 0.0)


def _sleep_if_needed(driver: Driver, seconds: float) -> None:
    if seconds > 0:
        driver.sleep(seconds)
