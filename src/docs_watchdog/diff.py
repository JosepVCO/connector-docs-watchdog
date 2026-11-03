"""Change detection between two normalized snapshots.

Nothing here truncates or drops content: every added, removed and modified piece of text (or,
for OpenAPI snapshots, every structural fact) becomes a `Change`. Large change sets are split
into chunks with `chunk_changes` for consumers with a size limit (LLM calls); the chunks together
always cover every change.
"""
from __future__ import annotations

import difflib
import re
from collections import Counter
from dataclasses import dataclass, field

MAX_CHUNK_CHARS = 12000
_HEADING = re.compile(r"^#{1,6}\s+\S")
_PAIR_MIN_RATIO = 0.6
_PAIR_MAX_COMPARISONS = 4000  # bound the O(n*m) pairing of a huge replaced block


@dataclass
class Change:
    kind: str  # "added" | "removed" | "modified"
    old: str = ""
    new: str = ""
    context: str = ""  # nearest preceding heading (text changes)
    domain: str = "text"  # "text" | "openapi"
    facet: str = ""  # openapi: operation|deprecated|param|body|response|field|schema|version|...
    entity: str = ""  # openapi: "GET /pets", "schema Pet"
    item: str = ""  # openapi: "param query:limit"
    old_attrs: dict = field(default_factory=dict)
    new_attrs: dict = field(default_factory=dict)


def _headings(lines: list[str]) -> list[str]:
    out, cur = [], ""
    for line in lines:
        if _HEADING.match(line):
            cur = line.lstrip("#").strip()
        out.append(cur)
    return out


def _runs(indexed: list[tuple[int, str]]) -> list[list[tuple[int, str]]]:
    runs: list[list[tuple[int, str]]] = []
    for item in indexed:
        if runs and item[0] == runs[-1][-1][0] + 1:
            runs[-1].append(item)
        else:
            runs.append([item])
    return runs


def _pair(removed: list[tuple[int, str]], added: list[tuple[int, str]]):
    """Greedily match removed lines with their rewritten counterparts (modified lines)."""
    pairs, used = [], set()
    if len(removed) * len(added) > _PAIR_MAX_COMPARISONS:
        return pairs, removed, added
    for ai, aline in added:
        best, best_r = None, _PAIR_MIN_RATIO
        for ri, rline in removed:
            if ri in used:
                continue
            sm = difflib.SequenceMatcher(None, rline, aline, autojunk=False)
            if sm.real_quick_ratio() < best_r or sm.quick_ratio() < best_r:
                continue
            r = sm.ratio()
            if r >= best_r:
                best, best_r = (ri, rline), r
        if best:
            used.add(best[0])
            pairs.append((best, (ai, aline)))
    return (pairs, [x for x in removed if x[0] not in used],
            [x for x in added if x[0] not in {p[1][0] for p in pairs}])


def text_changes(old: str, new: str) -> list[Change]:
    """Added, removed and modified text. Lines that merely moved are ignored, but a line whose
    occurrence count grew is still reported (a repeated sentence added for another endpoint)."""
    a, b = old.splitlines(), new.splitlines()
    ca, cb = Counter(a), Counter(b)
    add_allow, rem_allow = cb - ca, ca - cb
    ha, hb = _headings(a), _headings(b)
    out: list[Change] = []
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        removed, added = [], []
        for i in range(i1, i2):
            if rem_allow[a[i]] > 0:
                rem_allow[a[i]] -= 1
                removed.append((i, a[i]))
        for j in range(j1, j2):
            if add_allow[b[j]] > 0:
                add_allow[b[j]] -= 1
                added.append((j, b[j]))
        pairs, removed, added = _pair(removed, added) if removed and added else ([], removed, added)
        for (ri, rline), (ai, aline) in pairs:
            out.append(Change("modified", old=rline, new=aline, context=hb[ai]))
        for run in _runs(added):
            out.append(Change("added", new="\n".join(l for _, l in run), context=hb[run[0][0]]))
        for run in _runs(removed):
            out.append(Change("removed", old="\n".join(l for _, l in run), context=ha[run[0][0]]))
    return out


def compute_changes(old: str, new: str, structural: bool = False) -> list[Change]:
    """Every difference between two snapshots. `structural` enables OpenAPI fact comparison
    (used for `openapi` sources); text comparison is the fallback if either side is not facts."""
    if structural:
        from . import openapi

        if openapi.is_facts(old) and openapi.is_facts(new):
            return openapi.diff_facts(old, new)
    return text_changes(old, new)


def make_diff(old: str, new: str) -> str:
    """Text added or rewritten since `old` (no truncation). Kept for simple callers."""
    parts = []
    for ch in text_changes(old, new):
        if ch.kind in ("added", "modified"):
            parts.append(ch.new)
    return "\n".join(parts)


def added_lines(old: str, new: str) -> list[str]:
    return make_diff(old, new).splitlines()


# ---- rendering for reports and LLM input --------------------------------------------------
def render_change(ch: Change) -> str:
    if ch.domain == "openapi":
        head = f"[OPENAPI {ch.kind.upper()}] {ch.entity}" + (f" | {ch.item}" if ch.item else "")
        head += f" ({ch.facet})" if ch.facet else ""
    else:
        head = f"[{ch.kind.upper()}]" + (f" (under: {ch.context})" if ch.context else "")
    body = []
    if ch.kind == "added":
        body = ch.new.splitlines()
    elif ch.kind == "removed":
        body = ch.old.splitlines()
    else:
        body = [f"- {l}" for l in ch.old.splitlines()] + [f"+ {l}" for l in ch.new.splitlines()]
    return "\n".join([head, *body])


def render_changes(changes: list[Change]) -> str:
    return "\n".join(render_change(c) for c in changes)


def _split_piece(text: str, limit: int) -> list[str]:
    """Split one rendered change into pieces <= limit, repeating its header line."""
    if len(text) <= limit:
        return [text]
    head, *lines = text.split("\n")
    room = max(limit - len(head) - 1, 1)
    pieces, cur, size = [], [], 0
    for line in lines:
        while len(line) > room:  # a single enormous line is cut, never discarded
            if cur:
                pieces.append("\n".join([head, *cur]))
                cur, size = [], 0
            pieces.append("\n".join([head, line[:room]]))
            line = line[room:]
        if cur and size + len(line) + 1 > room:
            pieces.append("\n".join([head, *cur]))
            cur, size = [], 0
        cur.append(line)
        size += len(line) + 1
    if cur:
        pieces.append("\n".join([head, *cur]))
    return pieces


def chunk_changes(changes: list[Change], max_chars: int = MAX_CHUNK_CHARS) -> list[str]:
    """Split the rendered changes into chunks of at most `max_chars`, never losing content."""
    chunks, cur, size = [], [], 0
    for ch in changes:
        for piece in _split_piece(render_change(ch), max_chars):
            if cur and size + len(piece) + 1 > max_chars:
                chunks.append("\n".join(cur))
                cur, size = [], 0
            cur.append(piece)
            size += len(piece) + 1
    if cur:
        chunks.append("\n".join(cur))
    return chunks
