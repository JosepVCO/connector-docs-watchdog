from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib import robotparser
from urllib.parse import urlparse

import httpx

# Keep it short: Meta serves an empty shell to UAs with a parenthetical comment.
USER_AGENT = "connector-docs-watchdog/0.1"
log = logging.getLogger("docs_watchdog.fetch")

TIMEOUT = httpx.Timeout(30.0, connect=10.0)
MAX_ATTEMPTS = 3  # bounded: 1 try + 2 retries
BACKOFF_SECONDS = (1.0, 3.0)  # waits before retry 1 and 2
RETRY_STATUS = {429, 500, 502, 503, 504}


class SourceError(Exception):
    """A source could not be fetched or yielded no usable content."""


class FetchError(SourceError):
    """Network, HTTP or robots.txt failure: nothing usable was retrieved."""


class EmptyContentError(SourceError):
    """Retrieved, but empty/too short/missing expected content: treated as an incomplete source."""


@dataclass
class FetchRecord:
    """What was actually retrieved, for the report. Never holds headers or page bodies."""

    method: str  # "http" | "browser" | "bundled-file"
    url: str
    final_url: str = ""
    status: int | None = None
    fetched_at: str = ""
    content_type: str = ""
    raw_bytes: int = 0
    attempts: int = 1


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class HttpFetcher:
    """HTTP client with a per-host robots.txt cache, bounded retries and a provenance record of
    the last request. Reused for a whole run."""

    def __init__(self, client: httpx.Client | None = None, sleep=time.sleep):
        self.client = client or httpx.Client(
            headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT, follow_redirects=True
        )
        self._robots: dict[str, robotparser.RobotFileParser | None] = {}
        self._sleep = sleep
        self.last: FetchRecord | None = None

    def allowed(self, url: str) -> bool:
        p = urlparse(url)
        host = f"{p.scheme}://{p.netloc}"
        if host not in self._robots:
            rp = robotparser.RobotFileParser()
            try:
                r = self.client.get(f"{host}/robots.txt")
                if r.status_code >= 400:
                    self._robots[host] = None
                else:
                    rp.parse(r.text.splitlines())
                    self._robots[host] = rp
            except httpx.HTTPError:
                self._robots[host] = None  # unreachable robots.txt: treated as allow
        rp = self._robots[host]
        return rp is None or rp.can_fetch(USER_AGENT, url)

    def get(self, url: str) -> str:
        self.last = FetchRecord("http", url)
        if not self.allowed(url):
            raise FetchError(f"robots.txt disallows {url}")
        last_err: Exception | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            self.last.attempts = attempt
            try:
                r = self.client.get(url)
                self.last.status, self.last.final_url = r.status_code, str(r.url)
                self.last.fetched_at = utc_now_iso()
                self.last.content_type = r.headers.get("content-type", "").split(";")[0]
                self.last.raw_bytes = len(r.content)
                if r.status_code in RETRY_STATUS and attempt < MAX_ATTEMPTS:
                    last_err = httpx.HTTPStatusError(f"HTTP {r.status_code}", request=r.request, response=r)
                else:
                    r.raise_for_status()
                    return r.text
            except httpx.TimeoutException as e:
                last_err = e
                if attempt == MAX_ATTEMPTS:
                    raise FetchError(f"timeout fetching {url} after {attempt} attempt(s)") from e
            except httpx.HTTPStatusError as e:
                if e.response.status_code not in RETRY_STATUS:  # 4xx etc: retrying will not help
                    raise FetchError(f"HTTP {e.response.status_code} fetching {url}") from e
                last_err = e
            except httpx.HTTPError as e:
                last_err = e
                if attempt == MAX_ATTEMPTS:
                    raise FetchError(f"network error fetching {url}: {type(e).__name__}") from e
            if attempt < MAX_ATTEMPTS:
                wait = BACKOFF_SECONDS[min(attempt - 1, len(BACKOFF_SECONDS) - 1)]
                log.warning("%s: attempt %d failed (%s); retrying in %.0fs", url, attempt,
                            type(last_err).__name__, wait)
                self._sleep(wait)
        raise FetchError(f"giving up on {url} after {MAX_ATTEMPTS} attempts: {last_err}")

    def close(self) -> None:
        self.client.close()
