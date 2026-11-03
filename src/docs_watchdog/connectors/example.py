"""EXAMPLE ONLY: the smallest possible custom extraction strategy.

It exists to show the contract and is exercised by tests/test_example_connector.py. It is NOT one
of the production vendor connectors (Meta Ads, Google Ads, LinkedIn Marketing).

A strategy is a function `raw HTTP body -> normalized text`. Keep it deterministic and side-effect
free; everything else (fetching, retries, baselines, comparison, rules, reports) is shared.
"""
from __future__ import annotations

from ..extract import normalize
from ..extractors import register_strategy


def example_lines(raw: str) -> str:
    """Plain-text changelog where each non-empty line is one entry; '#' lines are comments."""
    return normalize("\n".join(l for l in raw.splitlines() if not l.lstrip().startswith("#")))


register_strategy("example-lines", example_lines)
