# FlowBusters

A zero-config, multi-agent system that records business workflows, analyzes them for state transitions, generates adversarial mutation scripts, and probes live environments for business logic flaws.

**Runtime:** VS Code Copilot with the Squad agent framework. No external LLM proxies or Python environments required beyond what's needed to execute the generated probe scripts.

---

## How It Works

FlowBusters runs a strictly sequential 4-phase pipeline. Each phase must pass a verification gate before the next phase begins. The Captain agent orchestrates the full run and manages artifact handoff between phases.

```
RECORD → ANALYZE → MUTATE → PROBE
```

| Phase | Agent | Input | Output |
|-------|-------|-------|--------|
| 1. RECORD | Recorder | Target URL, flow-name | `flows/{flow-name}/demo.json`, `flows/{flow-name}/recording.har` |
| 2. ANALYZE | Analyst | `flows/{flow-name}/demo.json`, `flows/{flow-name}/recording.har` | `flows/{flow-name}/state_map.json` |
| 3. MUTATE | Saboteur | `flows/{flow-name}/state_map.json` | `mutations/{flow-name}/*.py` (3–5 scripts) |
| 4. PROBE | Prober | `mutations/{flow-name}/*.json` | `reports/{flow-name}/findings.json`, `reports/{flow-name}/remediation.md` |

---

## Quickstart

Invoke the Captain with a target URL:

```
Captain, run FlowBusters against https://your-app.example.com/workflow
```

Captain will orchestrate each phase in order. During Phase 1, a headed browser will open — complete your full workflow (login + business flow) and signal when done. FlowBusters will handle the rest.

### Recording Multiple Flows

To test multiple scenarios (happy path, error recovery, etc.) without file conflicts, use the `--flow-name` parameter:

```
Captain, run FlowBusters against https://your-app.example.com/workflow --flow-name happy-path
Captain, run FlowBusters against https://your-app.example.com/workflow --flow-name error-recovery
```

Each flow is recorded to its own directory tree:

```
flows/
  happy-path/
    demo.json
    recording.har
    state_map.json
  error-recovery/
    demo.json
    recording.har
    state_map.json

mutations/
  happy-path/*.json
  error-recovery/*.json

reports/
  happy-path/
    findings.json
    remediation.md
  error-recovery/
    findings.json
    remediation.md
```

**Note:** If `--flow-name` is omitted, outputs default to `flows/default/`, `mutations/default/`, `reports/default/`.

Flow names must be kebab-case (lowercase alphanumeric + hyphens). Recommendations:
- `happy-path` — standard/successful user workflows
- `error-recovery` — error handling and edge cases
- `admin-flow` — workflows for different roles
- `edge-case-{scenario}` — specific edge cases

### Running Individual Phases

You can also invoke phases independently if artifacts from prior phases already exist:

| Command | Phase |
|---------|-------|
| `Record the flow` | Phase 1 only |
| `Analyze the recording` | Phase 2 only (requires Phase 1 artifacts) |
| `Generate mutations` | Phase 3 only (requires Phase 2 artifacts) |
| `Run probes` | Phase 4 only (requires Phase 3 artifacts) |
| `Status` | Report current phase state |

---

## Mutation Types

Saboteur generates scripts targeting these attack vectors:

| Type | Description |
|------|-------------|
| `SKIP_STEP` | Call a late-stage endpoint without completing prerequisites |
| `ROLE_SWAP` | Use Role A's session to access Role B's endpoints |
| `DATA_TAMPER` | Modify request body values (IDs, amounts, statuses) to unauthorized values |
| `REPLAY_ATTACK` | Replay a captured request after state should have invalidated it |
| `FORCED_BROWSING` | Access endpoints directly without going through the expected UI flow |

---

## Artifact Structure

```
flows/
  {flow-name}/
    demo.json          # DOM interaction trace from Phase 1
    recording.har      # Network traffic capture (HAR 1.2) from Phase 1
    state_map.json     # State transitions, roles, critical endpoints from Phase 2

mutations/
  {flow-name}/
    01_*.py            # Adversarial probe scripts from Phase 3
    02_*.py
    ...

reports/
  {flow-name}/
    findings.json      # Outcome classification for each probe script
    remediation.md     # CWE-mapped remediation guidance (only if bugs found)
```

**Default:** If `--flow-name` is omitted, `{flow-name}` defaults to `"default"`.

### `flows/state_map.json` Schema

```json
{
  "target_url": "https://...",
  "recorded_at": "ISO-8601",
  "transitions": [
    {
      "name": "descriptive_action_name",
      "method": "POST|PUT|DELETE|PATCH",
      "url": "https://...",
      "headers": { "Cookie": "...", "Authorization": "..." },
      "body_keys": ["field1", "field2"],
      "response_status": 200,
      "criticality": "HIGH|MED|LOW",
      "depends_on": ["previous_transition_name"]
    }
  ],
  "roles": [
    {
      "name": "role_name",
      "cookies": [{ "name": "...", "value": "...", "domain": "...", "path": "/" }],
      "headers": { "Authorization": "Bearer ..." }
    }
  ],
  "critical_endpoints": [
    {
      "url": "https://...",
      "method": "POST",
      "why": "brief explanation",
      "attack_surface": ["SKIP_STEP", "ROLE_SWAP", "DATA_TAMPER"]
    }
  ]
}
```

### `reports/findings.json` Schema

```json
{
  "run_timestamp": "ISO-8601",
  "target_url": "https://...",
  "flow_name": "default",
  "total_scripts": 5,
  "results": [
    {
      "script": "01_skip_step_approval.py",
      "mutation_type": "SKIP_STEP",
      "outcome": "BUG_FOUND|REJECTED|ERROR",
      "status_code": 200,
      "url_tested": "https://...",
      "response_snippet": "...",
      "error_message": null
    }
  ],
  "summary": {
    "bugs_found": 2,
    "rejected": 2,
    "errors": 1
  }
}
```

---

## Probe Outcome Classification

| Outcome | Condition |
|---------|-----------|
| `BUG_FOUND` | Server returned 2xx when it should have rejected the request |
| `REJECTED` | Server correctly rejected the malicious request (4xx/5xx) |
| `ERROR` | Script failed (timeout, import error, malformed output) |

When a `BUG_FOUND` outcome is recorded, Prober generates `reports/remediation.md` with CWE ID mapping, severity rating, and specific fix recommendations.

---

## Tools Required

- **Playwright MCP** — headed browser automation for Phase 1 capture
- **Terminal (bash)** — script execution and file validation
- **VS Code Copilot** — LLM reasoning for analysis, mutation generation, and remediation authoring
- **Python 3** — runtime for executing generated probe scripts (`playwright`, `httpx`)

---

## Safety

- **Scope enforcement:** If `scope.json` exists in the repo root, Captain validates the target URL against `allowed_domains` and `allowed_paths_prefix` before opening a browser. If `block_production` is `true`, URLs without a QA/staging indicator are rejected automatically.
- **Never** run against production URLs without explicit user confirmation
- Generated probe scripts use only `playwright.async_api`, `httpx`, `json`, and `asyncio` — no `eval`, `exec`, `os.system`, or `subprocess`
- All network operations in probe scripts carry a 30-second timeout
- Phases are strictly sequential; Captain will halt and report if any gate fails
