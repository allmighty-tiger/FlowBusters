"""FlowBusters Portal - Minimal FastAPI Application.

Endpoints:
  POST /api/assessments         → Start FlowBusters assessment
  POST /api/assessments/finish-recording  → Signal RECORD phase completion
  GET  /api/assessments/stream  → SSE progress stream
  GET  /api/assessments/report  → Get findings + remediation
"""
from __future__ import annotations

import asyncio
import collections
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel

from backend.runtime.orchestrator import ProgressEvent, Phase, run_flowbusters

# Load .env so ANTHROPIC_API_KEY, etc. are available immediately
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv not installed; rely on OS env

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("fb.app")

# ── Global State (single user, single assessment) ────────────────────────────

_active_run: asyncio.Future | None = None
# Fan-out: each SSE client gets its own queue so multiple consumers
# (browser UI + CLI watchers) all receive every event.
_sse_subscribers: set[asyncio.Queue[ProgressEvent | None]] = set()
# Ring buffer of recent events so a client that connects AFTER a run has
# already started (e.g. the browser only navigates to the Progress page once
# the run is underway) still sees the early phase transitions instead of a
# frozen "all pending" view.
_recent_events: "collections.deque[ProgressEvent]" = collections.deque(maxlen=100)
_last_findings: dict | None = None
_last_remediation: str | None = None
_last_flow_name: str = ""


# ── Lifespan ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info("FlowBusters Portal starting (API_KEY=%s)" % ("set" if os.environ.get("ANTHROPIC_API_KEY") else "MISSING"))
    yield
    logger.info("FlowBusters Portal shutting down")

app = FastAPI(title="FlowBusters Portal", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("CORS_ORIGINS", "http://localhost:3000").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Assessment Endpoints ─────────────────────────────────────────────────────

class AssessmentRequest(BaseModel):
    app_name: str
    target_url: str
    flow_name: str = "default"


def _broadcast(event: ProgressEvent | None):
    """Send an event to every connected SSE client.

    Non-terminal events are also appended to a small ring buffer so a
    client connecting after the fact can replay them.
    """
    if event is not None:
        _recent_events.append(event)
    for q in list(_sse_subscribers):
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            pass


async def _progress_writer():
    """Background task that finalizes results when the run ends."""
    global _last_findings, _last_remediation, _last_flow_name
    try:
        if _active_run:
            result = await _active_run
            _last_findings = result["findings"]
            _last_remediation = result.get("remediation")
    except Exception as e:
        logger.error(f"Assessment failed: {e}")
        _broadcast(ProgressEvent(Phase.FAILED, str(e), done=True, error=str(e)))
    finally:
        _broadcast(None)  # Signal end of stream to all clients


def _make_progress_cb():
    def cb(event: ProgressEvent):
        _broadcast(event)
    return cb


@app.post("/api/assessments")
async def start_assessment(body: AssessmentRequest):
    global _active_run, _progress_queue, _last_findings, _last_remediation, _last_flow_name

    if _active_run and not _active_run.done():
        return JSONResponse(status_code=409, content={"detail": "An assessment is already running"})

    _last_findings = None
    _last_remediation = None
    _last_flow_name = body.flow_name
    # Clear the SSE replay buffer so a fresh run's progress page isn't shown the
    # PREVIOUS run's events (which end in a terminal COMPLETE and make every
    # stage render "done" before this run has produced anything).
    _recent_events.clear()

    run_dir = os.environ.get("ARTIFACTS_DIR", ".")

    _active_run = asyncio.ensure_future(
        run_flowbusters(
            target_url=body.target_url,
            flow_name=body.flow_name,
            run_dir=run_dir,
            anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
            anthropic_model=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5"),
            progress_cb=_make_progress_cb(),
            headless=False,
        )
    )

    # Start background progress writer
    asyncio.ensure_future(_progress_writer())

    return {"status": "started", "flow_name": body.flow_name}


@app.get("/api/assessments/stream")
async def stream_progress():
    """SSE stream of progress events."""

    async def event_stream():
        q: asyncio.Queue[ProgressEvent | None] = asyncio.Queue()
        # Replay recent events so a client joining mid-run isn't left frozen
        # on "all pending" while the crew is blocked in the RECORD wait.
        for ev in list(_recent_events):
            q.put_nowait(ev)
        _sse_subscribers.add(q)
        try:
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=int(os.environ.get("SSE_TIMEOUT", "900")))
                except asyncio.TimeoutError:
                    yield f"event: timeout\ndata: {{}}\n\n"
                    continue

                if event is None:
                    # Stream ended
                    break

                data = json.dumps({
                    "phase": event.phase.value,
                    "message": event.message,
                    "done": event.done,
                    "error": event.error,
                })
                yield f"event: progress\ndata: {data}\n\n"

            yield "event: done\ndata: {}\n\n"
        finally:
            _sse_subscribers.discard(q)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/assessments/report")
async def get_report(flow_name: str = Query(default="")):
    """Get the latest assessment report."""
    if not flow_name:
        flow_name = _last_flow_name

    run_dir = Path(os.environ.get("ARTIFACTS_DIR", ".")) / "runs" / flow_name

    # The crew writes to flow-scoped paths (reports/<flow>/, flows/<flow>/,
    # mutations/<flow>/) per the system prompt. Fall back to the flat layout
    # for any pre-existing runs that used it.
    def _find(primary: Path, *fallbacks: Path) -> Path:
        for p in (primary, *fallbacks):
            if p.exists():
                return p
        return primary

    findings_path = _find(
        run_dir / "reports" / flow_name / "findings.json",
        run_dir / "reports" / "findings.json",
    )
    remediation_path = _find(
        run_dir / "reports" / flow_name / "remediation.md",
        run_dir / "reports" / "remediation.md",
    )

    if not findings_path.exists():
        return JSONResponse(status_code=404, content={"detail": "No report found"})

    findings = json.loads(findings_path.read_text())
    remediation = remediation_path.read_text() if remediation_path.exists() else None

    state_map = (run_dir / "flows" / flow_name / "state_map.json")
    if not state_map.exists():
        state_map = run_dir / "flows" / "state_map.json"
    mutations_dir = run_dir / "mutations" / flow_name
    if not mutations_dir.exists():
        mutations_dir = run_dir / "mutations"

    return {
        "findings": findings,
        "remediation": remediation,
        "flow_name": flow_name,
        "artifacts": {
            "state_map": str(state_map.exists()),
            "mutations": sorted(str(f.name) for f in mutations_dir.glob("*.py")) if mutations_dir.exists() else [],
        },
    }


@app.get("/api/assessments/reports")
async def list_reports():
    """List every run on disk that produced a report, with its summary."""
    runs_root = Path(os.environ.get("ARTIFACTS_DIR", ".")) / "runs"
    if not runs_root.exists():
        return {"reports": []}

    items: list[dict] = []
    for run_dir in sorted(runs_root.iterdir(), reverse=True):
        if not run_dir.is_dir():
            continue
        flow_name = run_dir.name
        findings_path = run_dir / "reports" / flow_name / "findings.json"
        if not findings_path.exists():
            findings_path = run_dir / "reports" / "findings.json"
        if not findings_path.exists():
            continue

        try:
            data = json.loads(findings_path.read_text())
        except (json.JSONDecodeError, OSError):
            continue

        summary = data.get("summary", {}) or {}
        results = data.get("results", []) or []
        # New schema: findings[] is the vulnerability list (may include
        # non-script findings like the auth check). Old schema: count results.
        findings = data.get("findings")
        if isinstance(findings, list):
            bugs_found = len(findings)
            critical = len([f for f in findings if isinstance(f, dict) and f.get("severity") == "Critical"])
        else:
            # Legacy schema: script results + free-form additional_observations
            # (the detail page counts both, so keep the index consistent).
            bugs_found = summary.get("bugs_found", 0)
            obs = data.get("additional_observations") or []
            bugs_found += len(obs)
            critical = len([o for o in obs if isinstance(o, dict) and o.get("severity") == "Critical"])
        # mtime as a stable, sortable "when" that needs no extra metadata
        try:
            mtime = findings_path.stat().st_mtime
            modified = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(mtime))
        except OSError:
            mtime = 0.0
            modified = ""

        items.append({
            "flow_name": flow_name,
            "target_url": data.get("target_url", ""),
            "run_timestamp": data.get("run_timestamp", ""),
            "modified": modified,
            "bugs_found": bugs_found,
            "critical_findings": critical,
            "rejected": summary.get("rejected", 0),
            "errors": summary.get("errors", 0),
            "total_scripts": data.get("total_scripts", len(results)),
        })

    # Most recently modified first
    items.sort(key=lambda x: x["modified"], reverse=True)
    return {"reports": items}


# ── Finish Recording (RECORD phase handshake) ────────────────────────────────

@app.post("/api/assessments/finish-recording")
async def finish_recording(flow_name: str = Query(default="default")):
    """Signal the RECORD phase that the user is done browsing.

    Writes a marker file that the Recorder agent (polling via File Read)
    detects, triggering HAR assembly, demo.json finalization, and browser close.
    """
    run_dir = os.environ.get("ARTIFACTS_DIR", ".")
    marker_path = Path(run_dir) / "runs" / flow_name / "recording_done.marker"
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    marker_path.write_text(time.strftime("%Y-%m-%dT%H:%M:%SZ"))
    logger.info("Recording marker written: %s", marker_path)
    return {"status": "recording_finishing", "flow_name": flow_name}


# ── Health ───────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok"}
