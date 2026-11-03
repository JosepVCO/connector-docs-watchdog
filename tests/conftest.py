from pathlib import Path

import pytest

from docs_watchdog.fetch import SourceError
from docs_watchdog.models import Source
from docs_watchdog.store import Store

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fx():
    return lambda name: (FIXTURES / name).read_text()


@pytest.fixture
def store():
    return Store(Path(":memory:"))


class FakeHttp:
    """Stands in for HttpFetcher: url -> body, or an Exception to raise."""

    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def get(self, url):
        self.calls.append(url)
        v = self.pages[url]
        if isinstance(v, Exception):
            raise v
        return v


class FakeBrowser:
    def __init__(self, text=None, error=None):
        self.text, self.error, self.calls = text, error, 0

    def render_text(self, url, selector):
        self.calls += 1
        if self.error:
            raise self.error
        return self.text


def md_source(url="https://x/doc.md", **kw):
    return Source(id="s", type="markdown", url=url, **kw)


__all__ = ["FakeHttp", "FakeBrowser", "md_source", "SourceError"]
