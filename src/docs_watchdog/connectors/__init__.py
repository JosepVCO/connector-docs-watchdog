"""Connector-specific Python, only when YAML options are not enough.

Most connectors need no Python at all: a connector is an entry in `connectors.yaml` using the
built-in strategies plus per-source options (`expect_text`, `drop_sections`, `ignore_patterns`,
`selector`, `fallback`). If a vendor's pages need a parser of their own, add a module here that calls
`docs_watchdog.extractors.register_strategy(...)` and import it below (one explicit line per module).
"""
from . import example  # noqa: F401  (EXAMPLE ONLY: shows the contract; no production connector uses it)
