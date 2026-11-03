import json

import httpx
import pytest
from typer.testing import CliRunner

from docs_watchdog.classify import ClassificationError, classify
from docs_watchdog.cli import app
from docs_watchdog.demo import run_demo
from docs_watchdog.llm import (AnthropicProvider, DemoProvider, LLMConfigError,
                          OpenAICompatibleProvider, provider_from_env)

DIFF = "The `old_api` endpoint is deprecated and will be removed on 2027-03-31. Use `new_api`."


# ---- configuration -------------------------------------------------------------------
def test_no_provider_configured_explains_options():
    with pytest.raises(LLMConfigError, match="--demo"):
        provider_from_env({})


def test_demo_provider_needs_no_key():
    assert isinstance(provider_from_env({"LLM_PROVIDER": "demo"}), DemoProvider)


def test_openai_compatible_from_env_with_base_url():
    p = provider_from_env({"LLM_PROVIDER": "openai", "LLM_API_KEY": "k", "LLM_MODEL": "m",
                           "LLM_BASE_URL": "http://localhost:11434/v1"})
    assert isinstance(p, OpenAICompatibleProvider) and p.model == "m"
    assert p.base_url == "http://localhost:11434/v1"


def test_anthropic_from_generic_env():
    p = provider_from_env({"LLM_PROVIDER": "anthropic", "LLM_API_KEY": "k"})
    assert isinstance(p, AnthropicProvider) and p.name.startswith("anthropic:")


def test_legacy_anthropic_key_still_works():
    assert isinstance(provider_from_env({"ANTHROPIC_API_KEY": "k"}), AnthropicProvider)


@pytest.mark.parametrize("env", [{"LLM_PROVIDER": "openai"}, {"LLM_PROVIDER": "nope", "LLM_API_KEY": "k"}])
def test_invalid_config_rejected(env):
    with pytest.raises(LLMConfigError):
        provider_from_env(env)


# ---- providers -----------------------------------------------------------------------
def test_openai_compatible_request_and_response_shape():
    seen = {}

    def handler(request: httpx.Request):
        seen["url"], seen["auth"] = str(request.url), request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"findings": []}'}}]})

    p = OpenAICompatibleProvider("secret", "m", "http://gw/v1/",
                                 http=httpx.Client(transport=httpx.MockTransport(handler)))
    assert p.complete("sys", "usr") == '{"findings": []}'
    assert seen["url"] == "http://gw/v1/chat/completions" and seen["auth"] == "Bearer secret"
    assert seen["body"]["messages"][0] == {"role": "system", "content": "sys"}


def test_provider_http_error_becomes_classification_error_without_key_leak():
    p = OpenAICompatibleProvider("TOPSECRET", http=httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(500))))
    with pytest.raises(ClassificationError) as e:
        classify(DIFF, "c", "s", "u", provider=p)
    assert "TOPSECRET" not in str(e.value)


def test_same_validation_applies_to_every_provider():
    class Liar:
        name, is_demo = "fake", False

        def complete(self, s, u):
            return json.dumps({"findings": [{"change_type": "deprecation", "severity": "high",
                "evidence": "text that is not in the diff 2030", "deadline": "2030-01-01",
                "confirmed": True}]})

    f = classify(DIFF, "c", "s", "u", provider=Liar())[0]
    assert not f.evidence_verified and f.deadline is None and f.classifier == "fake"


def test_classify_without_provider_errors():
    with pytest.raises(ClassificationError):
        classify(DIFF, "c", "s", "u")


# ---- demo mode -----------------------------------------------------------------------
def test_demo_provider_is_deterministic_and_evidence_verbatim():
    a, b = DemoProvider().complete("", "Connector: x\n\n" + DIFF), DemoProvider().complete("", "Connector: x\n\n" + DIFF)
    assert a == b
    f = classify(DIFF, "c", "s", "u", provider=DemoProvider())[0]
    assert f.deadline == "2027-03-31" and f.evidence_verified and f.classifier == "demo-rules"


def test_demo_ignores_non_matching_text():
    assert classify("A new optional metric was added to the reporting endpoint.", "c", "s", "u",
                    provider=DemoProvider()) == []


def test_run_demo_detects_the_two_synthetic_deprecations():
    text, res = run_demo()
    areas = {f.connector: f for f in res.findings}
    assert areas["Sample Platform A (synthetic)"].deadline == "2027-09-30"
    assert areas["Example Ads API (synthetic)"].deadline == "2027-03-31"
    assert not res.errors and "DEMONSTRATION MODE" in text and "not AI output" in text
    assert "synthetic and illustrative" in text
    assert not any(h in text for h in ("facebook.com", "google.com", "microsoft.com", "linkedin.com"))  # no vendor URLs/text


def test_demo_is_reproducible():
    assert run_demo()[0].split("\n", 1)[1] == run_demo()[0].split("\n", 1)[1]  # ignoring date line


def test_cli_demo_end_to_end(tmp_path):
    r = CliRunner().invoke(app, ["--demo", "--out", str(tmp_path)])
    assert r.exit_code == 0
    report = (tmp_path / "demo-report.md").read_text()
    assert "[HIGH]" in report and "deterministic-rules" in report


def test_cli_without_provider_runs_deterministic_and_reports(monkeypatch, tmp_path):
    """No LLM configuration is a normal mode, not an error."""
    for k in ("LLM_PROVIDER", "LLM_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    cfg = tmp_path / "c.yaml"
    cfg.write_text("connectors:\n  - name: A\n    sources: [{id: s, type: markdown, verified: true, "
                   "url: 'https://x/doc.md', min_chars: 5}]\n")
    page = {"v": "<html><body><pre># T\nentry one long enough\n</pre></body></html>"}
    import docs_watchdog.cli as cli

    class FakeHttp:
        def get(self, url):
            return page["v"]

        def close(self):
            pass

    monkeypatch.setattr(cli, "HttpFetcher", FakeHttp)
    args = ["--config", str(cfg), "--state-dir", str(tmp_path / "st"), "--out", str(tmp_path / "rep")]
    assert CliRunner().invoke(app, args).exit_code == 0  # baseline
    page["v"] = page["v"].replace("</pre>", "The v1 API is deprecated and ends on 2027-01-31.\n</pre>")
    r = CliRunner().invoke(app, args)
    assert r.exit_code == 0
    latest = (tmp_path / "rep" / "latest.md").read_text()
    assert "Run status: COMPLETE" in latest and "deterministic (no AI)" in latest
    assert "2027-01-31" in latest and "Deprecations (1)" in latest


def test_cli_configured_but_invalid_provider_is_still_an_error(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    assert CliRunner().invoke(app, []).exit_code == 2
