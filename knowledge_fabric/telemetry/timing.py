"""Phase and active-versus-idle timing for one answer (T56).

A dependency-free stopwatch (``time.perf_counter`` only) that a request builds
as it runs. It records how long each phase took — ``retrieve``, ``summarise``,
``agent_steps``, ``translate``, ``image`` — the total *active* time (model plus
tool work) and, by subtraction from the wall clock, the *idle* time spent
waiting. ``attach`` writes the collected block onto a span, trace or event as a
``timing`` key, so the ledger insights read active-versus-idle and per-phase
percentiles without re-instrumenting the answer path.

Concepts reproduced from the MIT-licensed *token-meter* project (its
``phases``/active-idle accounting). This is a clean-room re-implementation — no
token-meter code is copied — credited in ``THIRD_PARTY_NOTICES.md``.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager

# The phases an answer moves through, in order. ``retrieve`` is wall time with
# no model call (its cost is zero in the ledger); the rest each map to a
# ledgered purpose.
PHASES = ("retrieve", "summarise", "agent_steps", "translate", "image")


class PhaseTimer:
    """Accumulate phase durations, active time and tool-call count for one answer.

    Use it as a set of nested context managers::

        timer = PhaseTimer()
        with timer.phase("retrieve", active=False):
            ...                      # retrieval is wall time, not billed active
        with timer.phase("summarise"):
            ...                      # a model call — active
        block = timer.timing()       # {phase_ms, active_ms, idle_ms, total_ms, tool_calls}

    ``active`` marks whether a phase counts toward busy time. Retrieval and any
    pure-wait span pass ``active=False`` so it lands in idle by subtraction.
    """

    def __init__(self) -> None:
        self.phase_ms: dict[str, float] = {}
        self.active_ms: float = 0.0
        self.tool_calls: int = 0
        self._t0: float = time.perf_counter()

    def reset(self) -> PhaseTimer:
        """Restart the wall clock and clear all counters."""
        self.phase_ms = {}
        self.active_ms = 0.0
        self.tool_calls = 0
        self._t0 = time.perf_counter()
        return self

    def add(self, phase: str, ms: float, *, active: bool = True) -> None:
        """Record ``ms`` under ``phase`` (already measured elsewhere)."""
        ms = max(0.0, float(ms))
        self.phase_ms[phase] = self.phase_ms.get(phase, 0.0) + ms
        if active:
            self.active_ms += ms

    @contextmanager
    def phase(self, name: str, *, active: bool = True) -> Iterator[None]:
        """Time the wrapped block and add it to ``name``."""
        start = time.perf_counter()
        try:
            yield
        finally:
            self.add(name, (time.perf_counter() - start) * 1000.0, active=active)

    @contextmanager
    def tool(self, phase: str = "agent_steps") -> Iterator[None]:
        """Time one tool call: counts as active time and bumps ``tool_calls``."""
        self.tool_calls += 1
        with self.phase(phase, active=True):
            yield

    def total_ms(self) -> float:
        """Wall-clock milliseconds since construction (or the last ``reset``)."""
        return (time.perf_counter() - self._t0) * 1000.0

    def idle_ms(self, total_ms: float | None = None) -> float:
        """Wall clock minus active time, floored at zero."""
        total = self.total_ms() if total_ms is None else float(total_ms)
        return max(0.0, total - self.active_ms)

    def timing(self, total_ms: float | None = None) -> dict:
        """The JSON-serialisable ``timing`` block for a trace/event/span."""
        total = self.total_ms() if total_ms is None else float(total_ms)
        # Active can never exceed the wall clock; clamp so idle stays >= 0.
        active = min(self.active_ms, total) if total else self.active_ms
        return {
            "phase_ms": {k: round(v, 2) for k, v in self.phase_ms.items()},
            "active_ms": round(active, 2),
            "idle_ms": round(max(0.0, total - active), 2),
            "total_ms": round(total, 2),
            "tool_calls": int(self.tool_calls),
        }


def attach(trace_or_event, timer: PhaseTimer, *, total_ms: float | None = None):
    """Add a ``timing`` block from ``timer`` onto ``trace_or_event`` and return it.

    Accepts either a telemetry span (anything exposing ``.set(**attrs)``) or a
    plain dict event/trace. The integrator calls this at the end of an answer,
    e.g. ``attach(span, timer)`` inside ``answer/service.py`` or
    ``answer/agent.py``; it is a no-op-safe helper with no dependency on the
    answer path.
    """
    block = timer.timing(total_ms=total_ms)
    if hasattr(trace_or_event, "set"):
        trace_or_event.set(timing=block)
    elif isinstance(trace_or_event, dict):
        trace_or_event["timing"] = block
    return trace_or_event


__all__ = ["PHASES", "PhaseTimer", "attach"]
