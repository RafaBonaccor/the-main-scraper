import json
from pathlib import Path

from .sources.subito_contact import run_subito_bulk_contact_action, run_subito_contact_action
from .sources.vinted_offer import run_vinted_action_offer_batch, run_vinted_offer_action
from .sources.vinted_upload import run_vinted_upload_action, run_vinted_upload_batch
from .vinted_upload_worker import enqueue_vinted_upload_job, wait_for_vinted_upload_job_result


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

    if source == "vinted_upload":
        items = _resolve_vinted_upload_items(kwargs)
        common_kwargs = {
            "submit": bool(kwargs.get("submit", False)),
            "keep_browser_open": bool(kwargs.get("keep_browser_open", True)),
            "keep_open_seconds": int(kwargs.get("keep_open_seconds", 0)),
            "slow_mode": bool(kwargs.get("slow_mode", False)),
            "action_delay_seconds": float(kwargs.get("action_delay_seconds", 1.5)),
            "page_settle_seconds": float(kwargs.get("page_settle_seconds", 3.0)),
            "browser_mode": kwargs.get("browser_mode", "chrome_normale"),
            "browser_user_data_dir": kwargs.get("browser_user_data_dir", ""),
            "browser_profile_directory": kwargs.get("browser_profile_directory", "Default"),
        }
        reuse_browser_session = bool(kwargs.get("reuse_browser_session", True))
        if reuse_browser_session and bool(common_kwargs.get("keep_browser_open", True)):
            queued = enqueue_vinted_upload_job(
                items=items,
                delay_between_seconds=int(kwargs.get("delay_between_seconds", 2)),
                **common_kwargs,
            )
            if not bool(queued.get("ok")):
                return queued
            job_id = str(queued.get("job_id", "") or "").strip()
            result = wait_for_vinted_upload_job_result(
                job_id,
                timeout_seconds=max(int(kwargs.get("worker_wait_timeout_seconds", 1800) or 0), 30),
            )
            result["worker_job_id"] = job_id
            result["worker_pid"] = queued.get("worker_pid")
            result["worker_log_file"] = queued.get("worker_log_file")
            return result
        if len(items) == 1:
            item = items[0]
            return run_vinted_upload_action(
                title=str(item.get("title", "") or ""),
                description=str(item.get("description", "") or ""),
                price=item.get("price", ""),
                category=str(item.get("category", "") or ""),
                brand=str(item.get("brand", "") or ""),
                condition=str(item.get("condition", "") or ""),
                material=str(item.get("material", "") or ""),
                photo_paths=list(item.get("photo_paths", []) or []),
                openai_used=bool(item.get("openai_used", False)),
                openai_model=str(item.get("openai_model", "") or ""),
                **common_kwargs,
            )
        return run_vinted_upload_batch(
            items=items,
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


def _resolve_vinted_upload_items(kwargs: dict) -> list[dict]:
    items_file = str(kwargs.get("items_file", "") or kwargs.get("links_file", "") or "").strip()
    title = str(kwargs.get("title", "") or "").strip()
    description = str(kwargs.get("description", "") or "").strip()
    price = kwargs.get("price", "")
    category = str(kwargs.get("category", "") or "").strip()
    brand = str(kwargs.get("brand", "") or "").strip()
    condition = str(kwargs.get("condition", "") or "").strip()
    material = str(kwargs.get("material", "") or "").strip()
    photo_paths = list(kwargs.get("photo_paths", []) or [])

    if items_file:
        return _read_vinted_upload_items_file(Path(items_file))
    if title and str(price or "").strip() and photo_paths:
        return [{
            "title": title,
            "description": description,
            "price": price,
            "category": category,
            "brand": brand,
            "condition": condition,
            "material": material,
            "photo_paths": photo_paths,
        }]

    raise ValueError("Serve un items_file oppure almeno titolo/prezzo/foto per l'upload Vinted.")


def _read_vinted_upload_items_file(path: Path) -> list[dict]:
    if not path.exists():
        raise ValueError(f"Items file non trovato: {path}")

    content = path.read_text(encoding="utf-8").strip()
    if not content:
        raise ValueError(f"Items file vuoto: {path}")

    payload = json.loads(content)
    raw_items = payload.get("items", []) if isinstance(payload, dict) else payload
    manifest_openai_used = bool(payload.get("openai_used", False)) if isinstance(payload, dict) else False
    manifest_openai_model = str(payload.get("openai_model", "") or "").strip() if isinstance(payload, dict) else ""
    if not isinstance(raw_items, list):
        raise ValueError(f"Items file non valido: {path}")

    items: list[dict] = []
    for raw_item in raw_items:
        if not isinstance(raw_item, dict):
            continue
        title = str(raw_item.get("title", "") or "").strip()
        description = str(raw_item.get("description", "") or "").strip()
        price = raw_item.get("price", "")
        category = str(raw_item.get("category", "") or "").strip()
        brand = str(raw_item.get("brand", "") or "").strip()
        condition = str(raw_item.get("condition", "") or "").strip()
        material = str(raw_item.get("material", "") or "").strip()
        photo_paths = [str(path).strip() for path in list(raw_item.get("photo_paths", []) or []) if str(path).strip()]
        if title and str(price or "").strip() and photo_paths:
            items.append(
                {
                    "title": title,
                    "description": description,
                    "price": price,
                    "category": category,
                    "brand": brand,
                    "condition": condition,
                    "material": material,
                    "photo_paths": photo_paths,
                    "openai_used": bool(raw_item.get("openai_used", manifest_openai_used)),
                    "openai_model": str(raw_item.get("openai_model", manifest_openai_model) or "").strip(),
                }
            )
    if not items:
        raise ValueError(f"Items file senza articoli validi: {path}")
    return items
