"""OpenAPI (3.x and Swagger 2) support: canonical rendering and structural comparison.

`render` turns a spec into stable, line-oriented "facts". That text is what gets stored as the
snapshot, so snapshots stay plain text and diff cleanly in Git. `diff_facts` parses two
renderings back into facts and reports structural changes (operations, parameters, request
bodies, responses, schema fields, types, required flags, deprecation).

Line grammar (values never contain spaces or " | "):

    version: 1.2.0
    GET /pets [DEPRECATED] <summary>                      operation line
    GET /pets | operationId | value=listPets
    GET /pets | param query:limit | required=false type=integer
    GET /pets | body application/json | required=true type=Pet
    GET /pets | response 200 application/json | type=array<Pet>
    schema Pet | field owner.name | required=true type=string
"""
from __future__ import annotations

import json
import re
from typing import Any

import yaml

from .diff import Change

METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")
_OP_LINE = re.compile(
    r"^(GET|PUT|POST|DELETE|OPTIONS|HEAD|PATCH|TRACE) (\S+)(?: (DEPRECATED))?(?: (.*))?$"
)
_MAX_FIELD_DEPTH = 4


def _clean(value: Any) -> str:
    """Single-line text with no pipes (the fact separator)."""
    return re.sub(r"[\s|]+", " ", str(value)).strip()


def _tok(value: Any) -> str:
    """Attribute value: no whitespace so `k=v k=v` stays tokenisable."""
    return re.sub(r"\s+", "_", _clean(value)) or "-"


class _Resolver:
    def __init__(self, spec: dict):
        self.spec = spec

    def deref(self, node: Any, _seen: tuple = ()) -> Any:
        """Follow local `$ref`s (bounded; cycles return the unresolved node)."""
        while isinstance(node, dict) and isinstance(node.get("$ref"), str):
            ref = node["$ref"]
            if not ref.startswith("#/") or ref in _seen:
                return node
            _seen += (ref,)
            cur: Any = self.spec
            for part in ref[2:].split("/"):
                part = part.replace("~1", "/").replace("~0", "~")
                if not isinstance(cur, dict) or part not in cur:
                    return node
                cur = cur[part]
            node = cur
        return node

    def type_of(self, schema: Any, depth: int = 0) -> str:
        if not isinstance(schema, dict):
            return "any"
        if "$ref" in schema:
            return _tok(str(schema["$ref"]).rsplit("/", 1)[-1])
        if depth > 6:
            return "any"
        if "enum" in schema and isinstance(schema["enum"], list):
            return "enum(" + ",".join(sorted(_tok(v) for v in schema["enum"])) + ")"
        for key in ("oneOf", "anyOf", "allOf"):
            if isinstance(schema.get(key), list):
                parts = [self.type_of(s, depth + 1) for s in schema[key]]
                return f"{key}(" + ",".join(sorted(parts) if key != "allOf" else parts) + ")"
        t = schema.get("type")
        if t == "array":
            return f"array<{self.type_of(schema.get('items'), depth + 1)}>"
        if isinstance(t, list):
            t = "|".join(sorted(_tok(x) for x in t))
        if t is None:
            t = "object" if "properties" in schema else "any"
        t = _tok(t)
        if schema.get("nullable") is True:
            t += "?"
        return f"{t}:{_tok(schema['format'])}" if schema.get("format") else t


def _attrs(**kw: Any) -> str:
    return " ".join(f"{k}={v}" for k, v in kw.items())


def _fields(res: _Resolver, schema: Any, prefix: str = "", depth: int = 0, seen: tuple = ()):
    """Yield (path, required, type) for the properties of an object schema (bounded depth)."""
    schema = res.deref(schema)
    if not isinstance(schema, dict) or depth > _MAX_FIELD_DEPTH:
        return
    parts = [schema] + [res.deref(s) for s in schema.get("allOf", []) if isinstance(s, (dict,))]
    required: set[str] = set()
    props: dict[str, Any] = {}
    for part in parts:
        if not isinstance(part, dict):
            continue
        required |= {str(r) for r in part.get("required", []) if isinstance(r, str)}
        if isinstance(part.get("properties"), dict):
            props.update(part["properties"])
    for name in sorted(props):
        sub = props[name]
        path = f"{prefix}{_tok(name)}"
        yield path, name in required, res.type_of(sub)
        inner = res.deref(sub)
        if isinstance(inner, dict) and id(inner) not in seen:
            nxt = inner
            if inner.get("type") == "array" and isinstance(inner.get("items"), dict):
                nxt, path = inner["items"], path + "[]"
            yield from _fields(res, nxt, path + ".", depth + 1, seen + (id(inner),))


def _params(res: _Resolver, shared: list, own: list) -> list[dict]:
    merged: dict[tuple, dict] = {}
    for raw in list(shared or []) + list(own or []):
        p = res.deref(raw)
        if isinstance(p, dict) and p.get("name") and p.get("in"):
            merged[(p["in"], p["name"])] = p  # operation-level overrides path-level
    return list(merged.values())


def render(raw: str) -> str:
    spec = json.loads(raw) if raw.lstrip().startswith("{") else yaml.safe_load(raw)
    if not isinstance(spec, dict):
        raise ValueError("OpenAPI document is not a mapping")
    res = _Resolver(spec)
    lines = [f"version: {_tok((spec.get('info') or {}).get('version', '?'))}"]
    paths = spec.get("paths") or {}
    for path, item in sorted(paths.items()):
        if not isinstance(item, dict):
            continue
        for method in sorted(m for m in item if str(m).lower() in METHODS):
            op = item[method]
            if not isinstance(op, dict):
                continue
            subject = f"{str(method).upper()} {path}"
            flag = " DEPRECATED" if op.get("deprecated") else ""
            lines.append(f"{subject}{flag} {_clean(op.get('summary', ''))}".strip())
            if op.get("operationId"):
                lines.append(f"{subject} | operationId | value={_tok(op['operationId'])}")
            for p in _params(res, item.get("parameters"), op.get("parameters")):
                if p["in"] == "body":  # Swagger 2 body parameter
                    lines.append(
                        f"{subject} | body {_tok(p['name'])} | "
                        + _attrs(required=str(bool(p.get("required"))).lower(),
                                 type=res.type_of(p.get("schema")))
                    )
                    continue
                schema = p.get("schema") if "schema" in p else p  # Swagger 2 inlines the type
                required = bool(p.get("required")) or p["in"] == "path"
                lines.append(
                    f"{subject} | param {_tok(p['in'])}:{_tok(p['name'])} | "
                    + _attrs(required=str(required).lower(), type=res.type_of(schema))
                )
            body = res.deref(op.get("requestBody"))
            if isinstance(body, dict):
                for ctype, media in sorted((body.get("content") or {}).items()):
                    schema = media.get("schema") if isinstance(media, dict) else None
                    lines.append(
                        f"{subject} | body {_tok(ctype)} | "
                        + _attrs(required=str(bool(body.get("required"))).lower(),
                                 type=res.type_of(schema))
                    )
            for status, resp in sorted((op.get("responses") or {}).items(), key=lambda kv: str(kv[0])):
                resp = res.deref(resp)
                if not isinstance(resp, dict):
                    continue
                content = resp.get("content")
                if isinstance(content, dict) and content:
                    for ctype, media in sorted(content.items()):
                        schema = media.get("schema") if isinstance(media, dict) else None
                        lines.append(f"{subject} | response {_tok(status)} {_tok(ctype)} | "
                                     + _attrs(type=res.type_of(schema)))
                else:  # no body (OpenAPI 3) or Swagger 2 `schema`
                    lines.append(f"{subject} | response {_tok(status)} | "
                                 + _attrs(type=res.type_of(resp["schema"]) if "schema" in resp else "none"))
    schemas = (spec.get("components") or {}).get("schemas") or spec.get("definitions") or {}
    for name, schema in sorted(schemas.items()):
        subject = f"schema {_tok(name)}"
        found = False
        for path, required, typ in _fields(res, schema):
            found = True
            lines.append(f"{subject} | field {path} | " + _attrs(required=str(required).lower(), type=typ))
        if not found:
            lines.append(f"{subject} | type | " + _attrs(value=res.type_of(res.deref(schema))))
    return "\n".join(lines)


# ---- structural comparison ---------------------------------------------------------------
def _parse_attrs(text: str) -> dict[str, str]:
    return dict(tok.split("=", 1) for tok in text.split() if "=" in tok)


def is_facts(text: str) -> bool:
    """True for text produced by `render` (always starts with the version line)."""
    return text.startswith("version: ")


def parse(text: str):
    """-> (version, ops, details). ops: {(METHOD, path): (deprecated, summary, line)};
    details: {(subject, item): (attrs, line)}."""
    version, ops, details = None, {}, {}
    for line in text.splitlines():
        if line.startswith("version: "):
            version = line[len("version: "):]
        elif " | " in line:
            parts = line.split(" | ", 2)
            if len(parts) == 3:
                details[(parts[0], parts[1])] = (_parse_attrs(parts[2]), line)
        else:
            m = _OP_LINE.match(line)
            if m:
                ops[(m.group(1), m.group(2))] = (bool(m.group(3)), m.group(4) or "", line)
    return version, ops, details


def diff_facts(old: str, new: str) -> list[Change]:
    ov, oops, odet = parse(old)
    nv, nops, ndet = parse(new)
    out: list[Change] = []

    if ov != nv:
        out.append(Change("modified", old=f"version: {ov}", new=f"version: {nv}", domain="openapi",
                          facet="version", entity="API version"))

    added_ops, removed_ops = set(nops) - set(oops), set(oops) - set(nops)
    for key in sorted(added_ops):
        out.append(Change("added", new=nops[key][2], domain="openapi", facet="operation",
                          entity=" ".join(key)))
    for key in sorted(removed_ops):
        out.append(Change("removed", old=oops[key][2], domain="openapi", facet="operation",
                          entity=" ".join(key)))
    for key in sorted(set(oops) & set(nops)):
        (od, osum, oline), (nd, nsum, nline) = oops[key], nops[key]
        if od != nd:
            out.append(Change("modified", old=oline, new=nline, domain="openapi", facet="deprecated",
                              entity=" ".join(key), old_attrs={"deprecated": od},
                              new_attrs={"deprecated": nd}))
        elif osum != nsum:
            out.append(Change("modified", old=oline, new=nline, domain="openapi", facet="summary",
                              entity=" ".join(key)))

    gone = {" ".join(k) for k in added_ops | removed_ops}  # details of wholly added/removed ops
    old_subjects = {s for s, _ in odet}
    new_subjects = {s for s, _ in ndet}
    for subject in sorted(old_subjects ^ new_subjects):
        if subject.startswith("schema "):
            if subject in new_subjects:
                lines = [ln for (s, _), (_, ln) in ndet.items() if s == subject]
                out.append(Change("added", new="\n".join(sorted(lines)), domain="openapi",
                                  facet="schema", entity=subject))
            else:
                lines = [ln for (s, _), (_, ln) in odet.items() if s == subject]
                out.append(Change("removed", old="\n".join(sorted(lines)), domain="openapi",
                                  facet="schema", entity=subject))

    for key in sorted(set(odet) | set(ndet)):
        subject, item = key
        if subject in gone or (subject in old_subjects) != (subject in new_subjects):
            continue  # reported as a whole operation/schema above
        facet = item.split(" ", 1)[0]
        if key not in odet:
            out.append(Change("added", new=ndet[key][1], domain="openapi", facet=facet,
                              entity=subject, item=item, new_attrs=ndet[key][0]))
        elif key not in ndet:
            out.append(Change("removed", old=odet[key][1], domain="openapi", facet=facet,
                              entity=subject, item=item, old_attrs=odet[key][0]))
        elif odet[key][0] != ndet[key][0]:
            out.append(Change("modified", old=odet[key][1], new=ndet[key][1], domain="openapi",
                              facet=facet, entity=subject, item=item,
                              old_attrs=odet[key][0], new_attrs=ndet[key][0]))
    return out
