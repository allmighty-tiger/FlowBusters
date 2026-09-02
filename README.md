<p align="center">
  <img src="assets/flowbusters-banner.svg" alt="FlowBusters — The lock was only painted" />
</p>

# FlowBusters

**AI-assisted business logic testing from one recorded browser workflow.**

FlowBusters records a legitimate workflow in a real browser, analyzes the
captured HTTP traffic, generates adversarial business-logic tests, executes
them against an authorized target, and produces evidence-backed findings.

> [!IMPORTANT]
> ### Experimental MVP / Proof of Concept
> FlowBusters is an active research prototype. It is **not production-ready**
> and is not a hardened or sandboxed security platform.
>
> **Authorized testing only. Use only against non-production applications you
> own or have explicit permission to test.**

## The problem

Traditional scanners are good at testing individual endpoints and known
vulnerability patterns. Business-logic flaws often appear only when someone
breaks the sequence, roles, or assumptions built into a workflow.

FlowBusters explores cases such as skipping required steps, replaying completed
actions, swapping roles, tampering with prices or state, and accessing workflow
endpoints directly.

## How it works

**Record → Analyze → Mutate → Probe → Report**

| Phase | What happens | Output |
|:---:|:---|:---|
| **Record** | You perform the legitimate workflow in a headed Playwright browser | `demo.json`, `recording.har` |
| **Analyze** | The workflow, roles, transitions, and critical endpoints are inferred | `state_map.json` |
| **Mutate** | Adversarial tests are generated for the observed business logic | `mutations/*.py` |
| **Probe** | Generated HTTP tests are executed and classified | `findings.json` |
| **Report** | Evidence, CWE references, and remediation are assembled | `remediation.md` |

<p align="center">
  <img src="assets/progress.png" alt="FlowBusters assessment progress page" width="480" />
</p>

## What it includes

| Component | Responsibility |
|:---|:---|
| React portal | Starts assessments, streams progress, and renders reports |
| FastAPI backend | Coordinates phases and manages artifacts |
| Playwright MCP | Records browser interactions and network traffic |
| Claude Code crew | Analyzes the workflow and proposes tests |
| HTTP probes | Execute generated tests outside the browser |
| CWE-mapped report | Presents evidence and remediation for findings |

The named Captain, Recorder, Analyst, Saboteur, and Prober are specialized
roles inside one Claude Code session. They are not five independent LLM
processes.

## Attack vectors

| Attack | Description |
|:---|:---|
| `SKIP_STEP` | Bypass required workflow prerequisites |
| `ROLE_SWAP` | Use one role's session against another role's endpoint |
| `DATA_TAMPER` | Change identifiers, amounts, statuses, or other values |
| `MASS_ASSIGNMENT` | Submit privileged fields hidden by the UI |
| `PRICING_TAMPER` | Submit manipulated prices or discounts |
| `DOUBLE_SPEND` | Repeat an operation sequentially or concurrently |
| `REPLAY_ATTACK` | Reuse a request after it should be invalid |
| `FORCED_BROWSING` | Call an endpoint without following the UI flow |

## Quickstart

### Prerequisites

- Python 3
- Node.js and npm
- Google Chrome
- Claude Code CLI
- Anthropic API key

### Configure

```bash
git clone https://github.com/allmighty-tiger/FlowBusters.git
cd FlowBusters
cp .env.example .env
npm install
cp mcp.json.example mcp.json
```

Set `ANTHROPIC_API_KEY` in `.env`.

Update the `command` in `mcp.json` to point to the local `playwright-mcp`
executable. On Windows this may be:

```text
<repo-root>\node_modules\.bin\playwright-mcp.cmd
```

### Start the backend

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
pip install python-dotenv
uvicorn backend.api.main:app --port 8000
```

On Windows PowerShell, activate the environment with:

```powershell
.\.venv\Scripts\Activate.ps1
```

Do not use `--reload` during an assessment; it can terminate the active crew.

### Start the frontend

In a second terminal:

```bash
cd frontend
npm install
npm run dev
```

Open:

```text
http://localhost:3000
```

Enter an authorized target URL, start an assessment, complete the workflow in
Chromium, and select **Finish recording**.

## Configuration

| Variable | Purpose | Default |
|:---|:---|:---|
| `ANTHROPIC_API_KEY` | Anthropic API credentials | none |
| `ANTHROPIC_MODEL` | Model used by the crew | `claude-sonnet-5` |
| `CREW_EFFORT` | Claude Code reasoning effort | `medium` |
| `ARTIFACTS_DIR` | Assessment artifact root | `.` |
| `CLAUDE_CODE_BIN` | Claude Code executable | `claude` |
| `MCP_CONFIG_PATH` | Playwright MCP configuration | `./mcp.json` |
| `CREW_DIR` | Crew definitions | `./crew` |

## Assessment artifacts

```text
runs/{flow-name}/
├── flows/
│   ├── demo.json
│   ├── recording.har
│   └── state_map.json
├── mutations/
└── reports/
    ├── findings.json
    └── remediation.md
```

`BUG_FOUND` means the target accepted an operation that should have been
rejected. `REJECTED` means the attempted operation was blocked. `ERROR` means
the probe could not execute or could not be evaluated.

A successful HTTP response alone does not prove a vulnerability. Findings
require human review.

## Safety

FlowBusters executes AI-generated Python probes against live targets. Claude
Code currently runs with `--dangerously-skip-permissions`, and generated code is
not contained by a hard operating-system sandbox.

Use a disposable, isolated environment; test only non-production systems; use
synthetic data and least-privileged accounts; inspect generated probes; and
treat HAR files, tokens, scripts, and reports as sensitive.

`scope.json` is a workflow guardrail, not a security boundary. Enforce scope
independently with network controls, dedicated credentials, and target-side
access controls.

Read [`SECURITY.md`](SECURITY.md) before running an assessment.

## Limitations

- Results depend on the completeness of the recorded workflow.
- Authentication and complex multi-user state may require manual handling.
- Generated tests can contain false positives or invalid assumptions.
- The MVP is not hardened for production deployment.
- FlowBusters does not replace manual penetration testing, code review, threat
  modeling, or conventional security scanning.

## Responsible Use

> [!WARNING]
> **FlowBusters is an offensive-security research project intended exclusively
> for authorized testing.**
>
> You are responsible for obtaining permission, defining scope, protecting
> captured data, reviewing generated probes, and complying with all applicable
> laws and organizational policies.

The presence of technical access, a reachable endpoint, or a permissive
`scope.json` does not constitute authorization. You remain responsible for
every request executed through FlowBusters and every effect it has on the
target.

<p align="center">
  <sub>FlowBusters — record a workflow, break its rules, get the fix.</sub>
</p>
