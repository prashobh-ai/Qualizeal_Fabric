"""QualiZeal Knowledge Fabric — telemetry dashboard (WS3 · PROVE).

A Power BI-style, multi-tab analytics surface served by the platform. Charts
are drawn with inline SVG (zero external dependencies, works fully offline).
Reads /api/analytics and /admin/sources with the filters the roadmap and
leadership asked for: window (last 24h / last 7d / all), per user, per role;
tokens in/out, model routing WITH the selector's reason, cost, and cost saved
by cache technique.
"""

from __future__ import annotations

from .ui_common import _read_asset

__all__ = ["DASHBOARD_HTML"]

DASHBOARD_HTML = _read_asset("dashboard__dashboard_html.html")
