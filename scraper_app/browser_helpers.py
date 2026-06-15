from urllib.parse import urlparse

from botasaurus.browser import Driver, Wait


DEFAULT_COOKIE_REJECT_TEXTS = (
    "Continua senza accettare",
    "Rifiuta tutto",
    "Rifiuta",
    "Reject all",
    "Decline all",
)


def click_visible_button_by_text(driver: Driver, text: str) -> bool:
    return bool(
        driver.run_js(
            """
const normalize = (value) => (value || "").replace(/\\s+/g, " ").trim().toLowerCase();
const target = normalize(args.text);
const isVisible = (element) => {
  const style = window.getComputedStyle(element);
  return style.display !== "none" && style.visibility !== "hidden" && element.getClientRects().length > 0;
};
const button = [...document.querySelectorAll("button, [role='button']")]
  .find((element) => isVisible(element) && normalize(element.innerText) === target);
if (!button) {
  return false;
}
button.click();
return true;
            """,
            {"text": text},
        )
    )


def click_visible_button_containing_text(driver: Driver, text: str) -> bool:
    return bool(
        driver.run_js(
            """
const normalize = (value) => (value || "").replace(/\\s+/g, " ").trim().toLowerCase();
const target = normalize(args.text);
const isVisible = (element) => {
  const style = window.getComputedStyle(element);
  return style.display !== "none" && style.visibility !== "hidden" && element.getClientRects().length > 0;
};
const candidates = [...document.querySelectorAll("button, [role='button'], [tabindex='0']")];
const button = candidates.find((element) => {
  if (!isVisible(element)) {
    return false;
  }
  const content = normalize(element.innerText || element.textContent);
  return content.includes(target);
});
if (!button) {
  return false;
}
button.click();
return true;
            """,
            {"text": text},
        )
    )


def click_first_matching_text(driver: Driver, texts: list[str] | tuple[str, ...]) -> str | None:
    for text in texts:
        try:
            if click_visible_button_by_text(driver, text):
                return text
        except Exception:
            pass

        try:
            if click_visible_button_containing_text(driver, text):
                return text
        except Exception:
            pass

        try:
            driver.click_element_containing_text(text, wait=Wait.SHORT)
            return text
        except Exception:
            continue

    return None


def current_page_url(driver: Driver) -> str:
    try:
        return str(driver.run_js("return window.location.href || ''")).strip()
    except Exception:
        return ""


def navigate_with_retries(driver: Driver, url: str, wait: int = Wait.LONG, use_google_get: bool = False) -> bool:
    target = str(url or "").strip()
    if not target:
        return False

    for _ in range(3):
        try:
            if use_google_get:
                driver.google_get(target, wait=wait)
            else:
                driver.get(target)
        except Exception:
            pass

        if _url_matches_target(current_page_url(driver), target):
            return True

        try:
            driver.run_js(
                """
window.location.href = args.url;
return window.location.href || "";
                """,
                {"url": target},
            )
        except Exception:
            pass
        driver.sleep(1)

        if _url_matches_target(current_page_url(driver), target):
            return True

        try:
            driver.run_js(
                """
window.location.replace(args.url);
return window.location.href || "";
                """,
                {"url": target},
            )
        except Exception:
            pass
        driver.sleep(1)

        if _url_matches_target(current_page_url(driver), target):
            return True

    return _url_matches_target(current_page_url(driver), target)


def _url_matches_target(current_url: str, target_url: str) -> bool:
    current = str(current_url or "").strip()
    target = str(target_url or "").strip()
    if not current or not target:
        return False
    if current == target or current.startswith(target) or target.startswith(current):
        return True

    current_parsed = urlparse(current)
    target_parsed = urlparse(target)
    if current_parsed.netloc.lower() != target_parsed.netloc.lower():
        return False

    current_path = current_parsed.path.rstrip("/")
    target_path = target_parsed.path.rstrip("/")
    if current_path and target_path and current_path == target_path:
        return True

    current_last = current_path.rsplit("/", 1)[-1]
    target_last = target_path.rsplit("/", 1)[-1]
    return bool(current_last and target_last and current_last == target_last)
