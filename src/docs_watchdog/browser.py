from __future__ import annotations

import logging

from .fetch import USER_AGENT, EmptyContentError, FetchError, FetchRecord, SourceError, utc_now_iso

log = logging.getLogger("docs_watchdog.browser")


class BrowserSession:
    """Lazily launches one headless Chromium and reuses it; closed via close()/with."""

    def __init__(self, timeout_ms: int = 45000, settle_ms: int = 1500):
        self.timeout_ms = timeout_ms
        self.settle_ms = settle_ms
        self._pw = None
        self._browser = None
        self.last: FetchRecord | None = None

    def _start(self):
        if self._browser:
            return
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as e:
            raise SourceError("playwright not installed: pip install -e '.[browser]'") from e
        log.info("launching headless Chromium")
        self._pw = sync_playwright().start()
        try:
            self._browser = self._pw.chromium.launch()
        except Exception as e:
            self.close()
            raise SourceError(f"cannot launch Chromium (run 'playwright install chromium'): {e}") from e

    def render_text(self, url: str, selector: str) -> str:
        self._start()
        from playwright.sync_api import Error as PWError
        from playwright.sync_api import TimeoutError as PWTimeout

        page = self._browser.new_page(user_agent=USER_AGENT)
        self.last = FetchRecord("browser", url)
        try:
            resp = page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
            self.last.status = resp.status if resp else None
            self.last.final_url, self.last.fetched_at = page.url, utc_now_iso()
            page.wait_for_selector(selector, timeout=self.timeout_ms)
            page.wait_for_timeout(self.settle_ms)  # let client-side rendering finish
            if page.locator(selector).count() == 0:
                raise EmptyContentError(f"selector '{selector}' not found on {url}")
            return page.locator(selector).first.inner_text()
        except PWTimeout as e:
            raise FetchError(f"timeout rendering {url} (selector '{selector}')") from e
        except PWError as e:
            raise FetchError(f"browser error on {url}: {e}") from e
        finally:
            page.close()

    def close(self) -> None:
        if self._browser:
            self._browser.close()
        if self._pw:
            self._pw.stop()
        self._browser = self._pw = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
