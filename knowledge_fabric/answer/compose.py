"""Cited-sentence filter for model-composed text (T43).

The agent's final message may only say what its tool results support. The rule
is mechanical, not judged: a sentence survives only if it carries a ``[n]``
marker that maps to a citation the run actually collected. Everything else —
an uncited claim, a marker pointing past the citation list, a courtesy sentence
— is dropped before display. Bullets and fenced code blocks are kept as units
when the unit (or the line introducing it) is cited.
"""

from __future__ import annotations

import re

_MARK = re.compile(r"\[(\d+)\]")
_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(\[\"'*•\-])")


def _cited(text: str, n: int) -> bool:
    return any(1 <= int(m) <= n for m in _MARK.findall(text))


def _units(text: str) -> list[tuple[str, str]]:
    """Split into (kind, text) units: 'code' fenced blocks, 'line' bullets/
    headings, 'sentence' prose sentences. Blank lines are 'break'."""
    out: list[tuple[str, str]] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.strip().startswith("```"):
            block = [line]
            i += 1
            while i < len(lines):
                block.append(lines[i])
                if lines[i].strip().startswith("```"):
                    i += 1
                    break
                i += 1
            out.append(("code", "\n".join(block)))
            continue
        if not line.strip():
            out.append(("break", ""))
        elif re.match(r"^\s*([-*•]|\d+[.)])\s+", line) or line.lstrip().startswith("#"):
            out.append(("line", line))
        else:
            for s in _SPLIT.split(line.strip()):
                if s.strip():
                    out.append(("sentence", s.strip()))
        i += 1
    return out


def keep_cited(text: str, citations: list) -> str:
    """Drop every sentence without a ``[n]`` marker that maps to ``citations``
    (1-based). Returns the surviving text, whitespace-normalised."""
    n = len(citations or [])
    if not text or n == 0:
        return ""
    kept: list[tuple[str, str]] = []
    prev_cited = False
    for kind, unit in _units(text):
        if kind == "break":
            if kept and kept[-1][0] != "break":
                kept.append(("break", ""))
            prev_cited = False
            continue
        if kind == "code":
            if prev_cited or _cited(unit, n):
                kept.append((kind, unit))
            continue
        ok = _cited(unit, n)
        if ok:
            kept.append((kind, unit))
        prev_cited = ok
    while kept and kept[-1][0] == "break":
        kept.pop()
    out = ""
    last = "break"
    for kind, unit in kept:
        if kind == "break":
            out += "\n\n"
        elif last == "break" or not out:
            out += unit
        elif kind == "sentence" and last == "sentence":
            out += " " + unit
        else:
            out += "\n" + unit
        last = kind
    return out.strip()


def renumber(text: str, mapping: dict[int, int]) -> str:
    """Rewrite ``[old]`` markers to ``[new]`` per ``mapping`` (unknown kept)."""
    return _MARK.sub(lambda m: f"[{mapping.get(int(m.group(1)), int(m.group(1)))}]", text)
