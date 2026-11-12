"""
Starts the MCP server with the Databricks backend over stdio, as an MCP client would, and checks the copilot's
numbers for the Colombia debit incident on the functions deployed in Unity Catalog.

  DATABRICKS_HOST=... DATABRICKS_TOKEN=... DATABRICKS_WAREHOUSE_ID=... python mcp_server/smoke_test.py

The workflow .github/workflows/mcp-smoke.yml runs it with the repository's secrets.
"""
import asyncio
import os
import sys

from mcp import Client, StdioServerParameters

HERE = os.path.dirname(os.path.abspath(__file__))
WINDOW = dict(country="CO", start_local="2026-08-13 14:00:00", end_local="2026-08-14 12:10:00", card_type="debit")


def rows(result):
    if result.is_error:
        sys.exit(f"tool error: {result.content[0].text}")
    data = result.structured_content
    return [dict(zip(data["columns"], r)) for r in data["rows"]]


async def main():
    # a stdio client passes only a few variables to the server by default, so the Databricks ones go explicitly
    params = StdioServerParameters(command=sys.executable, args=[os.path.join(HERE, "approval_copilot_mcp.py")],
                                   env=dict(os.environ))
    async with Client(params, read_timeout_seconds=300) as client:
        tools = sorted(t.name for t in (await client.list_tools()).tools)
        print("tools:", ", ".join(tools))
        impact = rows(await client.call_tool("drop_impact", WINDOW))[0]
        print("drop_impact:", {k: impact[k] for k in ("window_source", "window_payments", "final_rate_change_pp",
                                                       "lost_approvals", "lost_approved_usd")})
        issuers = rows(await client.call_tool("approval_drivers", {**WINDOW, "dimension": "issuer"}))
        print("lowest issuer:", issuers[0]["segment_value"], issuers[0]["window_rate"], issuers[0]["pp_below_next"])
        changes = rows(await client.call_tool("changes_near", {k: v for k, v in WINDOW.items() if k != "card_type"}))
        print("changes:", [(c["change_id"], c["position"]) for c in changes])
        incident = rows(await client.call_tool("drop_impact", {"country": "CO", "card_type": "debit",
                                                               "incident_date": "2026-08-13"}))[0]
        print("incident window:", incident["window_start_local"], incident["window_end_local"], incident["window_source"])
        retries = rows(await client.call_tool("cascade_health", WINDOW))
        stopped = [(r["acquirer_id"], r["reason_code"]) for r in retries if r["map_stopped_retries"]]
        print("retries the map stopped:", stopped)
        assert len(tools) == 5
        assert (impact["window_payments"], impact["lost_approvals"]) == (1004, 110), impact
        assert issuers[0]["segment_value"] == "CO-ISS-07", issuers[0]
        assert {"CHG-0813", "CHG-0814"} <= {c["change_id"] for c in changes}, changes
        assert (incident["window_start_local"], incident["window_end_local"], incident["window_payments"]) == (
            "2026-08-13 14:00:00", "2026-08-14 12:10:00", 1004), incident
        assert incident["window_source"] == impact["window_source"] == "change log: from CHG-0813 to CHG-0814"
        assert stopped == [("B", "05")], stopped
    print("MCP server on Databricks: OK")


if __name__ == "__main__":
    asyncio.run(main())
