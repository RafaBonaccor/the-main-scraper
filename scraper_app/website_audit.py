import ipaddress
import re
import time
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urljoin, urlparse
from urllib.request import Request, urlopen


EMAIL_PATTERN = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)
CONTACT_PATH_MARKERS = (
    "contatt",
    "contact",
    "chi-siamo",
    "about",
)
SOCIAL_HOSTS = {
    "facebook.com": "social_facebook",
    "instagram.com": "social_instagram",
    "linkedin.com": "social_linkedin",
}
IGNORED_EMAIL_DOMAINS = {
    "example.com",
    "example.org",
    "sentry.io",
    "wixpress.com",
}
MAX_RESPONSE_BYTES = 2_000_000


class WebsiteDocumentParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.in_title = False
        self.meta_description = ""
        self.generator = ""
        self.has_viewport = False
        self.links: list[tuple[str, str]] = []
        self._current_link = ""
        self._current_link_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key.lower(): str(value or "") for key, value in attrs}
        lowered_tag = tag.lower()
        if lowered_tag == "title":
            self.in_title = True
        elif lowered_tag == "meta":
            name = attributes.get("name", "").lower()
            property_name = attributes.get("property", "").lower()
            content = attributes.get("content", "").strip()
            if name == "viewport":
                self.has_viewport = True
            elif name == "description" and not self.meta_description:
                self.meta_description = content
            elif property_name == "og:description" and not self.meta_description:
                self.meta_description = content
            elif name == "generator" and not self.generator:
                self.generator = content
        elif lowered_tag == "a":
            self._current_link = attributes.get("href", "").strip()
            self._current_link_text = []

    def handle_endtag(self, tag: str) -> None:
        lowered_tag = tag.lower()
        if lowered_tag == "title":
            self.in_title = False
        elif lowered_tag == "a" and self._current_link:
            self.links.append((self._current_link, " ".join(self._current_link_text).strip()))
            self._current_link = ""
            self._current_link_text = []

    def handle_data(self, data: str) -> None:
        value = " ".join(data.split())
        if not value:
            return
        if self.in_title:
            self.title_parts.append(value)
        if self._current_link:
            self._current_link_text.append(value)

    @property
    def title(self) -> str:
        return " ".join(self.title_parts).strip()


def audit_business_website(url: str, timeout_seconds: float = 10.0, max_pages: int = 2) -> dict:
    normalized_url = _normalize_website_url(url)
    if not normalized_url:
        return _missing_website_result()

    result = {
        "website": normalized_url,
        "website_final_url": "",
        "website_status": "unreachable",
        "website_http_status": "",
        "website_response_ms": "",
        "website_https": False,
        "website_mobile_ready": False,
        "website_title": "",
        "website_meta_description": "",
        "website_generator": "",
        "website_emails": "",
        "email": "",
        "social_facebook": "",
        "social_instagram": "",
        "social_linkedin": "",
        "website_pages_checked": 0,
        "website_error": "",
    }

    try:
        home = _fetch_html(normalized_url, timeout_seconds)
    except (HTTPError, URLError, OSError, ValueError) as exc:
        result["website_error"] = _compact_error(exc)
        return _with_opportunity_score(result)

    result.update(
        {
            "website_final_url": home["final_url"],
            "website_status": "audited",
            "website_http_status": home["status"],
            "website_response_ms": home["response_ms"],
            "website_https": urlparse(home["final_url"]).scheme.lower() == "https",
            "website_pages_checked": 1,
        }
    )

    documents = [home]
    contact_urls = _contact_page_urls(home["parser"], home["final_url"])
    for contact_url in contact_urls[: max(max_pages - 1, 0)]:
        try:
            documents.append(_fetch_html(contact_url, timeout_seconds))
            result["website_pages_checked"] += 1
        except (HTTPError, URLError, OSError, ValueError):
            continue

    home_parser = home["parser"]
    result["website_title"] = home_parser.title
    result["website_meta_description"] = home_parser.meta_description
    result["website_generator"] = home_parser.generator
    result["website_mobile_ready"] = home_parser.has_viewport

    emails: list[str] = []
    social_links: dict[str, str] = {}
    for document in documents:
        emails.extend(_extract_emails(document["html"], document["parser"]))
        social_links.update(_extract_social_links(document["parser"], document["final_url"]))

    unique_emails = _unique_preserving_order(emails)
    result["website_emails"] = " | ".join(unique_emails)
    result["email"] = unique_emails[0] if unique_emails else ""
    result.update(social_links)
    return _with_opportunity_score(result)


def annotate_lead_opportunity(row: dict) -> dict:
    annotated = dict(row)
    website = str(annotated.get("website", "") or "").strip()
    if not website:
        if annotated.get("detail_checked"):
            annotated.update(_missing_website_result())
        else:
            annotated.update(_unknown_website_result())
    else:
        annotated.update(_with_opportunity_score({
            "website": website,
            "website_status": "not_audited",
            "website_error": "",
            "website_https": website.lower().startswith("https://"),
            "website_mobile_ready": "",
            "website_title": "",
            "website_meta_description": "",
            "website_response_ms": "",
        }))
    return annotated


def _fetch_html(url: str, timeout_seconds: float) -> dict:
    if not _is_safe_public_url(url):
        raise ValueError("URL del sito non pubblico o non valido")

    request = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "it-IT,it;q=0.9,en;q=0.7",
        },
    )
    started_at = time.perf_counter()
    with urlopen(request, timeout=max(float(timeout_seconds), 1.0)) as response:
        content_type = str(response.headers.get("Content-Type", "") or "").lower()
        if content_type and "html" not in content_type and "xhtml" not in content_type:
            raise ValueError(f"Contenuto non HTML: {content_type}")
        raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raw = raw[:MAX_RESPONSE_BYTES]
        charset = response.headers.get_content_charset() or "utf-8"
        html = raw.decode(charset, errors="replace")
        final_url = str(response.geturl() or url)
        status = int(getattr(response, "status", 200) or 200)
    response_ms = round((time.perf_counter() - started_at) * 1000)

    parser = WebsiteDocumentParser()
    parser.feed(html)
    return {
        "html": html,
        "parser": parser,
        "final_url": final_url,
        "status": status,
        "response_ms": response_ms,
    }


def _normalize_website_url(url: str) -> str:
    value = str(url or "").strip()
    if not value:
        return ""
    if value.startswith("//"):
        value = f"https:{value}"
    if not value.lower().startswith(("http://", "https://")):
        value = f"https://{value}"
    parsed = urlparse(value)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return ""
    return value


def _is_safe_public_url(url: str) -> bool:
    parsed = urlparse(url)
    hostname = str(parsed.hostname or "").strip().lower()
    if parsed.scheme.lower() not in {"http", "https"} or not hostname:
        return False
    if hostname == "localhost" or hostname.endswith(".localhost"):
        return False
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return True
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
    )


def _contact_page_urls(parser: WebsiteDocumentParser, base_url: str) -> list[str]:
    base_host = (urlparse(base_url).hostname or "").lower()
    candidates: list[str] = []
    for href, text in parser.links:
        joined = urljoin(base_url, href)
        parsed = urlparse(joined)
        if parsed.scheme.lower() not in {"http", "https"}:
            continue
        if (parsed.hostname or "").lower() != base_host:
            continue
        searchable = f"{unquote(parsed.path)} {text}".lower()
        if not any(marker in searchable for marker in CONTACT_PATH_MARKERS):
            continue
        candidates.append(joined.split("#", 1)[0])
    return _unique_preserving_order(candidates)


def _extract_emails(html: str, parser: WebsiteDocumentParser) -> list[str]:
    candidates = EMAIL_PATTERN.findall(html or "")
    for href, _text in parser.links:
        if href.lower().startswith("mailto:"):
            candidates.extend(EMAIL_PATTERN.findall(unquote(href[7:])))

    valid: list[str] = []
    for email in candidates:
        normalized = email.strip(".,;:()[]{}<>\"'").lower()
        domain = normalized.rsplit("@", 1)[-1]
        if domain in IGNORED_EMAIL_DOMAINS:
            continue
        if any(normalized.endswith(suffix) for suffix in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg")):
            continue
        valid.append(normalized)
    return _unique_preserving_order(valid)


def _extract_social_links(parser: WebsiteDocumentParser, base_url: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for href, _text in parser.links:
        joined = urljoin(base_url, href)
        hostname = (urlparse(joined).hostname or "").lower().removeprefix("www.")
        for social_host, field_name in SOCIAL_HOSTS.items():
            if hostname == social_host or hostname.endswith(f".{social_host}"):
                found.setdefault(field_name, joined)
    return found


def _missing_website_result() -> dict:
    return {
        "website": "",
        "website_final_url": "",
        "website_status": "missing",
        "website_http_status": "",
        "website_response_ms": "",
        "website_https": False,
        "website_mobile_ready": False,
        "website_title": "",
        "website_meta_description": "",
        "website_generator": "",
        "website_emails": "",
        "email": "",
        "social_facebook": "",
        "social_instagram": "",
        "social_linkedin": "",
        "website_pages_checked": 0,
        "website_error": "",
        "opportunity_score": 100,
        "lead_priority": "alta",
        "lead_reason": "Attivita senza sito web pubblico su Google Maps.",
    }


def _unknown_website_result() -> dict:
    result = _missing_website_result()
    result.update(
        {
            "website_status": "not_checked",
            "opportunity_score": 50,
            "lead_priority": "media",
            "lead_reason": "Sito non verificato: la scheda Maps non e stata aperta o letta completamente.",
        }
    )
    return result


def _with_opportunity_score(result: dict) -> dict:
    scored = dict(result)
    status = str(scored.get("website_status", "") or "")
    if status == "unreachable":
        score = 85
        reasons = ["Sito non raggiungibile o non analizzabile"]
    elif status == "not_audited":
        score = 35
        reasons = ["Sito presente, audit non eseguito"]
        if not scored.get("website_https"):
            score += 20
            reasons.append("assenza HTTPS")
    else:
        score = 10
        reasons: list[str] = []
        if not scored.get("website_https"):
            score += 20
            reasons.append("assenza HTTPS")
        if not scored.get("website_mobile_ready"):
            score += 25
            reasons.append("viewport mobile assente")
        if not str(scored.get("website_title", "") or "").strip():
            score += 10
            reasons.append("titolo pagina assente")
        if not str(scored.get("website_meta_description", "") or "").strip():
            score += 10
            reasons.append("meta description assente")
        try:
            if float(scored.get("website_response_ms", 0) or 0) >= 4000:
                score += 10
                reasons.append("risposta lenta")
        except (TypeError, ValueError):
            pass

    score = max(0, min(int(score), 100))
    priority = "alta" if score >= 70 else "media" if score >= 40 else "bassa"
    if status not in {"unreachable", "not_audited"}:
        scored["website_status"] = "needs_review" if score >= 40 else "good"
    scored["opportunity_score"] = score
    scored["lead_priority"] = priority
    scored["lead_reason"] = "; ".join(reasons) if reasons else "Sito presente con segnali tecnici essenziali rilevati."
    return scored


def _unique_preserving_order(values: list[str]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = str(value or "").strip()
        lowered = normalized.lower()
        if not normalized or lowered in seen:
            continue
        seen.add(lowered)
        unique.append(normalized)
    return unique


def _compact_error(exc: Exception) -> str:
    message = " ".join(str(exc).split())
    return f"{type(exc).__name__}: {message}"[:300]
