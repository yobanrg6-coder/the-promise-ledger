"""
FastAPI Backend Application - The Promise Ledger
Serves the modern web UI and provides SSE streaming endpoints for multi-agent execution.
"""

import asyncio
import json
import logging
import os
import re
import sys
import time
from collections import defaultdict, deque

import aiofiles
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("promise_ledger")

# Ensure local imports work properly
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(PROJECT_ROOT)
from agents.orchestrator import PromiseLedgerOrchestrator

load_dotenv()

app = FastAPI(
    title="The Promise Ledger Studio",
    description="Autonomous Attention-Timing Intelligence Studio powered by Google ADK & MCP",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    # No cookies/session auth exist on this API - allow_credentials=True paired
    # with a wildcard origin is a no-op in spec-compliant browsers but is a
    # red flag on inspection and was never actually needed here.
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Minimal in-memory rate limit for the one endpoint that spends real Gemini
# quota. The Cloud Run deploy in README.md is intentionally
# --allow-unauthenticated (judges need a public URL, no login), and this
# endpoint falls back to the server's own GEMINI_API_KEY when a caller sends
# none - without a limiter, that combination lets anyone who finds the URL
# script unlimited free Gemini calls against this project's quota. A single
# Cloud Run instance is the realistic demo deployment target, so per-process
# memory is an acceptable bound (not correct across multiple instances/
# restarts, but strictly better than no limit at all).
# ---------------------------------------------------------------------------
RATE_LIMIT_MAX_REQUESTS = 10
RATE_LIMIT_WINDOW_SECONDS = 60
_request_log: dict[str, deque] = defaultdict(deque)


def _resolve_client_ip(request: Request) -> str:
    # Cloud Run terminates the connection at Google's front end and proxies
    # to the container - request.client.host is the front end's internal
    # address, identical for every caller, not the visitor's real IP. Behind
    # any reverse proxy (Cloud Run included) the real client IP only survives
    # in X-Forwarded-For. Without this, the "per-IP" limiter below is
    # actually one shared global bucket: the first 10 requests from ANY
    # combination of visitors in a 60s window would lock out everyone else,
    # including judges loading the demo at the same time as any other
    # visitor.
    #
    # The LEFTMOST entry is whatever the client itself sent (or fabricated -
    # verified live: sending a fake X-Forwarded-For header from curl let a
    # single caller bypass the limiter entirely by rotating a fake value on
    # every request). Cloud Run's own front end appends the real client IP
    # as the LAST entry in the chain and that part of the header cannot be
    # supplied by the client, so the limiter has to key on the last value.
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        return forwarded_for.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


def _check_rate_limit(client_ip: str) -> None:
    now = time.monotonic()
    log = _request_log[client_ip]
    while log and now - log[0] > RATE_LIMIT_WINDOW_SECONDS:
        log.popleft()
    if len(log) >= RATE_LIMIT_MAX_REQUESTS:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded: max {RATE_LIMIT_MAX_REQUESTS} requests per {RATE_LIMIT_WINDOW_SECONDS}s.",
        )
    log.append(now)

# Mount static files
static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
os.makedirs(static_dir, exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir), name="static")

MAX_TOPIC_LENGTH = 200
_GEO_CODE_PATTERN = re.compile(r"^[A-Za-z]{2}$")

class CampaignRequest(BaseModel):
    topic: str
    platform: str = "tiktok"
    geo: str = "ES"
    target_geo: str | None = None
    tone: str = "Direct & High Impact"
    target_audience: str = "Founders, Creators and Businesses"
    api_key: str | None = None
    # Set when the user picks a BACKLOG alternative from a previous scan's
    # signals_evaluated instead of the auto-selected winner - re-runs the
    # same pipeline forced onto that specific candidate.
    forced_topic: str | None = None

    @field_validator("geo", "target_geo")
    @classmethod
    def _validate_geo_code(cls, value):
        # The frontend's <select> only ever sends a 2-letter code, but this
        # endpoint is reachable directly (no auth), so a caller bypassing the
        # UI could otherwise pass arbitrary text straight into rendered
        # strings ("trending in {geo}...") and Gemini prompts.
        if value is None:
            return value
        if not _GEO_CODE_PATTERN.match(value):
            raise ValueError("geo/target_geo must be a 2-letter country code (e.g. 'ES', 'MX')")
        return value.upper()

@app.get("/", response_class=HTMLResponse)
async def serve_dashboard():
    index_file = os.path.join(static_dir, "index.html")
    if os.path.exists(index_file):
        async with aiofiles.open(index_file, "r", encoding="utf-8") as f:
            return await f.read()
    return "<h1>The Promise Ledger - Web Dashboard Loading...</h1>"

@app.get("/api/health")
async def health_check():
    return {
        "status": "online",
        "service": "The Promise Ledger Studio",
        "model": os.getenv("MODEL", "gemini-3.5-flash-lite"),
        "mcp_server": os.getenv("MCP_SERVER_URL", "http://127.0.0.1:8080/mcp")
    }

@app.post("/api/generate-stream")
async def generate_campaign_stream(req: CampaignRequest, request: Request):
    """
    SSE Streaming endpoint that streams real-time deliberation events from the 4 agents.
    """
    _check_rate_limit(_resolve_client_ip(request))
    resolved_key = req.api_key or os.getenv("GEMINI_API_KEY")

    async def event_generator():
        if not resolved_key:
            yield f"data: {json.dumps({'type': 'error', 'message': 'Missing GEMINI_API_KEY: set it in .env or paste it into the optional field on the left panel.'}, ensure_ascii=False)}\n\n"
            return

        if not req.topic or not req.topic.strip():
            yield f"data: {json.dumps({'type': 'error', 'message': 'The niche/target cannot be empty.'}, ensure_ascii=False)}\n\n"
            return

        if len(req.topic) > MAX_TOPIC_LENGTH:
            yield f"data: {json.dumps({'type': 'error', 'message': f'The niche/target is too long ({len(req.topic)} chars, max {MAX_TOPIC_LENGTH}).'}, ensure_ascii=False)}\n\n"
            return

        try:
            orchestrator = PromiseLedgerOrchestrator(api_key=resolved_key)
        except Exception:
            logger.exception("Failed to initialize PromiseLedgerOrchestrator")
            yield f"data: {json.dumps({'type': 'error', 'message': 'Could not initialize the orchestrator. Check that the Gemini API key is valid.'}, ensure_ascii=False)}\n\n"
            return

        try:
            async for event in orchestrator.execute_campaign_stream(
                topic=req.topic,
                platform=req.platform,
                geo=req.geo,
                target_geo=req.target_geo,
                tone=req.tone,
                target_audience=req.target_audience,
                forced_topic=req.forced_topic,
            ):
                event_json = json.dumps(event, ensure_ascii=False)
                yield f"data: {event_json}\n\n"
                await asyncio.sleep(0.05)
        except Exception:
            # Full traceback stays server-side; the client gets a clean, non-leaky message.
            logger.exception("Agent swarm execution failed for topic=%r", req.topic)
            error_event = {
                "type": "error",
                "message": "The agent swarm failed during execution. Check the server logs for technical detail."
            }
            yield f"data: {json.dumps(error_event, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT") or os.getenv("WEB_APP_PORT", "8000"))
    uvicorn.run("app:app", host="127.0.0.1", port=port, reload=True)
