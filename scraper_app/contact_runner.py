import json
from pathlib import Path

from .sources.subito_contact import run_subito_bulk_contact_action, run_subito_contact_action
from .sources.vinted_offer import run_vinted_action_offer_batch, run_vinted_offer_action


def run_contact_action(source: str, **kwargs) -> dict:
    if source == "subito":
        links = _resolve_links(kwargs)
        common_kwargs = {
            "attachment": kwargs.get("attachment", ""),
            "message": kwargs.get("message", ""),
            "submit": bool(kwargs.get("submit", False)),
            "keep_open_seconds": int(kwargs.get("keep_open_seconds", 120)),
            "login_wait_seconds": int(kwargs.get("login_wait_seconds", 240)),
            "slow_mode": bool(kwargs.get("slow_mode", False)),
            "action_delay_seconds": float(kwargs.get("action_delay_seconds", 1.5)),
            "page_settle_seconds": float(kwargs.get("page_settle_seconds", 3.0)),
            "browser_mode": kwargs.get("browser_mode", "sessione_persistente"),
            "browser_user_data_dir": kwargs.get("browser_user_data_dir", ""),
            "browser_profile_directory": kwargs.get("browser_profile_directory", "Default"),
        }
        if len(links) == 1:
            return run_subito_contact_action(
                link=links[0],
                **common_kwargs,
            )
        return run_subito_bulk_contact_action(
            links=links,
            delay_between_seconds=int(kwargs.get("delay_between_seconds", 2)),
            **common_kwargs,
        )

    if source == "vinted":
        items = _resolve_vinted_offer_items(kwargs)
        common_kwargs = {
            "offer_discount_percent": float(kwargs.get("offer_discount_percent", 15.0)),
            "submit": bool(kwargs.get("submit", False)),
            "db_path": kwargs.get("db_path", "data/scraper.db"),
            "keep_browser_open": bool(kwargs.get("keep_browser_open", True)),
            "keep_open_seconds": int(kwargs.get("keep_open_seconds", 0)),
            "slow_mode": bool(kwargs.get("slow_mode", False)),
            "action_delay_seconds": float(kwargs.get("action_delay_seconds", 1.5)),
            "page_settle_seconds": float(kwargs.get("page_settle_seconds", 3.0)),
            "browser_mode": kwargs.get("browser_mode", "sessione_persistente"),
            "browser_user_data_dir": kwargs.get("browser_user_data_dir", ""),
            "browser_profile_directory": kwargs.get("browser_profile_directory", "Default"),
        }
        if len(items) == 1:
            item = items[0]
            return run_vinted_offer_action(
                link=str(item.get("link", "") or ""),
                base_price=item.get("base_price", item.get("base_total_price", "")),
                base_total_price=item.get("base_total_price", ""),
                **common_kwargs,
            )
        return run_vinted_action_offer_batch(
            offers=items,
            delay_between_seconds=int(kwargs.get("delay_between_seconds", 2)),
            **common_kwargs,
        )

    raise ValueError(f"Unsupported contact source: {source}")


def _resolve_links(kwargs: dict) -> list[str]:
    link = str(kwargs.get("link", "") or "").strip()
    links_file = str(kwargs.get("links_file", "") or "").strip()

    if links_file:
        return _read_links_file(Path(links_file))
    if link:
        return [link]
    raise ValueError("Serve un link oppure un links_file per il contatto.")


def _resolve_vinted_offer_items(kwargs: dict) -> list[dict]:
    link = str(kwargs.get("link", "") or "").strip()
    links_file = str(kwargs.get("links_file", "") or "").strip()
    base_price = kwargs.get("base_price", "")
    base_total_price = kwargs.get("base_total_price", "")

    if links_file:
        return _read_vinted_offer_items_file(Path(links_file))
    if link:
        return [{"link": link, "base_price": base_price or base_total_price, "base_total_price": base_total_price}]

    raise ValueError("Serve un link oppure un links_file per l'offerta Vinted.")


def _read_links_file(path: Path) -> list[str]:
    if not path.exists():
        raise ValueError(f"Links file non trovato: {path}")

    content = path.read_text(encoding="utf-8").strip()
    if not content:
        raise ValueError(f"Links file vuoto: {path}")

    if content.startswith("["):
        raw_links = json.loads(content)
        return [str(link).strip() for link in raw_links if str(link).strip()]

    links: list[str] = []
    for line in content.splitlines():
        value = line.strip()
        if value:
            links.append(value)
    return links


def _read_vinted_offer_items_file(path: Path) -> list[dict]:
    if not path.exists():
        raise ValueError(f"Links file non trovato: {path}")

    content = path.read_text(encoding="utf-8").strip()
    if not content:
        raise ValueError(f"Links file vuoto: {path}")

    items: list[dict] = []
    if content.startswith("["):
        raw_items = json.loads(content)
        if not isinstance(raw_items, list):
            raise ValueError(f"Links file non valido: {path}")
        for raw_item in raw_items:
            if isinstance(raw_item, dict):
                link = str(raw_item.get("link", "") or "").strip()
                if link:
                    items.append(
                        {
                            "link": link,
                            "item_id": str(raw_item.get("item_id", "") or ""),
                            "base_price": raw_item.get("base_price", raw_item.get("base_total_price", raw_item.get("price_value", raw_item.get("total_price_value", "")))),
                            "base_total_price": raw_item.get("base_total_price", raw_item.get("total_price_value", "")),
                        }
                    )
            else:
                link = str(raw_item or "").strip()
                if link:
                    items.append({"link": link, "item_id": "", "base_price": "", "base_total_price": ""})
        return items

    for line in content.splitlines():
        value = line.strip()
        if value:
            items.append({"link": value, "item_id": "", "base_price": "", "base_total_price": ""})
    return items
