"""
crew_runner.py — Spawns and monitors a Claude Code subprocess
that runs the full FlowBusters crew (all 4 phases).

The backend does NOT orchestrate phases. It:
  1. Prepares a working directory with vendored crew files
  2. Spawns `claude --print` with MCP config + system prompt
  3. Monitors stdout (stream-json) and filesystem (artifact watcher)
  4. Emits ProgressEvents on phase transitions
  5. Enforces timeouts
  6. Copies artifacts to canonical layout on exit
"""
from __future__ import annotations

import asyncio
import fnmatch
import glob
import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from backend.runtime.orchestrator import Phase, ProgressEvent
from backend.runtime.verification import load_report
from backend.runtime.recorder import record, RecordingError
from backend.runtime.ui_provenance import UIProvenanceError, validate_state_map
from backend.runtime.state_map_correction import correct_state_map, evidence_hashes

logger = logging.getLogger("flowbusters.crew_runner")


class ProbeContractError(ValueError):
    """A generated probe has a statically detectable output-contract defect."""

# ── Configuration ─────────────────────────────────────────────────────────────

@dataclass
class CrewConfig:
    target_url: str
    flow_name: str
    run_dir: str            # canonical artifact root (e.g. "." or "/some/path")
    crew_dir: str          # vendored crew/ in portal repo
    mcp_config: str         # path to mcp.json
    claude_bin: str         # path to claude binary
    model: str              # e.g. "claude-sonnet-5" (default), or your gateway model
    api_key: str
    display: str            # X11/Wayland display for headed browser
    phase_timeout: int      # per-phase timeout seconds
    overall_timeout: int    # overall assessment timeout seconds
    auto_complete: bool = False  # auto-write marker file after browser opens
    cross_flow_inputs: dict | None = None
    reanalysis_inputs: dict | None = None
    application_identity: dict | None = None


# ── Working Directory Preparation ─────────────────────────────────────────────

def prepare_run_dir(config: CrewConfig, flow_name: str) -> Path:
    """Copy vendored crew files into runs/<flow-name>/ and patch config.json."""
    base = Path(config.run_dir) / "runs" / flow_name
    if config.reanalysis_inputs is not None:
        base.parent.mkdir(parents=True, exist_ok=True)
        base.mkdir(exist_ok=False)
    else:
        base.mkdir(parents=True, exist_ok=True)

    # Only copy files actually read by the crew at runtime.
    # Everything else (templates/, casting/, decisions/, etc.) is dead weight
    # that slows Claude Code's working directory scan.
    _CREW_COPY_ITEMS = {
        "agents",       # charter.md for each agent
        "skills",       # SKILL.md for record/analyze/mutate/probe
        "scripts",      # synthesize_har.py
        "routing.md",
        "config.json",
    }

    # Agents and skills actually needed at runtime.
    _NEEDED_AGENTS = {"captain", "recorder", "analyst", "saboteur", "prober"}
    _NEEDED_SKILLS = {"record-flow", "analyze-har", "mutate-flow", "probe-flow"}
    # Files/patterns to skip when recursing into copied dirs.
    _SKIP_NAMES = {".DS_Store"}
    _SKIP_PATTERNS = [".DS_Store", "*.pre-har-helper"]

    crew_src = Path(config.crew_dir)
    crew_dst = base / "crew"
    crew_dst.mkdir(exist_ok=True)

    for item in crew_src.iterdir():
        if item.name not in _CREW_COPY_ITEMS:
            continue
        dst = crew_dst / item.name
        if item.is_dir():
            if dst.exists():
                shutil.rmtree(dst)
            dst.mkdir(exist_ok=True)
            for sub in item.iterdir():
                if sub.name in _SKIP_NAMES:
                    continue
                if any(fnmatch.fnmatch(sub.name, pat) for pat in _SKIP_PATTERNS):
                    continue
                if item.name == "agents" and sub.name not in _NEEDED_AGENTS:
                    continue
                if item.name == "skills" and sub.name not in _NEEDED_SKILLS:
                    continue
                if sub.is_dir():
                    if (dst / sub.name).exists():
                        shutil.rmtree(dst / sub.name)
                    shutil.copytree(sub, dst / sub.name,
                        ignore=shutil.ignore_patterns(*_SKIP_PATTERNS))
                else:
                    shutil.copy2(sub, dst / sub.name)
        else:
            shutil.copy2(item, dst)

    # Patch config.json teamRoot
    config_json = crew_dst / "config.json"
    if config_json.exists():
        cfg = json.loads(config_json.read_text(encoding="utf-8"))
        cfg["teamRoot"] = str(base)
        config_json.write_text(json.dumps(cfg, indent=2), encoding="utf-8")

    # Copy scope.json from run_dir root
    scope_src = Path(config.run_dir) / "scope.json"
    if scope_src.exists():
        scope_dst = base / "scope.json"
        shutil.copy2(scope_src, scope_dst)
        scope = json.loads(scope_dst.read_text(encoding="utf-8"))
        configured_setup_paths = [value.strip() for value in
                                  os.environ.get("SETUP_PATHS", "/api/demo/reset").split(",")
                                  if value.strip()]
        scope["setup_paths"] = list(dict.fromkeys([
            *(scope.get("setup_paths") or []), *configured_setup_paths]))
        scope_dst.write_text(json.dumps(scope, indent=2), encoding="utf-8")

    # Create artifact directories
    (base / "flows" / flow_name).mkdir(parents=True, exist_ok=True)
    (base / "mutations" / flow_name).mkdir(parents=True, exist_ok=True)
    (base / "reports" / flow_name).mkdir(parents=True, exist_ok=True)

    # Identity is backend-issued before any agent starts.  Agent output, the
    # target URL, and the run name can never create or alter this binding.
    if config.application_identity:
        from backend.runtime.application_identity import issue_identity
        issue_identity(base, Path(config.run_dir), config.target_url,
                       config.application_identity)

    logger.info("Run directory prepared: %s", base)
    return base


# ── System Prompt Assembly ────────────────────────────────────────────────────

def build_system_prompt(run_dir: Path, flow_name: str) -> str:
    """Build recording prompt and write the deferred continuation to disk."""
    crew = run_dir / "crew"

    parts: list[str] = []

    parts.append(
        "# FLOWBUSTERS CREW — SINGLE SESSION MODE\n\n"
        "You are running the FlowBusters assessment crew in a single session.\n"
        "Follow the Captain's protocol to execute all 4 phases sequentially.\n\n"
        "WORKING DIRECTORY is your current directory. All artifact paths are relative to here.\n\n"
        "For flow name, use: " + flow_name + "\n\n"
        "---\n\n"
    )

    # Preserve the startup scope gate while deferring later-phase instructions.
    parts.append(
        "Before browser navigation validate the flow name as kebab-case. "
        "If scope.json exists, read it and enforce allowed_domains, "
        "allowed_paths_prefix and block_production exactly as configured. "
        "Abort for out-of-scope or blocked production targets. Never test production "
        "without explicit user confirmation. Do not skip phase gates.\n"
        "Start with Recorder now. Do not read later-phase files before recording.\n"
    )
    for relative in ("agents/recorder/charter.md", "skills/record-flow/SKILL.md"):
        parts.append((crew / relative).read_text(encoding="utf-8"))

    deferred = ["agents/captain/charter.md", "routing.md",
                "agents/analyst/charter.md", "skills/analyze-har/SKILL.md",
                "skills/analyze-har/OBSERVED_UI_RULES.md",
                "agents/saboteur/charter.md", "skills/mutate-flow/SKILL.md",
                "agents/prober/charter.md", "skills/probe-flow/SKILL.md",
                "skills/probe-flow/VERIFICATION.md"]
    continuation = "# Post-recording instructions\n\n"
    for relative in deferred:
        continuation += "\n---\n# " + relative + "\n" + (crew / relative).read_text(encoding="utf-8") + "\n"
    continuation += (
        "\nBefore writing state_map.json, audit every observed_ui_rules fact "
        "against this exact, inseparable type/provenance/artifact matrix:\n"
        "- explicit_ui_text + explicit_visible_ui_text + demo.json\n"
        "- ui_element_transition + observed_ui_affordance + demo.json\n"
        "- api_field_transition + api_state_fact + recording.har\n"
        "A visible element at one sampled step is explicit_ui_text, even when "
        "it is a button or other affordance. Use ui_element_transition and "
        "observed_ui_affordance only for an exact supported before/after "
        "transition. Never mix values between rows. The artifact field is "
        "required on every fact and may not be inferred from its JSON pointer. "
        "Check each rule independently: it must include a directly relevant "
        "explicit_ui_text or ui_element_transition fact from demo.json. "
        "API-only facts or inference cannot populate observed_ui_rules; omit "
        "such rules and their references, never pad them with unrelated UI facts. "
        "Useful API-based hypotheses may remain in critical_endpoints[].why "
        "labelled Agent inference (unverified), not verified provenance. "
        "Re-read the completed file and verify every row before continuing.\n"
        "\nExecute mutation scripts with python3 <script_path>.py <target_url>, "
        "30-second timeout each. HTTP codes alone never establish a verdict. "
        "Apply the verification contract; missing state evidence is NEEDS_REVIEW.\n"
        "Recording is already complete. Resume at ANALYZE, then MUTATE, PROBE "
        "and REPORT, sequentially in this same session. Do not restart Recorder.\n"
    )
    (run_dir / "_post_recording_instructions.md").write_text(continuation, encoding="utf-8")

    # Gate relaxation — inline with Captain charter, not a separate section.

    # RECORD phase instructions for marker file + HAR assembly
    parts.append(
        "\n---\n\n# RECORD PHASE — MARKER FILE PROTOCOL\n\n"
        "After navigating to the target URL and opening the browser:\n\n"
        "1. Take an initial browser_snapshot and save interaction data.\n"
        "2. Wait for the user to finish browsing. You MUST use a single BLOCKING bash command:\n"
        "       timeout 3600 bash -c 'while [ ! -f recording_done.marker ]; do sleep 5; done'\n"
        "   CRITICAL: This command blocks your turn, keeping the session (and the browser) alive.\n"
        "   Do NOT end your turn before this command returns — if your turn ends, the session\n"
        "   exits, the browser closes, and the recording is lost. Do NOT announce that you are\n"
        "   going to wait in the background; start the blocking wait immediately after the\n"
        "   browser is open. When the command returns, the user is done browsing.\n"
        "3. Once the marker file appears, the USER IS DONE. From this point on, NEVER click,\n"
        "   navigate, type, or interact with any page — the recording is finished and the\n"
        "   browser is only used to dump already-captured network data. Do one network dump\n"
        "   pass (steps c-d), then close the browser. Extra navigation or repeated re-dumps\n"
        "   waste the user's time.\n"
        "   a. Take a final browser_snapshot.\n"
        "   b. Create the har_data directory: mkdir -p flows/" + flow_name + "/har_data\n"
        "   c. List captured API requests directly to disk:\n"
        "      browser_network_requests(static=false, filter=\"/api/.*\", filename=\"flows/" + flow_name + "/har_data/network_requests.log\")\n"
        "      Read the file and preserve its printed 1-based indexes (filtering may leave gaps).\n"
        "   d. For EACH printed index N, format N as NNN (001, 002, ...) and call all three:\n"
        "      browser_network_request(index=N, filename=\"flows/" + flow_name + "/har_data/request_NNN.log\")\n"
        "      browser_network_request(index=N, part=\"request-body\", filename=\"flows/" + flow_name + "/har_data/request_NNN_request_body.txt\")\n"
        "      browser_network_request(index=N, part=\"response-body\", filename=\"flows/" + flow_name + "/har_data/request_NNN_response_body.txt\")\n"
        "      The full-detail call does NOT contain bodies; the two part calls are required.\n"
        "      Empty GET request bodies are normal. Never infer response text from headers.\n"
        "\n"
        ">>> STOP — CLOSE THE BROWSER NOW <<<\n"
        "   The last browser call you need is done. IMMEDIATELY call browser_close and confirm\n"
        "   the call returned before you do ANYTHING else (no synthesize, no demo.json, no\n"
        "   announce). The user is watching a stuck Chrome window — leaving it open breaks\n"
        "   their trust in the tool. If browser_close errors, retry it once. Only proceed to\n"
        "   the next step after the browser is closed.\n"
        "\n"
        "   Then continue on disk (browser no longer needed):\n"
        "   e. Run: python3 crew/scripts/synthesize_har.py flows/" + flow_name + "/har_data flows/" + flow_name + "/recording.har\n"
        "   f. Verify synthesize_har.py exited 0 and recording.har exists\n"
        "   g. Write flows/" + flow_name + "/demo.json with interaction trace.\n"
        "   h. Validate recording.har: it MUST have {log: {version, creator, entries}}\n"
        "      and successful JSON responses must contain response.content.text.\n"
        "      If invalid, report the error and do NOT proceed to Phase 2.\n"
        "4. Announce RECORD phase complete with both file paths and counts.\n\n"
        "IMPORTANT: If the target URL has SSL certificate errors, use browser_navigate with\n"
        "ignoreHttpsErrors=true or continue anyway. Do NOT ask the user for permission —\n"
        "proceed with the assessment. This is a test environment.\n\n"
    )

    parts.append(
        "# CONTINUE AFTER RECORDING\n"
        "After recording_done.marker appears, finish the network dump, close the "
        "browser, synthesize and validate HAR and demo.json as instructed above. "
        "Then read ALL of _post_recording_instructions.md (continue reading if "
        "truncated) before analysis. Follow its phase gates and verification "
        "contract through final report. Do not end the session after recording.\n"
    )

    return "\n".join(parts)


# ── Stream-JSON Parser ────────────────────────────────────────────────────────

class StreamJsonParser:
    """Parse claude --output-format stream-json events from stdout."""

    def __init__(self):
        self.buffer = ""
        self.turn_count = 0
        self.tool_calls: list[dict] = []
        self.last_phase: str = ""
        self.first_browser_call_emitted = False
        self.first_browser_call_emitted_was_signaled = False

    def has_first_browser_call(self) -> bool:
        return self.first_browser_call_emitted

    def feed(self, text: str) -> list[dict]:
        """Feed text, return any complete JSON events parsed."""
        self.buffer += text
        events = []
        while True:
            # Try to extract a JSON object from the buffer
            idx = self.buffer.find("\n")
            if idx < 0:
                break
            line = self.buffer[:idx].strip()
            self.buffer = self.buffer[idx + 1:]
            if not line:
                continue
            try:
                obj = json.loads(line)
                events.append(obj)
                ttype = obj.get("type", "")
                if ttype == "result":
                    self.turn_count += 1
                    subtype = obj.get("subtype", "")
                    if subtype == "success":
                        result_text = obj.get("result", "")
                        # Detect phase transitions from result text
                        self._detect_phase(result_text)
                elif ttype == "tool_use":
                    self.tool_calls.append(obj)
                    name = obj.get("tool_name", "")
                    self._detect_tool_phase(name)
                elif ttype == "assistant":
                    # stream-json nests tool calls inside assistant message content
                    msg = obj.get("message", {})
                    for block in msg.get("content", []):
                        if isinstance(block, dict) and block.get("type") == "tool_use":
                            name = block.get("name", "")
                            self.tool_calls.append(block)
                            self._detect_tool_phase(name)
            except json.JSONDecodeError:
                pass
        return events

    def _detect_phase(self, text: str):
        t = text.lower()
        if "record" in t and ("complete" in t or "phase 1" in t):
            self.last_phase = "record"
        elif "analyze" in t and ("complete" in t or "phase 2" in t or "state_map" in t):
            self.last_phase = "analyze"
        elif "mutate" in t and ("complete" in t or "phase 3" in t or "mutation" in t):
            self.last_phase = "mutate"
        elif "probe" in t and ("complete" in t or "phase 4" in t or "findings" in t):
            self.last_phase = "probe"

    def _detect_tool_phase(self, tool_name: str):
        # MCP tools are namespaced by Claude Code as mcp__<server>__<tool>,
        # so a browser call arrives as e.g. mcp__playwright__browser_navigate.
        # Match the substring to be robust to that prefixing.
        if "browser_" in tool_name:
            self.last_phase = "record"
            self.first_browser_call_emitted = True


# ── Artifact Filesystem Watcher ───────────────────────────────────────────────

class ArtifactWatcher:
    """Poll the run directory for artifact files appearing."""

    def __init__(self, run_dir: Path, flow_name: str):
        self.run_dir = run_dir
        self.flow_name = flow_name
        self.found: dict[str, Path] = {}
        self.flows = run_dir / "flows" / flow_name
        self.mutations = run_dir / "mutations" / flow_name
        self.reports = run_dir / "reports" / flow_name

    def check(self) -> list[tuple[str, Path]]:
        """Return list of (name, path) for newly found artifacts."""
        new: list[tuple[str, Path]] = []
        checks = [
            ("demo.json", self.flows / "demo.json"),
            ("recording.har", self.flows / "recording.har"),
            ("state_map.json", self.flows / "state_map.json"),
            ("findings.json", self.reports / "findings.json"),
            ("remediation.md", self.reports / "remediation.md"),
        ]
        for name, path in checks:
            if name not in self.found and path.exists() and path.stat().st_size > 0:
                if name == "state_map.json":
                    try:
                        state_map = json.loads(path.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError) as exc:
                        raise UIProvenanceError(f"Invalid state_map.json: {exc}") from exc
                    validate_state_map(state_map, self.flows, self.flow_name, require_current=True)
                self.found[name] = path
                new.append((name, path))

        # Check mutations (≥3 .py files)
        if "mutations" not in self.found:
            pys = sorted(self.mutations.glob("*.py")) if self.mutations.exists() else []
            if len(pys) >= 3:
                from backend.runtime.probe_executor import preflight_script_contract
                for script in pys:
                    error = preflight_script_contract(script)
                    if error:
                        raise ProbeContractError(f"{script.name}: {error}")
                self.found["mutations"] = self.mutations
                new.append(("mutations", self.mutations))

        return new


# ── HAR Validator ──────────────────────────────────────────────────────────────

def validate_har(path: Path) -> tuple[bool, str]:
    """Validate a HAR file is well-formed HAR 1.2."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        return False, f"Invalid JSON in HAR file: {e}"

    log = data.get("log")
    if not log:
        return False, "HAR missing top-level 'log' object"
    if log.get("version") != "1.2":
        return False, f"HAR version is '{log.get('version')}', expected '1.2'"
    if "creator" not in log:
        return False, "HAR missing 'log.creator' object"
    if "entries" not in log:
        return False, "HAR missing 'log.entries' array"
    if not isinstance(log["entries"], list):
        return False, "HAR 'log.entries' is not an array"

    return True, f"Valid HAR 1.2 with {len(log['entries'])} entries"


async def prepare_recording_evidence(config, run_dir, mcp_config_arg, env, progress_cb):
    """Prepare one recording source; reanalysis deliberately never calls Playwright."""
    if config.cross_flow_inputs is not None:
        from backend.runtime.application_model import prepare_inputs
        from backend.runtime.ui_provenance import validate_cross_flow_manifest
        prepare_inputs(run_dir, config.flow_name, config.cross_flow_inputs, root=config.run_dir)
        progress_cb(ProgressEvent(Phase.ANALYZE, 'Authenticating source evidence', done=False))
        validate_cross_flow_manifest(run_dir, root=config.run_dir)
        (run_dir / 'recording_done.marker').write_text('cross-flow snapshots ready', encoding='utf-8')
        progress_cb(ProgressEvent(
            Phase.ANALYZE,
            'Source evidence authenticated',
            done=False,
            technical_detail='Cross-flow evidence manifest validated',
        ))
        return
    if config.reanalysis_inputs is not None:
        from backend.runtime.reanalyze import prepare_reanalysis_inputs
        prepare_reanalysis_inputs(
            run_dir, config.flow_name, config.reanalysis_inputs, root=config.run_dir)
        progress_cb(ProgressEvent(
            Phase.ANALYZE,
            f'Validated recording copied from {config.reanalysis_inputs["source_run"]}',
            done=False,
            technical_detail='recording_source.json authenticated; Playwright was not started',
        ))
        return
    await record(config, run_dir, mcp_config_arg, env,
                 lambda message: progress_cb(ProgressEvent(Phase.RECORD, message, done=False)))


def validate_final_state_map(run_dir, flow_name):
    """Always revalidate the final on-disk map immediately before probe execution."""
    path = Path(run_dir) / 'flows' / flow_name / 'state_map.json'
    try:
        state_map = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        raise UIProvenanceError(f'Invalid state_map.json: {exc}') from exc
    return validate_state_map(state_map, path.parent, flow_name, require_current=True)


async def execute_validated_probes(run_dir, flow_name, root, progress,
                                   execution_progress=None):
    """The only runner entry used here: state-map validation precedes execution."""
    validate_final_state_map(run_dir, flow_name)
    from backend.runtime.endpoint_catalog import prepare_endpoint_catalog
    prepare_endpoint_catalog(run_dir, flow_name, root)
    from backend.runtime.coverage_plans import prepare_coverage_probes
    prepare_coverage_probes(run_dir, flow_name, root)
    from backend.runtime.probe_executor import execute_run
    return await execute_run(run_dir, flow_name, root, progress,
                             execution_progress=execution_progress)


def _crew_chrome_pids(temp_home: str) -> list[int]:
    """PIDs of the crew's Chrome processes (launched under our temp HOME)."""
    pids: list[int] = []
    for cmdline in glob.glob("/proc/[0-9]*/cmdline"):
        try:
            with open(cmdline, "rb") as f:
                proc_args = f.read().decode("utf-8", "replace").split("\0")
        except OSError:
            continue
        if not proc_args or "chrome" not in proc_args[0].lower():
            continue
        if temp_home and temp_home in "".join(proc_args):
            pids.append(int(cmdline.split("/")[2]))
    return pids


def crew_browser_alive(temp_home: str) -> bool:
    """Return True if the crew's own Chrome is still running.

    The crew's Chrome is launched by playwright-mcp with a --user-data-dir
    under the isolated temp HOME we created for this run. Matching that path
    in a live process's cmdline lets us tell the crew's window apart from the
    user's personal browser (whose profile lives under the real ~). We read
    /proc directly to avoid any subprocess that could self-match.
    """
    return bool(_crew_chrome_pids(temp_home))


async def kill_crew_browser(temp_home: str) -> int:
    """Deterministically terminate the crew's Chrome processes (SIGTERM, then
    SIGKILL stragglers). Called by the watcher once the recording is fully
    captured (recording.har exists) so the window closes at the phase boundary
    even if the crew never issues its own browser_close. Returns the number of
    processes signalled. Only targets processes launched under this run's temp
    HOME, so the user's personal browser is never touched.
    """
    pids = _crew_chrome_pids(temp_home)
    if not pids:
        return 0
    for sig in (signal.SIGTERM, signal.SIGKILL):
        for pid in list(pids):
            try:
                os.kill(pid, sig)
            except (ProcessLookupError, PermissionError):
                pass
        await asyncio.sleep(1.5)
        pids = _crew_chrome_pids(temp_home)
        if not pids:
            break
    return len(pids)


# ── Main Runner ───────────────────────────────────────────────────────────────

async def run_crew(
    config: CrewConfig,
    progress_cb: Callable[[ProgressEvent], None],
) -> dict:
    """
    Spawn Claude Code subprocess, monitor it, emit progress, enforce timeouts.

    Returns dict with artifact paths and findings.
    """
    started_at = time.monotonic()
    milestones: dict[str, float] = {}
    flow_name = config.flow_name
    cross_flow = config.cross_flow_inputs is not None
    reanalysis = config.reanalysis_inputs is not None
    if cross_flow and reanalysis:
        raise ValueError('Cross-flow and single-flow reanalysis modes are mutually exclusive')

    # Prepare working directory
    run_dir = prepare_run_dir(config, flow_name)

    def startup_mark(name: str):
        if name in milestones:
            return
        milestones[name] = round(time.monotonic() - started_at, 3)
        logger.info("Startup timing %s: %.3fs since run_crew entry", name, milestones[name])
        try:
            (run_dir / "startup_timing.json").write_text(json.dumps({
                "clock": "seconds since run_crew entry (not button click)",
                "milestones": milestones,
            }, indent=2), encoding="utf-8")
        except OSError:
            logger.warning("Could not write startup timing file")

    startup_mark("run_directory_ready")
    # Build system prompt
    system_prompt = build_system_prompt(run_dir, flow_name)
    prompt_path = run_dir / "_system_prompt.txt"
    prompt_path.write_text(system_prompt, encoding="utf-8")
    startup_mark("prompt_ready")
    logger.info("Startup prompt size: %d characters", len(system_prompt))

    # Initial user message
    initial_message = (
        f"Captain, run FlowBusters against {config.target_url} --flow-name {flow_name}"
    )

    # Build isolated environment.
    # Start from full env (needed for Node/npm/claude binary),
    # then override only what matters for isolation.
    temp_home = tempfile.mkdtemp(prefix="fb-crew-")
    env = dict(os.environ)
    env["HOME"] = temp_home
    env["ANTHROPIC_API_KEY"] = config.api_key
    env["DISPLAY"] = config.display or ":0"
    # Strip potentially sensitive vars that Claude Code shouldn't need
    env.pop("CLAUDE_CODE_MCP_CONFIG", None)
    env.pop("CLAUDE_CODE_SETTINGS", None)

    # Build command — use --system-prompt-file for reliable large prompts.
    # prompt_path is inside cwd (run_dir), so use just the filename.
    # mcp_config must also be relative to cwd or absolute.
    mcp_config_arg = config.mcp_config
    if not os.path.isabs(mcp_config_arg):
        # Resolve relative to portal root, then make relative to cwd
        portal_base = Path(__file__).parent.parent.parent.resolve()
        resolved_mcp = (portal_base / config.mcp_config).resolve()
        mcp_config_arg = str(resolved_mcp)

    # Recording belongs to the backend, not to a model turn. Do not launch the
    # analyst until MCP has saved and validated the complete recording.
    source_count = len(config.cross_flow_inputs.get('sources', [])) if cross_flow else 0
    if cross_flow:
        recording_message = f'Loading {source_count} source flows'
    elif reanalysis:
        recording_message = (
            f'Loading validated recording from {config.reanalysis_inputs["source_run"]}')
    else:
        recording_message = 'Opening recording browser...'
    progress_cb(ProgressEvent(
        Phase.RECORD,
        recording_message,
        done=False,
    ))
    try:
        await prepare_recording_evidence(
            config, run_dir, mcp_config_arg, env, progress_cb)
        from backend.runtime.endpoint_catalog import prepare_endpoint_catalog
        prepare_endpoint_catalog(run_dir, flow_name, config.run_dir)
    except (RecordingError, OSError, ValueError, asyncio.TimeoutError) as exc:
        if cross_flow:
            message = f'Cross-flow source evidence failed: {exc}'
        elif reanalysis:
            message = f'Reanalysis source evidence failed: {exc}'
        else:
            message = f'Recording failed: {exc}'
        progress_cb(ProgressEvent(Phase.FAILED, message, done=True, error=message))
        shutil.rmtree(temp_home, ignore_errors=True)
        return {'error': message, 'run_dir': str(run_dir), 'exit_code': -1,
                'total_time': time.monotonic() - started_at, 'artifacts': {}}
    startup_mark('recording_validated')
    recording_hashes = evidence_hashes(run_dir, flow_name)
    system_prompt = (run_dir / '_post_recording_instructions.md').read_text(encoding="utf-8")
    system_prompt += (
        '\nThe backend completed RECORD and closed its MCP session. '
        'Read the existing flows/' + flow_name + '/recording.har and demo.json. '
        'Do not reopen a browser or repeat recording. Use HTTP probes for testing. '
        'Read scope.json and enforce allowed_domains, allowed_paths_prefix and '
        'block_production before probing. Start at ANALYZE.\n')
    if config.cross_flow_inputs is not None:
        from backend.runtime.application_model import CROSS_FLOW_INSTRUCTIONS
        system_prompt += CROSS_FLOW_INSTRUCTIONS.replace('{flow}', flow_name)
    elif reanalysis:
        system_prompt += (
            '\nMODE: SINGLE_FLOW_REANALYSIS. The backend copied and authenticated '
            f'recording evidence from source run {config.reanalysis_inputs["source_run"]} '
            f'into new run {flow_name}. Do not read or modify the source run. Treat the '
            'copied demo.json and recording.har as this destination run\'s immutable raw '
            f'evidence. Every new state_map flow_name and fact source_run must be {flow_name}. '
            'Do not copy or reuse any source state map, draft, mutation, report, or receipt.\n')
    system_prompt += '''
BACKEND EXECUTION CONTRACT (supersedes all earlier probing instructions):
Read endpoint_catalog.json. Use its exact method/origin/path entries and complete
workflow chains. This backend-authenticated inventory distinguishes recording
observations from backend application contracts. A UI button label cannot supply
a URL. Never shorten /refund/request to /refund or infer routes from button names.
If a required endpoint is absent, leave the scenario NOT_EXECUTED with the missing
endpoint precondition instead of guessing. Do not modify endpoint_catalog.json.
Do not write backend_coverage/: those scripts are independently generated by the
backend and appear separately from AI probes in the signed execution results.
You prepare mutation scripts and a draft findings.json/remediation.md, then exit.
Do NOT execute mutation scripts or send HTTP probes yourself. The backend runs
every script once after your session ends, captures HTTPX/requests transport
traffic and signs execution receipts. Do not edit scripts after execution.
Follow crew/skills/probe-flow/EXECUTION.md for the raw output contract.
Write suspected findings with script and source MUTATION_SCRIPT; leave
verification absent in the draft. Their evidence comes from script stdout and
must match the transport capture. Draft statuses are NEEDS_REVIEW.
Additional verification requests belong in separate Python scripts under
verification_probes/FLOW_NAME/, source VERIFICATION_PROBE, with triggered_by
pointing to the original mutation script. Never attribute those calls to it.
Do not execute scope setup/reset manually: each script establishes its own
preconditions under scope. If unavailable emit NOT_EXECUTED. Exit when ready.
'''.replace('FLOW_NAME', flow_name)
    prompt_path.write_text(system_prompt, encoding="utf-8")
    initial_message = f'Analyze the validated recording for {config.target_url}, flow {flow_name}; continue through REPORT.'
    # No browser tools are needed downstream; prevent accidental re-opening.
    analysis_mcp = run_dir.resolve() / '_analysis_mcp.json'
    analysis_mcp.write_text('{"mcpServers": {}}', encoding="utf-8")
    mcp_config_arg = str(analysis_mcp)

    # The crew runs with an isolated temp HOME, so the interactive session's
    # effort setting (~/.claude/settings.json) does not apply — the CLI falls
    # back to its default, which some gateways reject (400). Pass an explicit
    # effort level; override via CREW_EFFORT if the gateway changes.
    effort = os.environ.get("CREW_EFFORT", "medium")

    cmd = [
        config.claude_bin,
        "--print",
        "--output-format", "stream-json",
        "--verbose",
        "--mcp-config", mcp_config_arg,
        "--strict-mcp-config",
        "--tools", "Read,Write,Edit,Glob,Grep",
        "--model", config.model,
        "--effort", effort,
        "--system-prompt-file", prompt_path.name,
        "--dangerously-skip-permissions",
        initial_message,
    ]

    if not cross_flow:
        progress_cb(ProgressEvent(Phase.ANALYZE, "Starting analysis of validated recording...", done=False))

    # Spawn subprocess
    # limit= raises the StreamReader's max line length (default 64 KB).
    # stream-json emits one JSON event per line, and tool results can embed
    # whole files (e.g. the Analyst reading recording.har) — a single line
    # >64 KB makes readline() raise "Separator is found, but chunk is longer
    # than limit", which used to kill the run as a false failure.
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(run_dir),
        env=env,
        limit=64 * 1024 * 1024,
    )

    logger.info("Claude Code cmd: %s", " ".join(cmd))
    logger.info("Claude Code cwd: %s", run_dir)
    logger.info("Claude Code mcp_config exists: %s", Path(config.mcp_config).exists())
    logger.info("Claude Code prompt_file exists: %s", prompt_path.exists())
    startup_mark("agent_process_started")
    logger.info("Claude Code subprocess started (pid=%d)", proc.pid)
    if cross_flow:
        progress_cb(ProgressEvent(
            Phase.ANALYZE,
            'Building application state model',
            done=False,
            technical_detail='Crew process started (pid %d)' % proc.pid,
        ))
    else:
        progress_cb(ProgressEvent(Phase.ANALYZE, "✅ Crew running (pid %d)" % proc.pid, done=False))

    parser = StreamJsonParser()
    watcher = ArtifactWatcher(run_dir, flow_name)
    phase_timers: dict[str, float] = {}
    overall_start = time.time()
    error_msg: Optional[str] = None

    # Recording-window health: once the crew's Chrome has launched we watch for
    # it dying. If the window vanishes before the user writes the finish marker,
    # they can no longer click "Finish Recording" and the marker-wait loop would
    # otherwise hang until the phase/overall timeout — so fail fast with a clear
    # message instead.
    browser_seen = False
    window_dead_since: Optional[float] = None
    marker_announced = True  # backend already completed and validated RECORD
    browser_closed_announced = False
    browser_force_closed = True  # backend-owned MCP session already closed
    # Set the moment remediation.md lands: the report is DONE then, even if the
    # crew is still printing a final summary. Emit COMPLETE immediately so the
    # "View report" button appears at the end of the Report step instead of
    # waiting for the crew process to exit.
    completion_emitted = False
    pending_state_map_error = None

    # Auto-complete: in test mode, write the marker after a short delay
    # so the Recorder can finalize without human interaction.
    # We use a time-based approach since stream-json doesn't expose
    # individual tool_use events (they're internal to turns).
    auto_marker_written = False
    auto_delay = 15  # seconds after process start to write marker

    stdout_events: list[str] = []

    async def read_stdout():
        nonlocal error_msg
        while True:
            try:
                line = await proc.stdout.readline()
            except ValueError as e:
                # Backstop: even with the raised limit, an oversized line must
                # not take down the whole run (it once surfaced in the UI as
                # "Failed: Separator is found, but chunk is longer than limit").
                logger.warning("Oversized stdout line dropped: %s", e)
                await proc.stdout.read()
                continue
            if not line:
                break
            text = line.decode("utf-8", errors="replace")
            startup_mark("first_agent_output")
            stdout_events.append(text)
            parser.feed(text)

            # Emit "opening browser" SSE event when first browser_* tool call detected
            if parser.has_first_browser_call() and not parser.first_browser_call_emitted_was_signaled:
                parser.first_browser_call_emitted_was_signaled = True
                startup_mark("first_browser_tool_requested")
                progress_cb(ProgressEvent(
                    Phase.RECORD,
                    "🌐 Browser tool requested — waiting for browser process...",
                    done=False,
                ))

    async def read_stderr():
        while True:
            line = await proc.stderr.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").strip()
            if text:
                logger.debug("stderr: %s", text[:200])

    async def watch_artifacts():
        nonlocal error_msg, auto_marker_written, browser_seen, window_dead_since, marker_announced, browser_closed_announced, completion_emitted, browser_force_closed, pending_state_map_error
        while proc.returncode is None:
            try:
                new_artifacts = watcher.check()
            except UIProvenanceError as exc:
                # No simultaneous writers: correction runs after this agent
                # exits. Draft generation is not permission to execute probes.
                new_artifacts = []
                if pending_state_map_error != str(exc):
                    pending_state_map_error = str(exc)
                    progress_cb(ProgressEvent(Phase.ANALYZE,
                        f'State map rejected: {exc}. Analyst correction queued after draft generation; probes blocked.',
                        done=False))
            except ProbeContractError as exc:
                error_msg = f'Probe contract validation failed: {exc}'
                try:
                    proc.terminate()
                except ProcessLookupError:
                    pass  # The agent may have exited while the gate ran.
                try:
                    await asyncio.wait_for(proc.wait(), timeout=5)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()
                return
            else:
                pending_state_map_error = None

            # Announce once when the user's Finish marker lands: the browser is
            # being torn down and the crew moves on to analyze. Without this the
            # UI goes silent between the Finish click and the first artifact.
            if not marker_announced and (run_dir / "recording_done.marker").exists():
                marker_announced = True
                logger.info("Recording finished by user — moving to analysis")
                progress_cb(ProgressEvent(
                    Phase.ANALYZE,
                    "📝 Recording finished — closing browser and analyzing captured flow",
                    done=False,
                ))

            # Deterministic browser close at the phase boundary. The crew's own
            # browser_close is LLM-driven and sometimes doesn't happen, leaving a
            # stuck Chrome window that lingers through the analyze phase. Once the
            # recording is fully captured — the user's Finish marker has landed AND
            # recording.har exists (it is synthesized from the completed network
            # dump, so the browser is no longer needed for it) — we kill the crew's
            # Chrome ourselves. The crew proceeds to analyze/probe on disk and its
            # probe scripts launch their own fresh browser, so nothing downstream
            # depends on this window.
            if not browser_force_closed and (run_dir / "recording_done.marker").exists() \
                    and (run_dir / "flows" / flow_name / "recording.har").exists():
                if crew_browser_alive(temp_home):
                    killed = await kill_crew_browser(temp_home)
                    browser_force_closed = True
                    # The "Browser closed — recording secured" event below fires
                    # on the next check once the process is confirmed dead, so we
                    # log here but do not emit our own event (avoids a double line).
                    logger.info("Browser force-closed at record->analyze boundary (%d procs signalled)", killed)

            # Announce once when the crew's browser process actually exits after
            # the user finished. The "closing browser" event above fires the
            # moment the marker lands, but the user wants confirmation the window
            # is really gone — so detect the Chrome process actually dying.
            if (browser_seen and not browser_closed_announced
                    and not crew_browser_alive(temp_home)):
                browser_closed_announced = True
                logger.info("Crew browser window confirmed closed")
                progress_cb(ProgressEvent(
                    Phase.RECORD,
                    "✅ Browser closed — recording secured",
                    done=False,
                ))

            # Recording-window health check.
            if not watcher.found and not (run_dir / "recording_done.marker").exists():
                if parser.has_first_browser_call() and not browser_seen:
                    if crew_browser_alive(temp_home):
                        browser_seen = True
                        startup_mark("browser_process_detected")
                        window_dead_since = None
                        logger.info("Crew recording window confirmed open")
                    else:
                        logger.debug("Crew Chrome not yet visible (launch in progress)")
                elif browser_seen:
                    if not crew_browser_alive(temp_home):
                        if window_dead_since is None:
                            window_dead_since = time.time()
                            logger.warning("Crew recording window disappeared")
                        elif time.time() - window_dead_since > 10:
                            error_msg = (
                                "The recording browser window closed unexpectedly before "
                                "you clicked 'Finish Recording', so the flow couldn't be "
                                "captured. Please start the assessment again."
                            )
                            progress_cb(ProgressEvent(
                                Phase.FAILED,
                                "❌ Recording window closed unexpectedly — "
                                "please start the assessment again",
                                done=True, error=error_msg,
                            ))
                            proc.terminate()
                            return
                    else:
                        window_dead_since = None

            for name, path in new_artifacts:
                # findings.json means probing is FINISHED (not just started), and
                # remediation.md means the report is compiled. Emit these as
                # done=True so the progress page advances Probe→Report→done
                # immediately instead of holding on "Probe in progress" until
                # the very next artifact.
                artifact_phase = _artifact_to_phase(name)
                # Each phase's OWN terminal artifact means that phase is finished:
                # state_map.json → Analyze, mutations → Mutate, findings.json → Probe,
                # remediation.md → Report. Emit done=True for all four so the
                # progress page advances each step the moment it's complete instead
                # of holding "X in progress" until the next phase's artifact lands.
                artifact_done = name in (
                    "state_map.json", "mutations",
                )
                if name in ('findings.json', 'remediation.md'):
                    artifact_phase = Phase.MUTATE
                if cross_flow:
                    for event in _cross_flow_artifact_events(name):
                        progress_cb(event)
                else:
                    progress_cb(ProgressEvent(
                        artifact_phase,
                        f"📄 Artifact written: {name}",
                        done=artifact_done,
                    ))
                logger.info("Artifact detected: %s", path)

                # The final report is complete the instant remediation.md exists —
                # declare it now rather than waiting for the crew to exit.
                # Agent artifacts are drafts. Only backend execution and final
                # provenance reconciliation can complete the assessment.

                # Validate HAR immediately
                if name == "recording.har":
                    ok, msg = validate_har(path)
                    if not ok:
                        error_msg = (f"Invalid cross-flow evidence manifest: {msg}" if cross_flow
                                     else f"Invalid HAR file: {msg}")
                        progress_cb(ProgressEvent(
                            Phase.FAILED,
                            (f"Cross-flow evidence manifest validation failed: {msg}" if cross_flow
                             else f"❌ HAR validation failed: {msg}"),
                            done=True,
                            error=error_msg,
                        ))
                        proc.terminate()
                        return

            # Auto-complete: write marker after delay (browser should be open by then)
            if config.auto_complete and not auto_marker_written:
                elapsed = time.time() - overall_start
                if elapsed >= auto_delay and not (run_dir / "recording_done.marker").exists():
                    marker_path = run_dir / "recording_done.marker"
                    marker_path.write_text(time.strftime("%Y-%m-%dT%H:%M:%SZ"), encoding="utf-8")
                    auto_marker_written = True
                    logger.info("Auto-complete marker written after %.0fs: %s", elapsed, marker_path)
                    progress_cb(ProgressEvent(
                        Phase.RECORD,
                        "📷 Auto-complete: recording marker written",
                        done=False,
                    ))

            # Phase timeout checks based on artifacts
            if "demo.json" not in watcher.found and "recording.har" not in watcher.found:
                if "record" not in phase_timers:
                    phase_timers["record"] = time.time()
                elif time.time() - phase_timers["record"] > config.phase_timeout:
                    error_msg = "RECORD phase timeout exceeded"
                    progress_cb(ProgressEvent(
                        Phase.FAILED, error_msg, done=True, error=error_msg))
                    proc.terminate()
                    return

            if "demo.json" in watcher.found and "state_map.json" not in watcher.found:
                if "analyze" not in phase_timers:
                    phase_timers["analyze"] = time.time()
                elif time.time() - phase_timers["analyze"] > config.phase_timeout:
                    error_msg = (f'ANALYZE phase timeout exceeded; state map rejected: {pending_state_map_error}'
                                 if pending_state_map_error else 'ANALYZE phase timeout exceeded')
                    progress_cb(ProgressEvent(
                        Phase.FAILED, error_msg, done=True, error=error_msg))
                    proc.terminate()
                    return

            if "state_map.json" in watcher.found and "mutations" not in watcher.found:
                if "mutate" not in phase_timers:
                    phase_timers["mutate"] = time.time()
                elif time.time() - phase_timers["mutate"] > config.phase_timeout:
                    error_msg = "MUTATE phase timeout exceeded"
                    progress_cb(ProgressEvent(
                        Phase.FAILED, error_msg, done=True, error=error_msg))
                    proc.terminate()
                    return

            if "mutations" in watcher.found and "findings.json" not in watcher.found:
                if "probe" not in phase_timers:
                    phase_timers["probe"] = time.time()
                elif time.time() - phase_timers["probe"] > config.phase_timeout:
                    error_msg = "PROBE phase timeout exceeded"
                    progress_cb(ProgressEvent(
                        Phase.FAILED, error_msg, done=True, error=error_msg))
                    proc.terminate()
                    return

            # Overall timeout
            if time.time() - overall_start > config.overall_timeout:
                error_msg = "Overall assessment timeout exceeded"
                progress_cb(ProgressEvent(
                    Phase.FAILED, error_msg, done=True, error=error_msg))
                proc.terminate()
                return

            await asyncio.sleep(1)

    # Run readers and watcher concurrently
    await asyncio.gather(read_stdout(), read_stderr(), watch_artifacts())

    # Process completed
    await proc.wait()
    exit_code = proc.returncode
    total_time = time.time() - overall_start

    # Log the last result event for debugging
    for line in stdout_events:
        try:
            obj = json.loads(line.strip())
            if obj.get("type") == "result":
                logger.info("Crew result: turns=%s error=%s api=%s",
                    obj.get("num_turns"), obj.get("is_error"), obj.get("api_error_status"))
                r = obj.get("result", "")[:500]
                logger.info("Crew result text: %s", r)
        except Exception:
            pass

    if error_msg:
        pass  # Already reported
    elif exit_code != 0:
        error_msg = f"Claude Code exited with code {exit_code}"
        progress_cb(ProgressEvent(
            Phase.FAILED,
            f"❌ Crew subprocess exited with code {exit_code}",
            done=True,
            error=error_msg,
        ))

    # A fresh read-only Analyst call receives concrete validator feedback.
    # This gate finishes before any backend-owned probe can be executed.
    if not error_msg:
        try:
            await correct_state_map(config, run_dir, flow_name, env,
                lambda message: progress_cb(ProgressEvent(Phase.ANALYZE, message, done=False)),
                expected_hashes=recording_hashes,
                deadline=time.monotonic() + max(0, config.overall_timeout - (time.time() - overall_start)))
        except (UIProvenanceError, OSError, ValueError) as exc:
            error_msg = f'State-map validation failed: {exc}'
    total_time = time.time() - overall_start

    # Final artifact detection
    try:
        final_new = [] if error_msg else watcher.check()
    except (UIProvenanceError, ProbeContractError) as exc:
        final_new = []
        if not error_msg:
            label = ('State-map validation' if isinstance(exc, UIProvenanceError)
                     else 'Probe contract validation')
            error_msg = f'{label} failed: {exc}'
            progress_cb(ProgressEvent(Phase.FAILED, error_msg, done=True, error=error_msg))
    for name, path in final_new:
        if cross_flow:
            for event in _cross_flow_artifact_events(name):
                progress_cb(event)
        else:
            progress_cb(ProgressEvent(
                _artifact_to_phase(name),
                f"📄 Artifact written: {name}",
                done=False,
            ))

    if not error_msg and reanalysis:
        from backend.runtime.reanalyze import validate_reanalysis_copy
        try:
            validate_reanalysis_copy(
                run_dir, flow_name, config.reanalysis_inputs, root=config.run_dir)
        except (OSError, ValueError) as exc:
            error_msg = f'Reanalysis evidence validation failed: {exc}'
            progress_cb(ProgressEvent(
                Phase.FAILED, error_msg, done=True, error=error_msg))

    # Cross-flow hypotheses are gated before any backend-owned script executes.
    if not error_msg and config.cross_flow_inputs is not None:
        from backend.runtime.application_model import validate_cross_flow_candidates
        try:
            candidates_path = run_dir / 'flows' / flow_name / 'cross_flow_candidates.json'
            from backend.runtime.candidate_correction import correct_candidates
            candidate_count = await correct_candidates(config, run_dir, flow_name, env,
                lambda message: progress_cb(ProgressEvent(Phase.MUTATE, message, done=False)))
            progress_cb(ProgressEvent(
                Phase.MUTATE,
                f'Conflict hypotheses: {candidate_count} validated candidates',
                done=True,
                technical_detail='cross_flow_candidates.json validated',
            ))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            error_msg = f'Cross-flow analysis incomplete: candidate validation failed: {exc}'
            progress_cb(ProgressEvent(
                Phase.FAILED, error_msg, done=True, error=error_msg,
                technical_detail='Candidate artifact: cross_flow_candidates.json',
            ))

    # Versioned coverage inputs run only after artifact validation; no preset verdicts.
    execution_summary_detail = None
    if not error_msg:
        try:
            execution_progress = None
            validate_final_state_map(run_dir, flow_name)
            from backend.runtime.coverage_plans import prepare_coverage_probes
            prepare_coverage_probes(run_dir, flow_name, config.run_dir)
            if cross_flow:
                script_count = len(list((run_dir / 'mutations' / flow_name).glob('*.py')))
                script_count += len(list((run_dir / 'verification_probes' / flow_name).glob('*.py')))
                script_count += len(list((run_dir / 'backend_coverage' / flow_name).glob('*.py')))
                from backend.runtime.candidate_correction import load_gate
                gate = load_gate(run_dir, config.run_dir)
                if gate is not None:
                    script_count = len(gate['allowed_scripts']) + len(list(
                        (run_dir / 'backend_coverage' / flow_name).glob('*.py')))
                progress_cb(ProgressEvent(
                    Phase.PROBE,
                    f'Preparing {script_count} executable probes',
                    done=False,
                    technical_detail=f'{script_count} executable script(s) discovered',
                ))

                def execution_progress(detail):
                    nonlocal execution_summary_detail
                    if detail.get('stage') == 'summary':
                        execution_summary_detail = detail
                        return
                    progress_cb(ProgressEvent(
                        Phase.PROBE,
                        detail['message'],
                        done=False,
                        technical_detail=detail.get('technical_detail'),
                    ))

            await execute_validated_probes(run_dir, flow_name, config.run_dir,
                lambda message: progress_cb(ProgressEvent(Phase.PROBE, message, done=False)),
                execution_progress=execution_progress)
            if not cross_flow:
                progress_cb(ProgressEvent(Phase.PROBE, 'Execution receipts saved', done=True))
                progress_cb(ProgressEvent(Phase.REPORT, 'Checking report provenance and invariant evidence', done=False))
        except UIProvenanceError as exc:
            error_msg = f'State-map validation failed: {exc}'
            progress_cb(ProgressEvent(Phase.FAILED, error_msg, done=True, error=error_msg))
        except Exception as exc:
            error_msg = f'Backend probe execution failed: {exc}'
            progress_cb(ProgressEvent(Phase.FAILED, error_msg, done=True, error=error_msg))

    # Separate opt-in state-lock coverage. Versioned financial coverage above
    # supplements, rather than replaces, the crew's discovery. No coverage hook
    # guarantees a verdict. ALLOW_STATE_LOCK_PROBE enables another backend probe
    # after the crew has fully exited
    # (so it won't race the Prober) and, on a confirmed violation, authors/merges
    # the finding into reports/<flow>/findings.json. Running it BEFORE the "no
    # report produced" check also rescues a run that died mid-probe. Best-effort
    # and never raises.
    if (not error_msg and
            os.environ.get("ALLOW_STATE_LOCK_PROBE", "").strip().lower() in ("1", "true", "yes", "on")):
        try:
            from backend.runtime.probe_executor import execute_state_lock
            fid = await execute_state_lock(run_dir, flow_name, config.run_dir, config.target_url)
            if fid:
                progress_cb(ProgressEvent(
                    Phase.PROBE,
                    f"🔒 State-lock probe captured evidence ({fid}); awaiting provenance verification",
                    done=True,
                ))
        except Exception as e:  # defensive — the runner is already guarded, belt-and-braces
            logger.warning("state_lock_probe hook failed (non-fatal): %s", e)
    elif not error_msg:
        logger.info("state_lock_probe disabled (ALLOW_STATE_LOCK_PROBE not set) — "
                    "using generated and applicable versioned coverage probes")

    if cross_flow and not error_msg and execution_summary_detail is not None:
        progress_cb(ProgressEvent(
            Phase.PROBE,
            execution_summary_detail['message'],
            done=True,
            technical_detail=execution_summary_detail.get('technical_detail'),
        ))
        progress_cb(ProgressEvent(
            Phase.REPORT,
            'Verifying signed evidence and invariants',
            done=False,
        ))

    # Artifacts are written in-place under runs/<flow>/ (single source of truth);
    # the portal reads them from there. No copy-out / reconciliation needed.
    paths = {}

    # A run only counts as complete if it produced a final report. If the crew
    # exited cleanly but never reached REPORT (e.g. it ended its turn before
    # holding the recording wait, so the browser closed and the recording was
    # lost), mark it a failure instead of a misleading "complete".
    findings_path = run_dir / "reports" / flow_name / "findings.json"
    if not error_msg and config.cross_flow_inputs is not None:
        for artifact_name in ('application_state_map.json',):
            artifact_path = run_dir / 'flows' / flow_name / artifact_name
            try:
                model_data = json.loads(artifact_path.read_text(encoding='utf-8'))
                if not isinstance(model_data, (dict, list)):
                    raise ValueError('Expected an object or array')
            except (OSError, ValueError):
                error_msg = 'Cross-flow analysis incomplete: application state model is missing or invalid'
                progress_cb(ProgressEvent(
                    Phase.FAILED, error_msg, done=True, error=error_msg,
                    technical_detail=f'Artifact: {artifact_name}',
                ))
    if not error_msg and findings_path.exists():
        try:
            normalized = load_report(findings_path)
            if cross_flow:
                progress_cb(ProgressEvent(
                    Phase.REPORT,
                    'Signed evidence and invariants verified',
                    done=False,
                ))
                progress_cb(ProgressEvent(
                    Phase.REPORT,
                    'Building normalized final report',
                    done=False,
                ))
            staged = findings_path.with_suffix('.normalized.tmp')
            staged.write_text(json.dumps(normalized, indent=2), encoding="utf-8")
            staged.replace(findings_path)
            if cross_flow:
                progress_cb(ProgressEvent(
                    Phase.REPORT,
                    'Normalized final report built',
                    done=True,
                ))
        except (OSError, ValueError, TypeError) as exc:
            logger.warning('Report evidence reconciliation failed: %s', type(exc).__name__)
            error_msg = 'Report evidence reconciliation failed; inspect run artifacts.'
    if not error_msg and not findings_path.exists():
        if cross_flow:
            error_msg = 'No cross-flow report was produced.'
        else:
            error_msg = (
                "No report was produced — the recording window closed before the "
                "recording finished. Start the assessment again and click "
                "'Finish recording' once you've completed the flow."
            )

    # Emit completion (skipped if already declared when remediation.md landed)
    if completion_emitted:
        pass
    elif not error_msg:
        if cross_flow:
            summary = normalized.get('summary', {})
            duration = time.monotonic() - started_at
            progress_cb(ProgressEvent(
                Phase.COMPLETE,
                _cross_flow_final_message(summary, duration),
                done=True,
            ))
        else:
            progress_cb(ProgressEvent(Phase.COMPLETE,
                f"✅ Assessment complete ({total_time:.0f}s)", done=True))
    else:
        progress_cb(ProgressEvent(Phase.FAILED,
            f"❌ Assessment failed: {error_msg}", done=True, error=error_msg))

    # Cleanup temp home
    try:
        shutil.rmtree(temp_home, ignore_errors=True)
    except OSError:
        pass

    return {
        "exit_code": exit_code if exit_code is not None else -1,
        "total_time": total_time,
        "artifacts": paths,
        "run_dir": str(run_dir),
        "error": error_msg,
    }


def _artifact_to_phase(name: str) -> Phase:
    mapping = {
        "demo.json": Phase.RECORD,
        "recording.har": Phase.RECORD,
        "state_map.json": Phase.ANALYZE,
        "mutations": Phase.MUTATE,
        "findings.json": Phase.PROBE,
        "remediation.md": Phase.REPORT,
    }
    return mapping.get(name, Phase.REPORT)


def _cross_flow_final_message(summary: dict, duration: float) -> str:
    """Keep findings, probes, receipt outcomes, and presentation units distinct."""
    finding_count = int(summary.get('finding_count', summary.get('reported_findings', 0)) or 0)
    planned = int(summary.get('planned_executions', 0) or 0)
    completed = int(summary.get('completed_executions', 0) or 0)
    attempts = int(summary.get('execution_attempts', 0) or 0)
    process_errors = int(summary.get('process_errors', 0) or 0)
    contract_errors = int(summary.get('evidence_contract_errors', 0) or 0)
    trace_mismatches = int(summary.get('trace_mismatches', 0) or 0)
    result_counts = summary.get('execution_result_counts') or {}
    partial = int(summary.get('partial_coverage', 0) or 0)
    excluded = int(summary.get('excluded_setup_executions', 0) or 0)
    deduplicated = int(summary.get('deduplicated_primary_chains', 0) or 0)
    message = (
        f"Final security findings ({finding_count} normalized): "
        f"{int(summary.get('confirmed', 0) or 0)} confirmed, "
        f"{int(summary.get('needs_review', 0) or 0)} need review, "
        f"{int(summary.get('not_reproduced', 0) or 0)} not reproduced, "
        f"{int(summary.get('not_executed', 0) or 0)} coverage gaps, "
        f"{int(summary.get('errors', 0) or 0)} check errors. "
        f"Partial coverage: {partial} supplementary scenarios. "
        f"Excluded setup-path executions: {excluded}. "
        f"Probes: {completed}/{planned} authenticated terminal receipts from "
        f"{attempts} attempts; {process_errors} process errors, "
        f"{contract_errors} evidence-contract errors, {trace_mismatches} trace mismatches. "
        f"Primary execution results: {int(result_counts.get('needs_review', 0) or 0)} need review, "
        f"{int(result_counts.get('not_reproduced', 0) or 0)} not reproduced. "
        f"Deduplicated primary chains: {deduplicated}."
    )
    if 'unique_vulnerabilities' in summary:
        message += (f" Unique vulnerabilities: {int(summary['unique_vulnerabilities'])}; "
                    f"findings with incomplete scenario coverage: {int(summary.get('coverage_gaps', 0))}.")
    return f"{message} Total duration: {duration:.1f}s."


def _cross_flow_artifact_events(name: str) -> list[ProgressEvent]:
    """Translate agent artifacts into milestones without endorsing draft output."""
    if name in ('demo.json', 'recording.har', 'mutations'):
        return []
    if name == 'state_map.json':
        return [
            ProgressEvent(
                Phase.ANALYZE,
                'Application state model built',
                done=True,
                technical_detail='Artifact validated: state_map.json',
            ),
            ProgressEvent(Phase.MUTATE, 'Identifying cross-flow conflicts', done=False),
        ]
    if name == 'findings.json':
        return [ProgressEvent(
            Phase.MUTATE,
            'Draft findings prepared; execution has not verified them',
            done=False,
            technical_detail='Agent draft written: findings.json',
        )]
    if name == 'remediation.md':
        return [ProgressEvent(
            Phase.MUTATE,
            'Draft remediation prepared; final verdicts are pending',
            done=False,
            technical_detail='Agent draft written: remediation.md',
        )]
    return []
