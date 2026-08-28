"""
FastMCP server - The Promise Ledger.

The ledger's machine interface: any client (an ADK agent, the web UI, a CI
job) reads and writes the same verified ledger through these tools. Every
tool here is deterministic and LLM-free - promise EXTRACTION (the one Gemini
step) lives in the web app's streaming pipeline, not on this port.
"""

import datetime as dt
import os
import sys

from dotenv import load_dotenv
from fastmcp import FastMCP

# Windows consoles / subprocess pipes default to cp1252 and crash on any
# non-latin-1 output before the server binds a port; force UTF-8.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from ledger import promises as ledger
from ledger.run_cycle import run_cycle

load_dotenv()

mcp = FastMCP("The Promise Ledger")


@mcp.tool()
def get_scorecard() -> dict:
    """The accountability scorecard: per-company and overall counts of kept /
    late / delayed / abandoned / pending / unverifiable promises, plus an
    on-time rate (kept-on-time / resolved). Computed from the ledger, never
    estimated."""
    return ledger.get_scorecard()


@mcp.tool()
def list_promises() -> list:
    """Every promise in the ledger with its current status, source quote,
    source URL, deadline and the evidence URL the verifier checks."""
    return ledger.list_promises()


@mcp.tool()
def get_promise(promise_id: str) -> dict:
    """One promise by id, including its verification fields (status_reason,
    evidence_excerpt, last_checked_at, resolved_at)."""
    row = ledger.get_promise(promise_id)
    return row or {"error": f"no promise {promise_id!r}"}


@mcp.tool()
def admit_promise(
    company: str,
    promise_text: str,
    source_quote: str,
    source_url: str,
    announced_date: str,
    deadline_date: str,
    observable_outcome: str,
    check_keywords: list[str],
    evidence_url: str = "",
    deadline_raw: str = "",
) -> dict:
    """Add a promise to the ledger. Dates are YYYY-MM-DD. The ledger stores it
    as PENDING until a verification cycle resolves it."""
    pid = ledger.admit_promise(
        company=company,
        promise_text=promise_text,
        source_quote=source_quote,
        source_url=source_url,
        announced_date=announced_date,
        deadline_raw=deadline_raw or deadline_date,
        deadline_date=deadline_date,
        observable_outcome=observable_outcome,
        check_keywords=check_keywords,
        evidence_url=evidence_url,
        extractor_model="(mcp admit_promise)",
    )
    return {"promise_id": pid, "status": "PENDING"}


@mcp.tool()
def run_verification_cycle() -> dict:
    """Re-check every promise whose deadline has passed against its live
    evidence page (zero LLM) and persist any status change. Returns how many
    were checked, which changed, and the fresh scorecard."""
    result = run_cycle(check_date=dt.datetime.now(dt.timezone.utc).date())
    return {"checked": result["checked"], "changed": result["changed"],
            "errors": result["errors"], "scorecard": result["scorecard"]}


if __name__ == "__main__":
    host = os.getenv("MCP_SERVER_HOST", "127.0.0.1")
    port = int(os.getenv("MCP_SERVER_PORT", "8080"))
    print(f"Starting The Promise Ledger FastMCP server on http://{host}:{port}/mcp ...")
    mcp.run(transport="streamable-http", host=host, port=port)
