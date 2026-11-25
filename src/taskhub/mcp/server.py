"""FastMCP entry point for TaskHub."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from taskhub.config import get_settings
from taskhub.mcp import resources, tools

_settings = get_settings()
mcp = FastMCP("taskhub", host=_settings.host, port=_settings.port)

mcp.tool()(tools.create_task)
mcp.tool()(tools.update_task)
mcp.tool()(tools.change_status)
mcp.tool()(tools.get_workflow)
mcp.tool()(tools.get_context)
mcp.tool()(tools.get_next_item)
mcp.tool()(tools.list_projects)
mcp.tool()(tools.project_status)
mcp.tool()(tools.evaluate_project)
mcp.tool()(tools.forecast_project)
mcp.tool()(tools.import_backlog)
mcp.tool()(tools.backup_db)
mcp.tool()(tools.ingest_metrics)
mcp.tool()(tools.log_activity)
mcp.tool()(tools.task_metrics)
mcp.tool()(tools.metrics_report)
mcp.tool()(tools.add_note)
mcp.tool()(tools.search_notes)
mcp.tool()(tools.upsert_document)
mcp.tool()(tools.get_document)
mcp.tool()(tools.list_documents)
mcp.tool()(tools.list_templates)
mcp.tool()(tools.apply_template)
mcp.tool()(tools.import_templates)
mcp.tool()(tools.register_agent)
mcp.tool()(tools.claim_task)
mcp.tool()(tools.renew_claim)
mcp.tool()(tools.release_task)
mcp.tool()(tools.list_claims)
mcp.tool()(tools.list_tasks)
mcp.tool()(tools.register_projects)
mcp.tool()(tools.sync_projects)
mcp.tool()(tools.project_audit)
mcp.tool()(tools.move_project)
mcp.tool()(tools.archive_project)
mcp.tool()(tools.import_all_backlogs)
mcp.tool()(tools.import_notes)
mcp.tool()(tools.export_notes)
mcp.tool()(tools.economics_report)
mcp.tool()(tools.economics_summary)
mcp.tool()(tools.cost_series)
mcp.tool()(tools.list_prices)
mcp.tool()(tools.sync_prices)
mcp.tool()(tools.set_model_price)
mcp.tool()(tools.close_model_price)
mcp.tool()(tools.set_project_economics)
mcp.tool()(tools.portfolio_summary)

mcp.resource("task://{item_id}")(resources.task_doc)
mcp.resource("task://{item_id}/context")(resources.task_context)
mcp.resource("project://{slug}/backlog")(resources.project_backlog)
mcp.resource("project://{slug}/specs")(resources.project_specs)
mcp.resource("project://{slug}/notes")(resources.project_notes)
mcp.resource("project://{slug}/claims")(resources.project_claims)


def run() -> None:
    """Run the MCP server using the configured transport."""

    settings = get_settings()
    if settings.transport == "http":
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")
