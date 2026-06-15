from .models import ScrapeOutcome
from .sources.custom_site import run_custom_site_scraper
from .sources.google_maps import run_google_maps_scraper


def run_scraper(source: str, **kwargs) -> ScrapeOutcome:
    if source == "google_maps":
        return run_google_maps_scraper(
            search=kwargs["search"],
            city=kwargs.get("city", ""),
            max_results=int(kwargs.get("max_results", 25)),
        )

    if source == "custom_site":
        return run_custom_site_scraper(
            url=kwargs["url"],
            item_selector=kwargs["item_selector"],
            name_selector=kwargs.get("name_selector", ""),
            phone_selector=kwargs.get("phone_selector", ""),
            link_selector=kwargs.get("link_selector", ""),
            cookie_reject_texts=kwargs.get("cookie_reject_texts", ""),
        )

    raise ValueError(f"Unsupported source: {source}")
