"""Smoke test: the MCP server module imports and exposes its instance."""

from __future__ import annotations


def test_mcp_server_imports() -> None:
    from taskhub.mcp import server

    assert server.mcp is not None
