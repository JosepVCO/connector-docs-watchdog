"""LLM provider abstraction.

Configuration (environment only; never in connectors.yaml):
  LLM_PROVIDER  anthropic | openai | demo      (openai = any OpenAI-compatible endpoint)
  LLM_API_KEY   credential for the chosen provider
  LLM_MODEL     model name
  LLM_BASE_URL  optional endpoint override (Azure/OpenRouter/Ollama/vLLM/gateway ...)

A provider only turns (system, user) text into a response string. Everything that makes the
result trustworthy (JSON validation, verbatim-evidence check, deadline rules) lives in
classify.py and applies identically to every provider.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

import httpx

DEFAULT_MODELS = {"anthropic": "claude-sonnet-5-5", "openai": "gpt-4o-mini"}


class LLMConfigError(ValueError):
    """Provider configuration is missing or invalid."""


class LLMTruncatedError(Exception):
    """The model stopped because of its output limit: the response is incomplete."""


class LLMProvider(Protocol):
    name: str  # recorded on every finding
    is_demo: bool

    def complete(self, system: str, user: str) -> str: ...


@dataclass
class AnthropicProvider:
    api_key: str | None = field(default=None, repr=False)
    model: str = DEFAULT_MODELS["anthropic"]
    base_url: str | None = None
    client: object | None = None  # injectable for tests
    is_demo: bool = False

    @property
    def name(self) -> str:
        return f"anthropic:{self.model}"

    def complete(self, system: str, user: str) -> str:
        if self.client is None:
            try:
                import anthropic
            except ImportError as e:
                raise LLMConfigError("pip install -e '.[anthropic]' for this provider") from e
            self.client = anthropic.Anthropic(api_key=self.api_key, base_url=self.base_url)
        msg = self.client.messages.create(
            model=self.model, max_tokens=4096, system=system,
            messages=[{"role": "user", "content": user}],
        )
        if getattr(msg, "stop_reason", None) == "max_tokens":
            raise LLMTruncatedError("response hit the output token limit")
        return "".join(getattr(b, "text", "") for b in msg.content)


@dataclass
class OpenAICompatibleProvider:
    api_key: str = field(repr=False)
    model: str = DEFAULT_MODELS["openai"]
    base_url: str = "https://api.openai.com/v1"
    http: httpx.Client | None = None  # injectable for tests
    is_demo: bool = False

    @property
    def name(self) -> str:
        return f"openai-compatible:{self.model}"

    def complete(self, system: str, user: str) -> str:
        http = self.http or httpx.Client(timeout=60)
        r = http.post(
            self.base_url.rstrip("/") + "/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"model": self.model, "temperature": 0,
                  "messages": [{"role": "system", "content": system},
                               {"role": "user", "content": user}]},
        )
        r.raise_for_status()
        choice = r.json()["choices"][0]
        if choice.get("finish_reason") == "length":
            raise LLMTruncatedError("response hit the output token limit")
        return choice["message"]["content"]


# --- Deterministic demonstration mode -------------------------------------------------
_DATE = re.compile(
    r"\b(?:(?P<iso>\d{4}-\d{2}-\d{2})|(?P<mdy>(?:January|February|March|April|May|June|July|"
    r"August|September|October|November|December) \d{1,2}, \d{4}))\b"
)
_BREAKING = re.compile(r"\b(breaking change|removed|no longer (?:supported|available|work)|"
                       r"will stop working)\b", re.I)
_DEPRECATION = re.compile(r"\b(deprecat\w*|sunset\w*|will be retired|end of life|"
                          r"available until)\b", re.I)


def _to_iso(match: re.Match) -> str | None:
    try:
        if match.group("iso"):
            return datetime.strptime(match.group("iso"), "%Y-%m-%d").date().isoformat()
        return datetime.strptime(match.group("mdy"), "%B %d, %Y").date().isoformat()
    except ValueError:
        return None


class DemoProvider:
    """Rule-based keyword classifier. NOT an AI model: it exists so the whole workflow can
    be demonstrated offline and deterministically. Its output goes through the same
    validation as real LLM output and is labelled 'demo-rules' in every report."""

    name = "demo-rules"
    is_demo = True

    def complete(self, system: str, user: str) -> str:
        text = user.split("\n\n", 1)[-1]
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        findings, covered = [], -1
        for i, line in enumerate(lines):
            if i <= covered:  # already part of the previous finding's evidence window
                continue
            dep, brk = _DEPRECATION.search(line), _BREAKING.search(line)
            if len(line) < 25 or not (dep or brk):
                continue
            # Evidence = the matching line, plus the next one only if that is where the date is.
            window = lines[i : i + 1]
            if not _DATE.search(line) and i + 1 < len(lines) and _DATE.search(lines[i + 1]):
                window = lines[i : i + 2]
            covered = i + len(window) - 1
            evidence = "\n".join(window)
            m = _DATE.search(evidence)
            findings.append({
                "change_type": "deprecation" if dep else "breaking",
                "severity": "high" if m else "medium",
                "summary": f"Keyword match ({(dep or brk).group(0)!r}) in newly added text.",
                "impact": "Rule-based: may affect connectors using the mentioned feature; review manually.",
                "affected_area": line.lstrip("# ")[:80],
                "confirmed": bool(m),
                "deadline": _to_iso(m) if m else None,
                "evidence": evidence,
                "recommended_action": "Review the vendor notice and check connector usage.",
                "confidence": 0.6,
            })
        return json.dumps({"findings": findings})


def llm_configured(env: dict[str, str] | None = None) -> bool:
    """True when the environment asks for an LLM provider (AI-enhanced mode)."""
    env = dict(os.environ) if env is None else env
    return bool((env.get("LLM_PROVIDER") or "").strip() or env.get("ANTHROPIC_API_KEY"))


def provider_from_env(env: dict[str, str] | None = None) -> LLMProvider:
    env = dict(os.environ) if env is None else env
    provider = (env.get("LLM_PROVIDER") or "").strip().lower()
    key = env.get("LLM_API_KEY") or ""
    if not provider and env.get("ANTHROPIC_API_KEY"):  # legacy convenience
        provider, key = "anthropic", env["ANTHROPIC_API_KEY"]
    if not provider:
        raise LLMConfigError(
            "LLM_PROVIDER is not set (anthropic | openai | demo). Without it the tool runs in "
            "deterministic mode; use --demo for the offline demonstration."
        )
    model, base = env.get("LLM_MODEL") or None, env.get("LLM_BASE_URL") or None
    if provider == "demo":
        return DemoProvider()
    if provider not in DEFAULT_MODELS:
        raise LLMConfigError(f"unknown LLM_PROVIDER {provider!r}; use anthropic | openai | demo")
    if not key:
        raise LLMConfigError(f"LLM_API_KEY is required for provider {provider!r}")
    model = model or DEFAULT_MODELS[provider]
    if provider == "anthropic":
        return AnthropicProvider(api_key=key, model=model, base_url=base)
    return OpenAICompatibleProvider(api_key=key, model=model, base_url=base or "https://api.openai.com/v1")
