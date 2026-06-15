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
