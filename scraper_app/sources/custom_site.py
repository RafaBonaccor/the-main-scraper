from botasaurus.browser import Driver, Wait, browser

from ..browser_helpers import DEFAULT_COOKIE_REJECT_TEXTS, click_first_matching_text
from ..models import ScrapeOutcome
from ..utils import as_uri_if_path, extract_phone_numbers, first_nonempty_line, normalize_whitespace, parse_text_list


def run_custom_site_scraper(
    url: str,
    item_selector: str,
    name_selector: str = "",
    phone_selector: str = "",
    link_selector: str = "",
    cookie_reject_texts: str = "",
) -> ScrapeOutcome:
    config = {
        "url": as_uri_if_path(url),
        "item_selector": item_selector,
        "name_selector": name_selector,
        "phone_selector": phone_selector,
        "link_selector": link_selector,
        "cookie_reject_texts": parse_text_list(cookie_reject_texts) or list(DEFAULT_COOKIE_REJECT_TEXTS),
    }
    payload = _scrape_custom_site_task(config)
    return ScrapeOutcome(source="custom_site", rows=payload["rows"], meta=payload["meta"])


@browser
def _scrape_custom_site_task(driver: Driver, config: dict) -> dict:
    driver.get(config["url"])
    cookie_banner_action = click_first_matching_text(driver, config["cookie_reject_texts"])

    driver.select_all(config["item_selector"], wait=Wait.VERY_LONG)
    rows = _extract_rows(driver, config)

    return {
        "rows": rows,
        "meta": {
            "cookie_banner_action": cookie_banner_action,
            "url": config["url"],
            "item_selector": config["item_selector"],
            "row_count": len(rows),
        },
    }


def _extract_rows(driver: Driver, config: dict) -> list[dict]:
    raw_rows = driver.run_js(
        """
const normalize = (value) => (value || "").replace(/\\s+/g, " ").trim();
const getText = (root, selector) => {
  if (!selector) {
    return "";
  }
  const element = root.querySelector(selector);
  return element ? normalize(element.innerText || element.textContent) : "";
};
const getTexts = (root, selector) => {
  if (!selector) {
    return [];
  }
  return [...root.querySelectorAll(selector)]
    .map((element) => normalize(element.innerText || element.textContent))
    .filter(Boolean);
};
const getHref = (root, selector) => {
  if (!selector) {
    return "";
  }
  const element = root.querySelector(selector);
  return element ? (element.href || element.getAttribute("href") || "") : "";
};

return [...document.querySelectorAll(args.itemSelector)]
  .map((item) => {
    const rawText = normalize(item.innerText || item.textContent);
    if (!rawText) {
      return null;
    }
    return {
      raw_text: rawText,
      selected_name: getText(item, args.nameSelector),
      phone_texts: getTexts(item, args.phoneSelector),
      link: getHref(item, args.linkSelector) || getHref(item, "a[href]"),
    };
  })
  .filter(Boolean);
        """,
        {
            "itemSelector": config["item_selector"],
            "nameSelector": config.get("name_selector", ""),
            "phoneSelector": config.get("phone_selector", ""),
            "linkSelector": config.get("link_selector", ""),
        },
    )
    rows: list[dict] = []

    for raw_row in raw_rows or []:
        raw_text = (raw_row.get("raw_text") or "").strip()
        if not raw_text:
            continue

        name = normalize_whitespace(raw_row.get("selected_name") or "") or first_nonempty_line(raw_text)
        phones: list[str] = []
        seen: set[str] = set()

        for text in raw_row.get("phone_texts") or []:
            normalized_text = normalize_whitespace(text)
            matches = extract_phone_numbers(normalized_text) or ([normalized_text] if normalized_text else [])
            for phone in matches:
                if phone in seen:
                    continue
                seen.add(phone)
                phones.append(phone)

        if not phones:
            phones = extract_phone_numbers(raw_text)

        rows.append(
            {
                "source": "custom_site",
                "name": name,
                "phone": " | ".join(phones),
                "link": raw_row.get("link") or "",
                "raw_text": raw_text,
            }
        )

    return rows
