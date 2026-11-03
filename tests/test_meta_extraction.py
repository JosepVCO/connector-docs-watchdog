import pytest

from conftest import FakeBrowser, FakeHttp, md_source
from docs_watchdog.extractors import SourceExtractor
from docs_watchdog.fetch import EmptyContentError, SourceError
from docs_watchdog.models import Source

URL = "https://x/doc.md"


def test_meta_markdown_success(fx):
    text = SourceExtractor(FakeHttp({URL: fx("meta_v25_changelog.md.html")})).extract(md_source())
    # fixture is SYNTHETIC text on the structure of Meta's Markdown-in-<pre> pages
    assert "Bundle Campaigns and Bundle App Campaigns deprecation" in text
    assert "from June 30, 2027, for every earlier version" in text
    assert "<pre" not in text and "&quot;" not in text  # wrapper stripped, entities decoded
    assert '"Job Skipped"' in text  # &quot; decoded
    assert "{ad-account-id}" in text  # &#123; decoded


def test_js_rendered_shell_is_not_valid_content(fx):
    src = Source(id="s", type="http", url="https://x/p", min_chars=200)
    with pytest.raises(EmptyContentError):
        SourceExtractor(FakeHttp({"https://x/p": fx("meta_js_shell.html")})).extract(src)


def test_empty_page():
    with pytest.raises(EmptyContentError):
        SourceExtractor(FakeHttp({URL: "<html><body><pre></pre></body></html>"})).extract(md_source())


def test_network_failure():
    http = FakeHttp({URL: SourceError("network/HTTP error fetching")})
    with pytest.raises(SourceError, match="network"):
        SourceExtractor(http).extract(md_source())


def test_timeout_on_browser_source():
    src = Source(id="s", type="playwright", url="https://x/p", selector="article")
    br = FakeBrowser(error=SourceError("timeout rendering https://x/p"))
    with pytest.raises(SourceError, match="timeout"):
        SourceExtractor(FakeHttp({}), br).extract(src)


def test_unexpected_html_structure_rejected(fx):
    with pytest.raises(SourceError, match="unexpected structure"):
        SourceExtractor(FakeHttp({URL: fx("meta_soft_404.md.html")})).extract(md_source())


def test_fallback_used_when_primary_fails(fx):
    fb = Source(id="fb", type="playwright", url="https://x/p", selector="article")
    src = md_source(fallback=fb)
    browser = FakeBrowser(text="Marketing API changelog\n" + "entry line\n" * 40)
    out = SourceExtractor(FakeHttp({URL: fx("meta_soft_404.md.html")}), browser).extract(src)
    assert "entry line" in out and browser.calls == 1


def test_browser_not_touched_when_primary_succeeds(fx):
    fb = Source(id="fb", type="playwright", url="https://x/p", selector="article")
    browser = FakeBrowser(text="unused")
    SourceExtractor(FakeHttp({URL: fx("meta_v25_changelog.md.html")}), browser).extract(
        md_source(fallback=fb)
    )
    assert browser.calls == 0


def test_no_fallback_error_propagates_when_both_fail(fx):
    fb = Source(id="fb", type="playwright", url="https://x/p", selector="article")
    br = FakeBrowser(error=EmptyContentError("selector not found"))
    with pytest.raises(SourceError):
        SourceExtractor(FakeHttp({URL: fx("meta_soft_404.md.html")}), br).extract(md_source(fallback=fb))


def test_openapi_flags_deprecated():
    spec = '{"info":{"version":"2"},"paths":{"/a":{"get":{"summary":"List","deprecated":true}}}}'
    out = SourceExtractor(FakeHttp({"https://x/o": spec})).extract(
        Source(id="o", type="openapi", url="https://x/o", min_chars=5)
    )
    assert "GET /a DEPRECATED List" in out
