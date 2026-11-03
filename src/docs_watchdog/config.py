from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import yaml

from . import connectors as _connectors  # noqa: F401  (registers custom strategies)
from .extractors import STRATEGIES
from .models import Connector, Source


class ConfigError(ValueError):
    pass


def _parse_source(raw: dict, where: str) -> Source:
    raw = dict(raw)
    fb = raw.pop("fallback", None)
    unknown = set(raw) - {"id", "type", "url", "verified", "selector", "min_chars", "min_length_ratio",
                         "expect_text", "drop_sections", "ignore_patterns"}
    if unknown:
        raise ConfigError(f"{where}: unknown keys {sorted(unknown)}")
    for key in ("id", "type", "url"):
        if not raw.get(key):
            raise ConfigError(f"{where}: missing '{key}'")
    valid_types = sorted(set(STRATEGIES) | {"playwright"})
    if raw["type"] not in valid_types:
        raise ConfigError(f"{where}: type '{raw['type']}' not in {valid_types}")
    for key in ("expect_text", "drop_sections", "ignore_patterns"):
        v = raw.get(key, [])
        if not isinstance(v, list) or not all(isinstance(x, str) and x for x in v):
            raise ConfigError(f"{where}: '{key}' must be a list of non-empty strings")
    for pat in raw.get("ignore_patterns", []):
        try:
            re.compile(pat)
        except re.error as e:
            raise ConfigError(f"{where}: invalid ignore_patterns regex {pat!r}: {e}") from e
    raw["url"] = raw["url"].replace("{year}", str(date.today().year))
    if not raw["url"].startswith("https://"):
        raise ConfigError(f"{where}: url must be https")
    ratio = raw.get("min_length_ratio", 0.5)
    if isinstance(ratio, bool) or not isinstance(ratio, (int, float)) or not 0 <= ratio < 1:
        raise ConfigError(f"{where}: min_length_ratio must be a number in [0, 1)")
    src = Source(**raw)
    if src.type == "playwright" and not src.selector:
        raise ConfigError(f"{where}: playwright sources need a 'selector'")
    if fb:
        fb.setdefault("id", f"{src.id}-fallback")
        fb.setdefault("url", src.url)
        src.fallback = _parse_source(fb, f"{where}.fallback")
    return src


def load_connectors(path: Path, only_enabled: bool = True) -> list[Connector]:
    try:
        raw = yaml.safe_load(path.read_text())["connectors"]
    except (OSError, yaml.YAMLError, KeyError, TypeError) as e:
        raise ConfigError(f"cannot read {path}: {e}") from e
    out, names, conn_ids = [], set(), set()
    for c in raw:
        name = c.get("name")
        if not name or name in names:
            raise ConfigError(f"connector name missing or duplicated: {name!r}")
        names.add(name)
        cid = c.get("id") or re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        if cid in conn_ids:
            raise ConfigError(f"connector id duplicated: {cid!r}")
        conn_ids.add(cid)
        srcs = [_parse_source(s, f"{name}/{s.get('id', '?')}") for s in c.get("sources", [])]
        ids = [s.id for s in srcs]
        if not srcs or len(ids) != len(set(ids)):
            raise ConfigError(f"{name}: needs at least one source with unique ids")
        conn = Connector(name, c.get("priority", 3), c.get("enabled", True), srcs, id=cid,
                         vendor=c.get("vendor", ""), scope=c.get("scope", ""),
                         example=bool(c.get("example", False)))
        if conn.enabled or not only_enabled:
            out.append(conn)
    return sorted(out, key=lambda c: c.priority)
