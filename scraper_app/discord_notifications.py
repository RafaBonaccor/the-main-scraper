import json
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .utils import normalize_whitespace


def build_vinted_deal_discord_message(row: dict) -> str:
    title = normalize_whitespace(str(row.get("name", "") or "Affare Vinted"))
    link = str(row.get("link", "") or "").strip()
    search_term = normalize_whitespace(str(row.get("search_term", "") or ""))
    price = normalize_whitespace(str(row.get("price", "") or ""))
    shipping = normalize_whitespace(str(row.get("shipping_price", "") or ""))
    total = normalize_whitespace(str(row.get("total_price", "") or ""))
    favorites = row.get("favorite_count")
    published_at = normalize_whitespace(str(row.get("published_at", "") or ""))
    reason = normalize_whitespace(str(row.get("deal_hunter_reason", "") or ""))

    lines = ["**Nuovo affare Vinted**", title]
    if search_term:
        lines.append(f"Query: {search_term}")
    if price:
        lines.append(f"Prezzo: {price}")
    if shipping:
        lines.append(f"Spedizione: {shipping}")
    if total:
        lines.append(f"Totale: {total}")
    if favorites not in ("", None):
        lines.append(f"Like: {favorites}")
    if published_at:
        lines.append(f"Caricato: {published_at}")
    if reason:
        lines.append(f"Motivo: {reason}")
    if link:
        lines.append(link)
    return "\n".join(lines)


def send_discord_webhook_message(webhook_url: str, content: str, timeout_seconds: float = 10.0) -> dict[str, object]:
    payload = {
        "content": str(content or "").strip(),
        "allowed_mentions": {"parse": []},
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(
        str(webhook_url or "").strip(),
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    sent_at = datetime.now().isoformat(timespec="seconds")
    try:
        with urlopen(request, timeout=max(float(timeout_seconds or 0), 1.0)) as response:
            status_code = int(getattr(response, "status", 200) or 200)
            response_body = response.read().decode("utf-8", errors="replace")
        return {
            "ok": 200 <= status_code < 300,
            "status_code": status_code,
            "response_body": response_body,
            "sent_at": sent_at,
            "error": "",
        }
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return {
            "ok": False,
            "status_code": int(exc.code or 0),
            "response_body": body,
            "sent_at": sent_at,
            "error": f"HTTP {exc.code}: {body or exc.reason}",
        }
    except URLError as exc:
        return {
            "ok": False,
            "status_code": 0,
            "response_body": "",
            "sent_at": sent_at,
            "error": f"URL error: {exc.reason}",
        }
    except Exception as exc:  # pragma: no cover - defensive fallback
        return {
            "ok": False,
            "status_code": 0,
            "response_body": "",
            "sent_at": sent_at,
            "error": f"{type(exc).__name__}: {exc}",
        }

