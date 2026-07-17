from datetime import datetime

from botasaurus.browser import Driver, Wait, browser

from ..browser_helpers import DEFAULT_COOKIE_REJECT_TEXTS, click_first_matching_text, current_page_url, navigate_with_retries
from ..browser_runtime import resolve_browser_arguments, resolve_browser_profile
from ..utils import normalize_whitespace
from ..vinted_access import emit_vinted_access_signal, read_vinted_access_status
from ..vinted_database import (
    DEFAULT_VINTED_DB_PATH,
    build_vinted_item_identity_keys,
    extract_vinted_item_id_from_link,
    load_vinted_submitted_offer_keys,
    save_vinted_offer_results,
)
from .vinted import (
    _build_vinted_detail_row,
    _hold_vinted_browser_if_requested,
    _wait_for_vinted_login_if_needed,
)


VINTED_OFFER_BUTTON_TEXTS = (
    "Fai un'offerta",
    "Fai un’offerta",
    "Fai un offerta",
)
DEFAULT_VINTED_OFFER_DISCOUNT_PERCENT = 15.0


def run_vinted_offer_action(
    link: str,
    base_price: object = "",
    base_total_price: object = "",
    offer_discount_percent: object = DEFAULT_VINTED_OFFER_DISCOUNT_PERCENT,
    submit: bool = False,
    db_path: str = str(DEFAULT_VINTED_DB_PATH),
    keep_browser_open: bool = True,
    keep_open_seconds: int = 0,
    slow_mode: bool = False,
    action_delay_seconds: float = 1.5,
    page_settle_seconds: float = 3.0,
    browser_mode: str = "sessione_persistente",
    browser_user_data_dir: str = "",
    browser_profile_directory: str = "Default",
) -> dict:
    clean_link = str(link or "").strip()
    if not clean_link:
        raise ValueError("Vinted offer action requires a listing link.")
    config = {
        "link": clean_link,
        "base_price": base_price,
        "base_total_price": base_total_price,
        "offer_discount_percent": normalize_vinted_offer_discount_percent(offer_discount_percent),
        "submit": bool(submit),
        "db_path": str(db_path or DEFAULT_VINTED_DB_PATH),
        "keep_browser_open": bool(keep_browser_open),
        "keep_open_seconds": max(int(keep_open_seconds or 0), 0),
        "slow_mode": bool(slow_mode),
        "action_delay_seconds": float(action_delay_seconds),
        "page_settle_seconds": float(page_settle_seconds),
        "browser_mode": browser_mode,
        "browser_user_data_dir": browser_user_data_dir,
        "browser_profile_directory": browser_profile_directory,
    }
    if bool(config["submit"]):
        config["_submitted_offer_keys"] = load_vinted_submitted_offer_keys(config["db_path"])
    result = _run_vinted_offer_task(config)
    if bool(config["submit"]):
        result.update(save_vinted_offer_results([result], db_path=config["db_path"]))
    return result


def run_vinted_action_offer_batch(
    offers: list[dict],
    offer_discount_percent: object = DEFAULT_VINTED_OFFER_DISCOUNT_PERCENT,
    submit: bool = False,
    delay_between_seconds: int = 2,
    db_path: str = str(DEFAULT_VINTED_DB_PATH),
    keep_browser_open: bool = True,
    keep_open_seconds: int = 0,
    slow_mode: bool = False,
    action_delay_seconds: float = 1.5,
    page_settle_seconds: float = 3.0,
    browser_mode: str = "sessione_persistente",
    browser_user_data_dir: str = "",
    browser_profile_directory: str = "Default",
) -> dict:
    normalized_offers = [
        {
            "link": str(item.get("link", "") or "").strip(),
            "item_id": str(item.get("item_id", "") or "").strip(),
            "base_price": item.get("base_price", item.get("base_total_price", "")),
            "base_total_price": item.get("base_total_price", ""),
        }
        for item in offers
        if isinstance(item, dict) and str(item.get("link", "") or "").strip()
    ]
    if not normalized_offers:
        raise ValueError("Vinted bulk offer action requires at least one listing.")
    config = {
        "offers": normalized_offers,
        "offer_discount_percent": normalize_vinted_offer_discount_percent(offer_discount_percent),
        "submit": bool(submit),
        "delay_between_seconds": max(int(delay_between_seconds or 0), 0),
        "db_path": str(db_path or DEFAULT_VINTED_DB_PATH),
        "keep_browser_open": bool(keep_browser_open),
        "keep_open_seconds": max(int(keep_open_seconds or 0), 0),
        "slow_mode": bool(slow_mode),
        "action_delay_seconds": float(action_delay_seconds),
        "page_settle_seconds": float(page_settle_seconds),
        "browser_mode": browser_mode,
        "browser_user_data_dir": browser_user_data_dir,
        "browser_profile_directory": browser_profile_directory,
    }
    if bool(config["submit"]):
        config["_submitted_offer_keys"] = load_vinted_submitted_offer_keys(config["db_path"])
    result = _run_vinted_offer_bulk_task(config)
    if bool(config["submit"]):
        result.update(save_vinted_offer_results(list(result.get("results", []) or []), db_path=config["db_path"]))
    return result


@browser(
    profile=resolve_browser_profile,
    add_arguments=resolve_browser_arguments,
    wait_for_complete_page_load=False,
)
def _run_vinted_offer_task(driver: Driver, config: dict) -> dict:
    result = _offer_single_vinted_listing(driver, config)
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
def _run_vinted_offer_bulk_task(driver: Driver, config: dict) -> dict:
    offers = list(config.get("offers", []) or [])
    delay_between_seconds = max(int(config.get("delay_between_seconds", 2) or 0), 0)
    if bool(config.get("slow_mode", False)):
        delay_between_seconds = max(delay_between_seconds, 2)
    submitted_offer_keys = set(config.get("_submitted_offer_keys", set()) or set())

    results: list[dict] = []
    for index, offer in enumerate(offers):
        try:
            result = _offer_single_vinted_listing(driver, {**config, **offer, "_submitted_offer_keys": submitted_offer_keys})
        except Exception as exc:
            result = _build_vinted_offer_error_result(
                driver,
                {**config, **offer},
                error=f"{type(exc).__name__}: {exc}",
            )
        results.append(result)
        if bool(result.get("submitted")) or bool(result.get("skipped_already_submitted")):
            submitted_offer_keys.update(_vinted_result_identity_keys(result))
        if index < len(offers) - 1 and delay_between_seconds > 0:
            driver.sleep(delay_between_seconds)

    _hold_vinted_browser_if_requested(
        driver,
        keep_browser_open=bool(config.get("keep_browser_open", False)),
        keep_open_seconds=int(config.get("keep_open_seconds", 0) or 0),
    )
    prepared_count = sum(1 for item in results if item.get("prepared"))
    sent_count = sum(1 for item in results if item.get("submitted"))
    skipped_already_submitted_count = sum(1 for item in results if item.get("skipped_already_submitted"))
    failed_count = sum(
        1 for item in results if not item.get("ok") and not item.get("skipped_already_submitted")
    )
    return {
        "ok": (sent_count > 0 if bool(config.get("submit", False)) else prepared_count > 0)
        or skipped_already_submitted_count > 0,
        "links_count": len(offers),
        "prepared_count": prepared_count,
        "sent_count": sent_count,
        "failed_count": failed_count,
        "skipped_already_submitted_count": skipped_already_submitted_count,
        "offer_discount_percent": float(config.get("offer_discount_percent", DEFAULT_VINTED_OFFER_DISCOUNT_PERCENT)),
        "submit": bool(config.get("submit", False)),
        "delay_between_seconds": delay_between_seconds,
        "keep_browser_open": bool(config.get("keep_browser_open", False)),
        "keep_open_seconds": int(config.get("keep_open_seconds", 0) or 0),
        "results": results,
    }


def _offer_single_vinted_listing(driver: Driver, config: dict) -> dict:
    link = str(config.get("link", "") or "").strip()
    submit = bool(config.get("submit", False))
    if submit and _vinted_offer_was_already_submitted(config):
        return _build_vinted_offer_skipped_result(config)
    action_delay_seconds = _normalized_delay_seconds(
        config.get("action_delay_seconds", 1.5),
        default=1.5 if bool(config.get("slow_mode", False)) else 0.0,
    )
    page_settle_seconds = _normalized_delay_seconds(
        config.get("page_settle_seconds", 3.0),
        default=3.0 if bool(config.get("slow_mode", False)) else 0.0,
    )

    navigated = navigate_with_retries(driver, link, wait=Wait.LONG)
    _sleep_if_needed(driver, page_settle_seconds)
    cookie_action = click_first_matching_text(driver, DEFAULT_COOKIE_REJECT_TEXTS) or ""
    _sleep_if_needed(driver, action_delay_seconds)
    access_status = read_vinted_access_status(driver)
    emit_vinted_access_signal(access_status)
    access_status = _wait_for_vinted_login_if_needed(
        driver,
        access_status,
        revisit_url=link,
        action_delay_seconds=action_delay_seconds,
        page_settle_seconds=page_settle_seconds,
    )

    detail_row = _build_vinted_detail_row(
        driver=driver,
        current_link=link,
        search_term="",
        search_url="",
        tag="",
        item_name="",
        base_row={},
    )
    selected_price_value = _first_numeric_price(
        config.get("base_price"),
        config.get("base_total_price"),
    )
    parsed_price_value = _first_numeric_price(
        detail_row.get("price_value"),
        detail_row.get("total_price_value"),
    )
    price_value = _first_numeric_price(
        parsed_price_value,
        selected_price_value,
    )
    if price_value is None:
        raise RuntimeError("Prezzo annuncio non disponibile: impossibile calcolare l'offerta.")

    offer_discount_percent = normalize_vinted_offer_discount_percent(
        config.get("offer_discount_percent", DEFAULT_VINTED_OFFER_DISCOUNT_PERCENT)
    )
    offer_value = calculate_vinted_offer_value(price_value, offer_discount_percent)
    offer_input_value = format_vinted_offer_input(offer_value)

    offer_button_action = click_first_matching_text(driver, VINTED_OFFER_BUTTON_TEXTS) or _click_vinted_offer_button(driver) or ""
    if not offer_button_action:
        raise RuntimeError("Pulsante 'Fai un'offerta' non trovato su questo annuncio.")
    _sleep_if_needed(driver, max(action_delay_seconds, 1.2))

    offer_input_filled = _fill_vinted_offer_input(driver, offer_input_value)
    if not offer_input_filled:
        raise RuntimeError("Campo prezzo offerta non trovato nel popup Vinted.")
    _sleep_if_needed(driver, max(action_delay_seconds, 1.0))

    submit_action = ""
    submitted = False
    if submit:
        submit_action = _click_vinted_offer_submit(driver) or click_first_matching_text(driver, ("Offri",)) or ""
        submitted = bool(submit_action)
        if not submitted:
            raise RuntimeError("Pulsante 'Offri' non trovato o non cliccabile.")
        _sleep_if_needed(driver, max(action_delay_seconds, 1.5))

    result = {
        "ok": submitted if submit else offer_input_filled,
        "prepared": offer_input_filled,
        "submitted": submitted,
        "skipped_already_submitted": False,
        "link": link,
        "item_id": str(detail_row.get("item_id", "") or extract_vinted_item_id_from_link(link)),
        "navigated": navigated,
        "current_url": current_page_url(driver),
        "cookie_banner_action": cookie_action,
        "offer_button_action": offer_button_action,
        "offer_input_filled": offer_input_filled,
        "offer_submit_action": submit_action,
        "offer_discount_percent": offer_discount_percent,
        "submit": submit,
        "selected_price": selected_price_value,
        "parsed_price": parsed_price_value,
        "source_price": price_value,
        "source_price_text": str(detail_row.get("price", "") or ""),
        "selected_total_price": selected_price_value,
        "parsed_total_price": parsed_price_value,
        "source_total_price": price_value,
        "source_total_price_text": str(detail_row.get("price", "") or ""),
        "offer_value": offer_value,
        "offer_input_value": offer_input_value,
        "item_name": str(detail_row.get("name", "") or ""),
        "submitted_at": datetime.now().isoformat(timespec="seconds") if submitted else "",
        "login_required": not bool(access_status.get("marker_present")),
        "keep_browser_open": bool(config.get("keep_browser_open", False)),
        "keep_open_seconds": int(config.get("keep_open_seconds", 0) or 0),
    }
    return result


def _build_vinted_offer_error_result(driver: Driver, config: dict, *, error: str) -> dict:
    current_url = ""
    try:
        current_url = current_page_url(driver)
    except Exception:
        current_url = ""
    return {
        "ok": False,
        "prepared": False,
        "submitted": False,
        "skipped_already_submitted": False,
        "link": str(config.get("link", "") or "").strip(),
        "item_id": str(config.get("item_id", "") or "").strip() or extract_vinted_item_id_from_link(config.get("link", "")),
        "navigated": False,
        "current_url": current_url,
        "cookie_banner_action": "",
        "offer_button_action": "",
        "offer_input_filled": False,
        "offer_submit_action": "",
        "offer_discount_percent": normalize_vinted_offer_discount_percent(
            config.get("offer_discount_percent", DEFAULT_VINTED_OFFER_DISCOUNT_PERCENT)
        ),
        "submit": bool(config.get("submit", False)),
        "selected_price": _first_numeric_price(config.get("base_price"), config.get("base_total_price")),
        "parsed_price": None,
        "source_price": _first_numeric_price(config.get("base_price"), config.get("base_total_price")),
        "selected_total_price": _first_numeric_price(config.get("base_price"), config.get("base_total_price")),
        "parsed_total_price": None,
        "source_total_price": _first_numeric_price(config.get("base_price"), config.get("base_total_price")),
        "source_total_price_text": "",
        "offer_value": None,
        "offer_input_value": "",
        "item_name": str(config.get("name", "") or ""),
        "submitted_at": "",
        "login_required": False,
        "keep_browser_open": bool(config.get("keep_browser_open", False)),
        "keep_open_seconds": int(config.get("keep_open_seconds", 0) or 0),
        "error": error,
    }


def _build_vinted_offer_skipped_result(config: dict) -> dict:
    link = str(config.get("link", "") or "").strip()
    return {
        "ok": True,
        "prepared": False,
        "submitted": False,
        "skipped_already_submitted": True,
        "link": link,
        "item_id": str(config.get("item_id", "") or "").strip() or extract_vinted_item_id_from_link(link),
        "navigated": False,
        "current_url": "",
        "cookie_banner_action": "",
        "offer_button_action": "",
        "offer_input_filled": False,
        "offer_submit_action": "",
        "offer_discount_percent": normalize_vinted_offer_discount_percent(
            config.get("offer_discount_percent", DEFAULT_VINTED_OFFER_DISCOUNT_PERCENT)
        ),
        "submit": bool(config.get("submit", False)),
        "selected_price": _first_numeric_price(config.get("base_price"), config.get("base_total_price")),
        "parsed_price": None,
        "source_price": _first_numeric_price(config.get("base_price"), config.get("base_total_price")),
        "source_price_text": "",
        "selected_total_price": _first_numeric_price(config.get("base_price"), config.get("base_total_price")),
        "parsed_total_price": None,
        "source_total_price": _first_numeric_price(config.get("base_price"), config.get("base_total_price")),
        "source_total_price_text": "",
        "offer_value": None,
        "offer_input_value": "",
        "item_name": str(config.get("name", "") or ""),
        "submitted_at": "",
        "login_required": False,
        "keep_browser_open": bool(config.get("keep_browser_open", False)),
        "keep_open_seconds": int(config.get("keep_open_seconds", 0) or 0),
        "message": "Offerta gia inviata in precedenza: annuncio saltato.",
    }


def normalize_vinted_offer_discount_percent(value: object) -> float:
    if isinstance(value, str):
        value = value.strip().replace("%", "").replace(",", ".")
    try:
        percent = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Percentuale offerta Vinted non valida.") from exc
    if percent < 0 or percent >= 100:
        raise ValueError("Percentuale offerta Vinted deve essere tra 0 e 99.99.")
    return percent


def calculate_vinted_offer_value(total_price_value: float, discount_percent: float = DEFAULT_VINTED_OFFER_DISCOUNT_PERCENT) -> float:
    normalized_discount = normalize_vinted_offer_discount_percent(discount_percent)
    return round(float(total_price_value) * (1.0 - (normalized_discount / 100.0)), 2)


def format_vinted_offer_input(offer_value: float) -> str:
    return f"{float(offer_value):.2f}"


def _first_numeric_price(*values: object) -> float | None:
    for value in values:
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _vinted_offer_identity_keys(config: dict) -> tuple[str, ...]:
    return build_vinted_item_identity_keys(
        item_id=config.get("item_id", ""),
        link=config.get("link", ""),
    )


def _vinted_result_identity_keys(result: dict) -> tuple[str, ...]:
    return build_vinted_item_identity_keys(
        item_id=result.get("item_id", ""),
        link=result.get("link", ""),
    )


def _vinted_offer_was_already_submitted(config: dict) -> bool:
    submitted_offer_keys = config.get("_submitted_offer_keys", set()) or set()
    if not isinstance(submitted_offer_keys, set):
        submitted_offer_keys = set(submitted_offer_keys)
    return any(key in submitted_offer_keys for key in _vinted_offer_identity_keys(config))


def _click_vinted_offer_button(driver: Driver) -> str:
    clicked = driver.run_js(
        """
const selectors = [
  "button[data-testid*='offer']",
  "a[data-testid*='offer']",
  "button",
  "a",
];
const clean = (value) => (value || '').replace(/\\s+/g, ' ').trim();
for (const selector of selectors) {
  for (const node of document.querySelectorAll(selector)) {
    const text = clean(node.innerText || node.textContent || '');
    if (!text) continue;
    if (/fai\\s+un[’']?offerta/i.test(text)) {
      node.click();
      return text;
    }
  }
}
return '';
        """
    )
    return normalize_whitespace(str(clicked or ""))


def _fill_vinted_offer_input(driver: Driver, value: str) -> bool:
    script = """
const value = VALUE_HERE;
const input = document.querySelector("input[data-testid='offer-price-field--input'], input#offer, input[name='offer']");
if (!input || input.disabled) {
  return false;
}
const descriptor = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value");
descriptor?.set?.call(input, value);
input.dispatchEvent(new Event("input", { bubbles: true }));
input.dispatchEvent(new Event("change", { bubbles: true }));
input.dispatchEvent(new Event("blur", { bubbles: true }));
return true;
        """.replace("VALUE_HERE", repr(str(value)))
    return bool(driver.run_js(script))


def _click_vinted_offer_submit(driver: Driver) -> str:
    clicked = driver.run_js(
        """
const selectors = [
  "button[data-testid='offer-submit-button']",
  "button[type='submit']",
  "button",
];
const clean = (value) => (value || '').replace(/\\s+/g, ' ').trim();
for (const selector of selectors) {
  for (const node of document.querySelectorAll(selector)) {
    const text = clean(node.innerText || node.textContent || '');
    const testId = clean(node.getAttribute('data-testid'));
    if (node.disabled) continue;
    if (testId === 'offer-submit-button' || /^offri$/i.test(text)) {
      node.click();
      return text || testId || 'offer-submit-button';
    }
  }
}
return '';
        """
    )
    return normalize_whitespace(str(clicked or ""))


def _sleep_if_needed(driver: Driver, seconds: float) -> None:
    if seconds > 0:
        driver.sleep(seconds)


def _normalized_delay_seconds(value: object, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return max(default, 0.0)
    return max(parsed, 0.0)
