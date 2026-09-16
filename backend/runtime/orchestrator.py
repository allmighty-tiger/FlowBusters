"""
FlowBusters Orchestrator — V2 (thin wrapper).

Delegates all phase execution to crew_runner.py which spawns
a Claude Code subprocess running the full FlowBusters crew.

V1 logic (direct Anthropic API + Playwright Python) is removed.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger("flowbusters.runtime")


# ── Progress Events (preserved for SSE compatibility) ──────────────────────────

class Phase(str, Enum):
    RECORD = "record"
    ANALYZE = "analyze"
    MUTATE = "mutate"
    PROBE = "probe"
    REPORT = "report"
    COMPLETE = "complete"
    FAILED = "failed"


class RunMode(str, Enum):
    RECORDED_FLOW = "RECORDED_FLOW"
    CROSS_FLOW = "CROSS_FLOW"


@dataclass
class ProgressEvent:
    phase: Phase
    message: str
    done: bool = False
    error: Optional[str] = None
    run_mode: RunMode = RunMode.RECORDED_FLOW
    technical_detail: Optional[str] = None


# ── Thin Wrapper ───────────────────────────────────────────────────────────────

async def run_flowbusters(
    target_url: str,
    flow_name: str = "default",
    run_dir: str = ".",
    anthropic_api_key: str = "",
    anthropic_model: str = "claude-sonnet-5",
    progress_cb: Optional[Callable[[ProgressEvent], None]] = None,
    headless: bool = True,
    auto_complete: bool = False,
    cross_flow_inputs: dict | None = None,
) -> dict:
    """
    Execute the full FlowBusters pipeline via Claude Code crew subprocess.

    All arguments from the V1 interface are accepted for compatibility.
    `headless` and `auto_complete` are acknowledged but the RECORD phase
    is always user-driven (headed browser + marker file handshake).
    """
    from backend.runtime.crew_runner import CrewConfig, run_crew

    cb = progress_cb or _noop_progress
    run_mode = RunMode.CROSS_FLOW if cross_flow_inputs is not None else RunMode.RECORDED_FLOW

    def mode_cb(event: ProgressEvent):
        cb(replace(event, run_mode=run_mode))

    base = Path(__file__).parent.parent.parent.resolve()
    mcp_env = os.environ.get("MCP_CONFIG_PATH", "./mcp.json")
    crew_env = os.environ.get("CREW_DIR", "./crew")

    crew_dir = str((base / crew_env).resolve())
    mcp_config = str((base / mcp_env).resolve())
    claude_bin = os.environ.get("CLAUDE_CODE_BIN", "claude")
    phase_timeout = int(os.environ.get("PHASE_TIMEOUT", "600"))
    overall_timeout = int(os.environ.get("ASSESSMENT_TIMEOUT", "1800"))
    display = os.environ.get("DISPLAY", ":0")

    config = CrewConfig(
        target_url=target_url,
        flow_name=flow_name,
        run_dir=run_dir,
        crew_dir=crew_dir,
        mcp_config=mcp_config,
        claude_bin=claude_bin,
        model=anthropic_model,
        api_key=anthropic_api_key,
        display=display,
        phase_timeout=phase_timeout,
        overall_timeout=overall_timeout,
        auto_complete=auto_complete,
        cross_flow_inputs=cross_flow_inputs,
    )

    result = await run_crew(config, mode_cb)

    # Build findings for V1-compatible return
    findings_path = Path(run_dir) / "runs" / flow_name / "reports" / "findings.json"
    remediation_path = Path(run_dir) / "runs" / flow_name / "reports" / "remediation.md"
    scoped = Path(run_dir) / 'runs' / flow_name / 'reports' / flow_name
    if (scoped / 'findings.json').exists():
        findings_path = scoped / 'findings.json'
        remediation_path = scoped / 'remediation.md'

    if findings_path.exists():
        import json
        from backend.runtime.verification import load_report
        result["findings"] = load_report(findings_path)
    else:
        result["findings"] = {"summary": {"bugs_found": 0, "rejected": 0, "errors": 0}}

    if remediation_path.exists():
        result["remediation"] = remediation_path.read_text(encoding="utf-8")
    else:
        result["remediation"] = None

    return result


def _noop_progress(event: ProgressEvent):
    logger.info("[%s] %s", event.phase, event.message)
