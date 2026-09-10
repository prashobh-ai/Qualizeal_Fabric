"""Entry point: ``python -m knowledge_fabric.mcp`` runs the MCP server over stdio.

The MCP inspector's headless conformance check (``tools/list``) and any MCP
client (Claude Desktop, an IDE) launch the server this way. Configuration comes
from the environment: ``KF_TENANT`` (default ``qualizeal``) and ``KF_DB`` (the
store path), matching the HTTP surface's ``platform()``.
"""

from __future__ import annotations

from .server import build_server


def main() -> None:
    build_server().run(transport="stdio")


if __name__ == "__main__":
    main()
