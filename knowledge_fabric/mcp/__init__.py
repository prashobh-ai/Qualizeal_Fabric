"""MCP server package (T31) — the Knowledge Fabric over the Model Context Protocol.

Launch with ``python -m knowledge_fabric.mcp`` (stdio transport). The server and
its tools are defined in ``server.py``; importing this package never imports the
optional ``mcp`` dependency (that import is guarded inside ``build_server``).
"""

from __future__ import annotations

from .server import SERVER_NAME, build_server

__all__ = ["build_server", "SERVER_NAME"]
