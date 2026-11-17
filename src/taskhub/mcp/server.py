"""FastMCP entry point for TaskHub."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from taskhub.config import get_settings
from taskhub.mcp import resources, tools

_settings = get_settings()
mcp = FastMCP("taskhub", host=_settings.host, port=_settings.port)

mcp.tool()(tools.create_task)
mcp.tool()(tools.get_context)
mcp.tool()(tools.get_next_item)
mcp.tool()(tools.list_projects)
mcp.tool()(tools.project_status)
mcp.tool()(tools.evaluate_project)
mcp.tool()(tools.forecast_project)
mcp.tool()(tools.import_backlog)
mcp.tool()(tools.backup_db)

mcp.resource("task://{item_id}")(resources.task_doc)
mcp.resource("task://{item_id}/context")(resources.task_context)
mcp.resource("project://{slug}/backlog")(resources.project_backlog)


def run() -> None:
    """Run the MCP server using the configured transport."""

    settings = get_settings()
    if settings.transport == "http":
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")
