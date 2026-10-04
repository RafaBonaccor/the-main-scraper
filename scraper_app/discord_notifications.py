import json
import mimetypes
import uuid
from pathlib import Path
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

    heading = "**Candidato Vinted da catalogo**" if bool(row.get("deal_hunter_url_only_discord")) else "**Nuovo affare Vinted**"
    lines = [heading, title]
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
        lines.append(f"Apri annuncio: <{link}>")
    return "\n".join(lines)


def build_vinted_login_required_discord_message(status: dict) -> str:
    expected_alt = normalize_whitespace(str(status.get("expected_alt", "bonaccarla") or "bonaccarla"))
    current_url = str(status.get("current_url", "") or "").strip()
    checked_at = normalize_whitespace(str(status.get("checked_at", "") or ""))

    lines = [
        "⚠️ Login Vinted richiesto",
        f"Marker account assente: {expected_alt}",
    ]
    if checked_at:
        lines.append(f"Controllato: {checked_at}")
    if current_url:
        lines.append(f"URL: <{current_url}>")
    lines.append("Apri il browser dello scraper, fai login manualmente su Vinted e poi conferma dalla GUI.")
    return "\n".join(lines)


def build_vinted_profile_report_discord_message(meta: dict, rows: list[dict]) -> str:
    profile_url = str(meta.get("profile_url", "") or "").strip()
    member_id = normalize_whitespace(str(meta.get("member_id", "") or meta.get("profile_member_id", "") or ""))
    checked_at = normalize_whitespace(str(meta.get("profile_checked_at", "") or meta.get("extracted_at", "") or ""))
    active_count = int(meta.get("profile_item_count", 0) or 0)
    new_count = int(meta.get("profile_new_count", 0) or 0)
    gone_count = int(meta.get("profile_gone_count", 0) or 0)
    still_count = int(meta.get("profile_still_count", 0) or 0)
    cycle_index = int(meta.get("cycle_index", 1) or 1)

    lines = [
        "📦 **Report profilo Vinted**",
        f"Articoli attivi: {active_count}",
        f"Nuovi: {new_count}",
        f"Spariti/venduti/rimossi: {gone_count}",
        f"Gia presenti: {still_count}",
    ]
    if member_id:
        lines.append(f"Member ID: {member_id}")
    if checked_at:
        lines.append(f"Controllato: {checked_at}")
    lines.append(f"Ciclo: {cycle_index}")
    if profile_url:
        lines.append(f"Profilo: <{profile_url}>")

    new_rows = [row for row in rows if str(row.get("profile_item_status", "") or "") == "new"]
    gone_rows = [row for row in rows if str(row.get("profile_item_status", "") or "") == "gone"]
    if new_rows:
        lines.append("")
        lines.append("🟢 Nuovi articoli:")
        lines.extend(_format_vinted_profile_report_rows(new_rows))
    if gone_rows:
        lines.append("")
        lines.append("🔴 Spariti/venduti/rimossi:")
        lines.extend(_format_vinted_profile_report_rows(gone_rows))
    if not new_rows and not gone_rows:
        lines.append("")
        lines.append("Nessun cambiamento rispetto allo snapshot precedente.")
    return "\n".join(lines)


def _format_vinted_profile_report_rows(rows: list[dict], limit: int = 8) -> list[str]:
    lines: list[str] = []
    for row in rows[:limit]:
        title = normalize_whitespace(str(row.get("name", "") or row.get("title", "") or "Articolo Vinted"))
        price = normalize_whitespace(str(row.get("price", "") or row.get("price_text", "") or ""))
        sold_after = normalize_whitespace(str(row.get("profile_sold_after_text", "") or ""))
        link = str(row.get("link", "") or "").strip()
        suffix_parts = []
        if price:
            suffix_parts.append(price)
        if sold_after:
            suffix_parts.append(f"venduto/sparito dopo {sold_after}")
        suffix = f" — {' | '.join(suffix_parts)}" if suffix_parts else ""
        if link:
            lines.append(f"- {title}{suffix}: <{link}>")
        else:
            lines.append(f"- {title}{suffix}")
    remaining = len(rows) - limit
    if remaining > 0:
        lines.append(f"- ...altri {remaining}")
    return lines


def build_scraper_error_discord_message(
    title: str,
    message: str,
    *,
    level: str = "error",
    context: dict[str, object] | None = None,
) -> str:
    lines = [
        "⚠️ The Main Scraper alert" if str(level).lower() != "warning" else "⚠️ The Main Scraper warning",
        normalize_whitespace(str(title or "Errore scraper") or "Errore scraper"),
        normalize_whitespace(str(message or "Errore sconosciuto") or "Errore sconosciuto"),
    ]
    payload = context if isinstance(context, dict) else {}
    for key in ("source", "phase", "current_url", "checked_at", "job_kind"):
        value = normalize_whitespace(str(payload.get(key, "") or ""))
        if value:
            lines.append(f"{key}: {value}")
    return "\n".join(lines)


def send_discord_webhook_message(
    webhook_url: str,
    content: str,
    timeout_seconds: float = 10.0,
    *,
    attachment_paths: list[str | Path] | None = None,
    embeds: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    payload = {
        "content": str(content or "").strip(),
        "allowed_mentions": {"parse": []},
    }
    normalized_embeds = [embed for embed in (embeds or []) if isinstance(embed, dict)]
    if normalized_embeds:
        payload["embeds"] = normalized_embeds[:10]
    attachments = [
        Path(path).expanduser().resolve()
        for path in (attachment_paths or [])
        if str(path or "").strip()
    ]
    if attachments:
        boundary = f"----TheMainScraperBoundary{uuid.uuid4().hex}"
        data = _multipart_webhook_payload(payload, attachments, boundary)
        content_type = f"multipart/form-data; boundary={boundary}"
    else:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        content_type = "application/json"
    request = Request(
        str(webhook_url or "").strip(),
        data=data,
        headers={
            "Content-Type": content_type,
            "Accept": "application/json, text/plain, */*",
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36 TheMainScraper/1.0"
            ),
        },
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
            "attachments": [str(path) for path in attachments],
        }
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return {
            "ok": False,
            "status_code": int(exc.code or 0),
            "response_body": body,
            "sent_at": sent_at,
            "error": f"HTTP {exc.code}: {body or exc.reason}",
            "attachments": [str(path) for path in attachments],
        }
    except URLError as exc:
        return {
            "ok": False,
            "status_code": 0,
            "response_body": "",
            "sent_at": sent_at,
            "error": f"URL error: {exc.reason}",
            "attachments": [str(path) for path in attachments],
        }
    except Exception as exc:  # pragma: no cover - defensive fallback
        return {
            "ok": False,
            "status_code": 0,
            "response_body": "",
            "sent_at": sent_at,
            "error": f"{type(exc).__name__}: {exc}",
            "attachments": [str(path) for path in attachments],
        }


def _multipart_webhook_payload(payload: dict[str, object], attachments: list[Path], boundary: str) -> bytes:
    chunks: list[bytes] = []

    def add_text(name: str, value: str, *, content_type: str = "text/plain; charset=utf-8") -> None:
        chunks.append(f"--{boundary}\r\n".encode("utf-8"))
        chunks.append(
            f'Content-Disposition: form-data; name="{name}"\r\nContent-Type: {content_type}\r\n\r\n'.encode("utf-8")
        )
        chunks.append(value.encode("utf-8"))
        chunks.append(b"\r\n")

    add_text("payload_json", json.dumps(payload, ensure_ascii=False), content_type="application/json")
    for index, path in enumerate(attachments):
        mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        chunks.append(f"--{boundary}\r\n".encode("utf-8"))
        chunks.append(
            (
                f'Content-Disposition: form-data; name="files[{index}]"; filename="{path.name}"\r\n'
                f"Content-Type: {mime_type}\r\n\r\n"
            ).encode("utf-8")
        )
        chunks.append(path.read_bytes())
        chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(chunks)
