"""Workspace (L2) — the signed-in chat console, served at ``/`` and ``/ask``.

A three-column product surface (F3, made concrete):

* **Threads** (left, 300) — the reader's conversations, kept per-session; a
  new chat, click to re-open, the first question names the thread.
* **Conversation** (centre) — a slim corpus strip (numbers, labels under), the
  message stream (question bubble + grounded answer with inline citation chips
  that open the page viewer; declines read as a plain sentence), and the
  composer (mic placeholder until voice, answer-language pill, read-aloud
  toggle, Send).
* **Right rail** (380) — the compact answer galaxy (activation from the
  retrieved passages) with a "Why did the AI say this?" Explain overlay; **the
  card** (one row per fact — Answered by, Why, Model, Moved levels, then the
  rest under Details); and **My usage** at the bottom (questions · answered ·
  declined, tokens, cost, the level split for today / 7 d / 30 d, and the
  budget bar).

The reader only ever sees the level as a word (L1.5). Zero external
dependencies: inline CSS/JS/SVG only (``ui_common.shell``).
"""

from __future__ import annotations

from .ui_common import _read_asset, shell

__all__ = ["ASK_HTML"]

_CSS = _read_asset("ask_ui__css.css")

_BODY = _read_asset("ask_ui__body.html")

_JS = _read_asset("ask_ui__js.js")

ASK_HTML = shell("Workspace", "", _BODY, _JS, "Workspace", _CSS)
