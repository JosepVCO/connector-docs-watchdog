import pytest

from docs_watchdog.config import ConfigError, load_connectors


def write(tmp_path, body):
    p = tmp_path / "c.yaml"
    p.write_text(body)
    return p


def test_repo_config_is_valid():
    from pathlib import Path

    cs = load_connectors(Path(__file__).parent.parent / "connectors.yaml", only_enabled=False)
    meta = next(c for c in cs if c.name == "Meta Ads")
    assert meta.sources[0].type == "markdown" and meta.sources[0].fallback.type == "playwright"


@pytest.mark.parametrize("src", [
    "{id: a, type: ftp, url: 'https://x'}",
    "{id: a, type: http, url: 'http://x'}",
    "{id: a, type: playwright, url: 'https://x'}",
    "{id: a, type: http, url: 'https://x', typo: 1}",
])
def test_invalid_sources_rejected(tmp_path, src):
    with pytest.raises(ConfigError):
        load_connectors(write(tmp_path, f"connectors:\n  - name: A\n    sources: [{src}]\n"))


def test_year_placeholder_expanded(tmp_path):
    p = write(tmp_path, "connectors:\n  - name: A\n    sources: [{id: a, type: http, url: 'https://x/{year}'}]\n")
    assert "{year}" not in load_connectors(p)[0].sources[0].url
