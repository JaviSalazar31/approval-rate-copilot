# MCP server

The copilot's five diagnostic functions as read-only tools for any client of the Model Context Protocol (MCP),
such as Claude Desktop, Claude Code or another agent. The Genie agent reaches the same functions from Databricks;
this server lets an assistant outside the workspace run the same method and get the same numbers.

| Tool | Function in Unity Catalog | Returns |
|---|---|---|
| `drop_impact` | `fn_drop_impact` | The size of a drop: change in final approval, lost approvals, lost approved volume (USD) and fees |
| `approval_drivers` | `fn_approval_drivers` | Which acquirers, issuers, card brands, 3DS outcomes, merchant segments and routing versions moved the rate |
| `decline_shift` | `fn_decline_shift` | Which first-attempt decline codes grew, per acquirer and across acquirers |
| `cascade_health` | `fn_cascade_health` | Whether failed attempts were retried on another acquirer and recovered, and what stopped retries cost |
| `changes_near` | `fn_changes_near` | Production changes before, during and after a window |

Every tool takes a country (MX, CO or BR) and a window in the country's local time, and compares it with the days
before it. The answer is a table: its columns, its rows and whether it was cut at 200 rows.

## How it stays read-only

- Each tool runs one `SELECT` on one of the five functions. There is no tool that takes SQL.
- Arguments are checked before anything runs: the country and card type against the values the data holds,
  timestamps parsed and written back in one format, whole numbers within bounds. A text "NULL" means no value,
  a lesson from the Genie evaluation.
- On Databricks, every argument is bound as a parameter of the Statement Execution API, so no value from the
  client becomes SQL text. The token's permissions still apply, so a read-only token is the right one to use.
- The tools declare themselves read-only and idempotent to the client.

## Run it

```bash
pip install -r mcp_server/requirements.txt

# On the local pipeline output (python local/run_local.py first): no workspace needed
python mcp_server/approval_copilot_mcp.py --backend local

# On Databricks: the SDK's default authentication and a SQL warehouse
databricks auth login --host https://<workspace>.cloud.databricks.com --profile approval-copilot
export DATABRICKS_CONFIG_PROFILE=approval-copilot
export DATABRICKS_WAREHOUSE_ID=<SQL warehouse ID>
python mcp_server/approval_copilot_mcp.py
```

The server speaks MCP over stdin and stdout, so an MCP client starts it; running it by hand only shows that it
waits for a client. `APPROVAL_COPILOT_SCHEMA` changes the catalog and schema (default `workspace.approval_copilot`).

## Add it to Claude Desktop

In `claude_desktop_config.json` (on Windows, `%APPDATA%\Claude\claude_desktop_config.json`), with the paths of
your copy of the repository and of the Python environment where you installed the requirements:

```json
{
  "mcpServers": {
    "approval-rate-copilot": {
      "command": "C:\\path\\to\\approval-rate-copilot\\.venv-mcp\\Scripts\\python.exe",
      "args": ["C:\\path\\to\\approval-rate-copilot\\mcp_server\\approval_copilot_mcp.py"],
      "env": {
        "DATABRICKS_CONFIG_PROFILE": "approval-copilot",
        "DATABRICKS_WAREHOUSE_ID": "<SQL warehouse ID>"
      }
    }
  }
}
```

The client passes the server only the environment listed there, which is why the profile and the warehouse go in
`env`. Restart Claude Desktop, and the five tools appear under the server's name. Then ask, for example, "Why did
debit approval in Colombia drop on 13 August 2026?"

## Tests

[`tests/test_mcp_server.py`](../tests/test_mcp_server.py), in CI: the argument checks, the statement and parameters
sent to Databricks (no value reaches the SQL text), typed rows from the Statement Execution API, and the five
tools called through MCP on the local pipeline output, in process and over stdio, where `drop_impact` returns the
copilot's numbers for the Colombia debit incident: 1,004 payments, −10.95 points, 110 lost approvals.
[`smoke_test.py`](smoke_test.py) does the same on the deployed functions in Unity Catalog; the workflow
`.github/workflows/mcp-smoke.yml` runs it with the repository's secrets.
