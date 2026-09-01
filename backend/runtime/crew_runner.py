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
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from backend.runtime.orchestrator import Phase, ProgressEvent

logger = logging.getLogger("flowbusters.crew_runner")

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


# ── Working Directory Preparation ─────────────────────────────────────────────

def prepare_run_dir(config: CrewConfig, flow_name: str) -> Path:
    """Copy vendored crew files into runs/<flow-name>/ and patch config.json."""
    base = Path(config.run_dir) / "runs" / flow_name
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
        cfg = json.loads(config_json.read_text())
        cfg["teamRoot"] = str(base)
        config_json.write_text(json.dumps(cfg, indent=2))

    # Copy scope.json from run_dir root
    scope_src = Path(config.run_dir) / "scope.json"
    if scope_src.exists():
        shutil.copy2(scope_src, base / "scope.json")

    # Create artifact directories
    (base / "flows" / flow_name).mkdir(parents=True, exist_ok=True)
    (base / "mutations" / flow_name).mkdir(parents=True, exist_ok=True)
    (base / "reports" / flow_name).mkdir(parents=True, exist_ok=True)

    logger.info("Run directory prepared: %s", base)
    return base


# ── System Prompt Assembly ────────────────────────────────────────────────────

def build_system_prompt(run_dir: Path, flow_name: str) -> str:
    """Assemble the full system prompt from vendored charters + skills."""
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

    # Captain charter
    captain = crew / "agents" / "captain" / "charter.md"
    if captain.exists():
        parts.append(captain.read_text())

    # Recorder charter (with RECORD phase note)
    recorder = crew / "agents" / "recorder" / "charter.md"
    if recorder.exists():
        parts.append("\n\n---\n\n" + recorder.read_text())

    # Analyst charter
    analyst = crew / "agents" / "analyst" / "charter.md"
    if analyst.exists():
        parts.append("\n\n---\n\n" + analyst.read_text())

    # Saboteur charter
    saboteur = crew / "agents" / "saboteur" / "charter.md"
    if saboteur.exists():
        parts.append("\n\n---\n\n" + saboteur.read_text())

    # Prober charter
    prober = crew / "agents" / "prober" / "charter.md"
    if prober.exists():
        parts.append("\n\n---\n\n" + prober.read_text())

    # Routing rules
    routing = crew / "routing.md"
    if routing.exists():
        parts.append("\n\n---\n\n" + routing.read_text())

    # Skill definitions
    skills_dir = crew / "skills"
    skill_names = ["record-flow", "analyze-har", "mutate-flow", "probe-flow"]
    parts.append("\n\n---\n\n# SKILL DEFINITIONS\n\n")
    for skill in skill_names:
        skill_file = skills_dir / skill / "SKILL.md"
        if skill_file.exists():
            parts.append(skill_file.read_text() + "\n\n")

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
        "   c. List the captured API requests:\n"
        "      browser_network_requests(static=false, filter=\"/api/.*\")\n"
        "      This returns a numbered list. Note the indexes.\n"
        "   d. For EACH index N in that list, call:\n"
        "      browser_network_request(index=N)\n"
        "      Collect every request's url, method, status, status_text, request_headers,\n"
        "      request_body, response_headers, response_body, response_mime, started_at,\n"
        "      duration_ms. Then write flows/" + flow_name + "/har_data/raw_network.json as:\n"
        "        {\"entries\": [ <one object per request, in order> ]}\n"
        "      (This is the structured dump the HAR builder needs — a plain list of URLs is\n"
        "      NOT sufficient; each entry must carry the headers and bodies.)\n"
        "\n"
        ">>> STOP — CLOSE THE BROWSER NOW <<<\n"
        "   The last browser call you need is done. IMMEDIATELY call browser_close and confirm\n"
        "   the call returned before you do ANYTHING else (no synthesize, no demo.json, no\n"
        "   announce). The user is watching a stuck Chrome window — leaving it open breaks\n"
        "   their trust in the tool. If browser_close errors, retry it once. Only proceed to\n"
        "   the next step after the browser is closed.\n"
        "\n"
        "   Then continue on disk (browser no longer needed):\n"
        "   e. Run: python3 crew/scripts/synthesize_har.py flows/" + flow_name + "/har_data/raw_network.json flows/" + flow_name + "/recording.har\n"
        "   f. Verify synthesize_har.py exited 0 and recording.har exists\n"
        "   g. Write flows/" + flow_name + "/demo.json with interaction trace.\n"
        "   h. Validate recording.har: it MUST have {log: {version, creator, entries}}.\n"
        "      If invalid, report the error and do NOT proceed to Phase 2.\n"
        "4. Announce RECORD phase complete with both file paths and counts.\n\n"
        "IMPORTANT: If the target URL has SSL certificate errors, use browser_navigate with\n"
        "ignoreHttpsErrors=true or continue anyway. Do NOT ask the user for permission —\n"
        "proceed with the assessment. This is a test environment.\n\n"
    )

    # Prober phase instructions for Python path
    parts.append(
        "# PROBE PHASE — SCRIPT EXECUTION\n\n"
        "Execute each mutation script with:\n"
        "    python3 <script_path>.py <target_url>\n"
        "Use a 30-second timeout per script. Parse the JSON line each script prints.\n"
        "Classify: BUG_FOUND (2xx when rejection expected), REJECTED (4xx/5xx), ERROR.\n\n"
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
                self.found[name] = path
                new.append((name, path))

        # Check mutations (≥3 .py files)
        if "mutations" not in self.found:
            pys = sorted(self.mutations.glob("*.py")) if self.mutations.exists() else []
            if len(pys) >= 3:
                self.found["mutations"] = self.mutations
                new.append(("mutations", self.mutations))

        return new


# ── HAR Validator ──────────────────────────────────────────────────────────────

def validate_har(path: Path) -> tuple[bool, str]:
    """Validate a HAR file is well-formed HAR 1.2."""
    try:
        data = json.loads(path.read_text())
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


# ── Main Runner ───────────────────────────────────────────────────────────────

async def run_crew(
    config: CrewConfig,
    progress_cb: Callable[[ProgressEvent], None],
) -> dict:
    """
    Spawn Claude Code subprocess, monitor it, emit progress, enforce timeouts.

    Returns dict with artifact paths and findings.
    """
    flow_name = config.flow_name

    # Prepare working directory
    run_dir = prepare_run_dir(config, flow_name)

    # Build system prompt
    system_prompt = build_system_prompt(run_dir, flow_name)
    prompt_path = run_dir / "_system_prompt.txt"
    prompt_path.write_text(system_prompt)

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
        "--model", config.model,
        "--effort", effort,
        "--system-prompt-file", prompt_path.name,
        "--dangerously-skip-permissions",
        initial_message,
    ]

    progress_cb(ProgressEvent(Phase.RECORD, "⏳ Spawning Claude Code crew...", done=False))

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
    logger.info("Claude Code subprocess started (pid=%d)", proc.pid)
    progress_cb(ProgressEvent(Phase.RECORD, "✅ Crew running (pid %d)" % proc.pid, done=False))

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
    marker_announced = False
    browser_closed_announced = False
    # Set the moment remediation.md lands: the report is DONE then, even if the
    # crew is still printing a final summary. Emit COMPLETE immediately so the
    # "View report" button appears at the end of the Report step instead of
    # waiting for the crew process to exit.
    completion_emitted = False

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
            stdout_events.append(text)
            parser.feed(text)

            # Emit "opening browser" SSE event when first browser_* tool call detected
            if parser.has_first_browser_call() and not parser.first_browser_call_emitted_was_signaled:
                parser.first_browser_call_emitted_was_signaled = True
                progress_cb(ProgressEvent(
                    Phase.RECORD,
                    "🌐 Browser opening — Chromium launching now...",
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
        nonlocal error_msg, auto_marker_written, browser_seen, window_dead_since, marker_announced, browser_closed_announced, completion_emitted
        while proc.returncode is None:
            new_artifacts = watcher.check()

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
                    "state_map.json", "mutations", "findings.json", "remediation.md",
                )
                progress_cb(ProgressEvent(
                    artifact_phase,
                    f"📄 Artifact written: {name}",
                    done=artifact_done,
                ))
                logger.info("Artifact detected: %s", path)

                # The final report is complete the instant remediation.md exists —
                # declare it now rather than waiting for the crew to exit.
                if name == "remediation.md" and not completion_emitted:
                    completion_emitted = True
                    progress_cb(ProgressEvent(
                        Phase.COMPLETE,
                        f"✅ Assessment complete ({time.time() - overall_start:.0f}s)",
                        done=True,
                    ))

                # Validate HAR immediately
                if name == "recording.har":
                    ok, msg = validate_har(path)
                    if not ok:
                        error_msg = f"Invalid HAR file: {msg}"
                        progress_cb(ProgressEvent(
                            Phase.FAILED,
                            f"❌ HAR validation failed: {msg}",
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
                    marker_path.write_text(time.strftime("%Y-%m-%dT%H:%M:%SZ"))
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
                    error_msg = "ANALYZE phase timeout exceeded"
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

    # Final artifact detection
    final_new = watcher.check()
    for name, path in final_new:
        progress_cb(ProgressEvent(
            _artifact_to_phase(name),
            f"📄 Artifact written: {name}",
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
    if not error_msg and not findings_path.exists():
        error_msg = (
            "No report was produced — the recording window closed before the "
            "recording finished. Start the assessment again and click "
            "'Finish recording' once you've completed the flow."
        )

    # Emit completion (skipped if already declared when remediation.md landed)
    if completion_emitted:
        pass
    elif not error_msg:
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
