import json
from pathlib import Path

from .sources.subito_contact import run_subito_bulk_contact_action, run_subito_contact_action


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

    raise ValueError(f"Unsupported contact source: {source}")


def _resolve_links(kwargs: dict) -> list[str]:
    link = str(kwargs.get("link", "") or "").strip()
    links_file = str(kwargs.get("links_file", "") or "").strip()

    if links_file:
        return _read_links_file(Path(links_file))
    if link:
        return [link]
    raise ValueError("Serve un link oppure un links_file per il contatto.")


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
