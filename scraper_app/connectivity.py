"""Internet connectivity gate shared by long-running scraper jobs."""

from __future__ import annotations

import time
from collections.abc import Callable
from urllib.error import URLError
from urllib.request import Request, urlopen


_CONNECTIVITY_ENDPOINTS = (
    "https://www.gstatic.com/generate_204",
    "https://cp.cloudflare.com/generate_204",
)


def internet_connection_available(*, timeout_seconds: float = 4.0) -> bool:
    """Return True only when an external HTTPS connectivity probe succeeds."""
    for url in _CONNECTIVITY_ENDPOINTS:
        try:
            request = Request(url, headers={"User-Agent": "AgentLabConnectivityCheck/1.0"})
            with urlopen(request, timeout=max(float(timeout_seconds or 4.0), 0.5)) as response:
                if int(getattr(response, "status", 0) or 0) == 204:
                    return True
        except (OSError, URLError, TimeoutError, ValueError):
            continue
    return False


def wait_for_internet_connection(
    *,
    label: str = "scraper",
    poll_seconds: float = 10.0,
    timeout_seconds: float = 4.0,
    should_stop: Callable[[], bool] | None = None,
) -> bool | None:
    """Pause this scraper process until the internet is reachable again."""
    if internet_connection_available(timeout_seconds=timeout_seconds):
        return False

    print(
        f"[network] {label}: connessione Internet assente; processi in pausa, "
        f"nuovo controllo ogni {max(float(poll_seconds or 10.0), 1.0):g}s",
        flush=True,
    )
    interval = max(float(poll_seconds or 10.0), 1.0)
    while True:
        if should_stop is not None and should_stop():
            return None
        if internet_connection_available(timeout_seconds=timeout_seconds):
            break
        remaining = interval
        while remaining > 0:
            if should_stop is not None and should_stop():
                return None
            sleep_for = min(1.0, remaining)
            time.sleep(sleep_for)
            remaining -= sleep_for
    print(f"[network] {label}: connessione ripristinata; riprendo il lavoro", flush=True)
    return True
