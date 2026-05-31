```
╔══════════════════════════════════════════════════════════════╗
║  💣  F L O W B U S T E R S                                  ║
║  Hunt business logic flaws before attackers do               ║
║  ── powered by Playwright MCP ──────────────────────────    ║
╚══════════════════════════════════════════════════════════════╝
```

> **Your app passed every test. But did you test the rules of the game?**

Can an approver jump straight to the approval endpoint?  
Can a regular user replay an admin's captured request?  
Can someone close a loan without completing the required steps?

**FlowBusters finds out.**

Demo your workflow once. FlowBusters records it, maps every state transition, generates adversarial probe scripts targeting skip-step, role-swap, replay, and forced-browsing attacks — then executes them and hands you a CWE-mapped remediation report.

No config. No infrastructure. Just Playwright MCP and one command.

```
Captain, run FlowBusters against https://qa.example.com/your-workflow
```

---

## ⚔️  How It Works

```
▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓  SELECT YOUR MISSION  ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
```

Four specialist agents. Four locked levels. One boss fight with your app's logic.  
**No level starts until the previous gate clears. Captain holds the master key.**

```
╔══════════════════════════════════════════════════════════════════════╗
║  🎥  LEVEL 1  ░░  R E C O R D                                       ║
║                                                                      ║
║  Recorder opens a live browser via Playwright MCP.                   ║
║  You play the workflow. Every click, every request — captured.       ║
║                                                                      ║
║  [ GATE UNLOCKED ] ──►  demo.json  +  recording.har                 ║
╚══════════════════════════════╦═══════════════════════════════════════╝
                               ║  ✔  GATE CLEAR
╔══════════════════════════════╩═══════════════════════════════════════╗
║  🔬  LEVEL 2  ░░  A N A L Y Z E                                     ║
║                                                                      ║
║  Analyst dissects the HAR. Filters noise. Maps every state           ║
║  transition, auth token, role context, and critical endpoint.        ║
║                                                                      ║
║  [ GATE UNLOCKED ] ──►  state_map.json                              ║
╚══════════════════════════════╦═══════════════════════════════════════╝
                               ║  ✔  GATE CLEAR
╔══════════════════════════════╩═══════════════════════════════════════╗
║  💣  LEVEL 3  ░░  M U T A T E                                       ║
║                                                                      ║
║  Saboteur forges adversarial scripts from the state map:             ║
║  SKIP_STEP · ROLE_SWAP · DATA_TAMPER · REPLAY · FORCED_BROWSING     ║
║                                                                      ║
║  [ GATE UNLOCKED ] ──►  mutations/*.py  (3–5 scripts)               ║
╚══════════════════════════════╦═══════════════════════════════════════╝
                               ║  ✔  GATE CLEAR
╔══════════════════════════════╩═══════════════════════════════════════╗
║  🔍  LEVEL 4  ░░  P R O B E              ★  B O S S  F I G H T  ★  ║
║                                                                      ║
║  Prober fires every script at the live target.                       ║
║  Each hit classified:  BUG_FOUND  ·  REJECTED  ·  ERROR             ║
║                                                                      ║
║  [ GATE UNLOCKED ] ──►  findings.json  +  remediation.md            ║
╚══════════════════════════════╦═══════════════════════════════════════╝
                               ║
                        🏗️  CAPTAIN
                    assembles final report
```

|  | Level | Agent | Drops |
|:---:|:---|:---|:---|
| 🎥 | **RECORD** | Recorder | `demo.json` · `recording.har` |
| 🔬 | **ANALYZE** | Analyst | `state_map.json` |
| 💣 | **MUTATE** | Saboteur | `mutations/*.py` × 3–5 |
| 🔍 | **PROBE** | Prober | `findings.json` · `remediation.md` |

---

## 🚀  Quickstart

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

## �️  Sample Run

What a full FlowBusters session looks like in your terminal:

```
╔══════════════════════════════════════════════════════════════════════╗
║  🎥  PHASE 1 — RECORD                                                ║
╚══════════════════════════════════════════════════════════════════════╝

  Browser is open. The page loaded — go ahead and:
    1. Log in with your credentials
    2. Complete the full business flow you want to test
    3. Tell me when you're done — I'll capture everything

  Take your time — I'm recording. 🔴

❯ done

✅ Phase 1 RECORD — Gate passed.
   · 10 interactions captured
   · 109 network requests recorded
   · flows/demo.json (4.4 KB) + flows/recording.har (168 KB) saved

╔══════════════════════════════════════════════════════════════════════╗
║  🔬  PHASE 2 — ANALYZE                                               ║
╚══════════════════════════════════════════════════════════════════════╝

✅ Phase 2 ANALYZE — Gate passed.
   · 8 state transitions identified
   · 2 roles inferred
   · 7 critical endpoints mapped
   · flows/state_map.json saved

╔══════════════════════════════════════════════════════════════════════╗
║  💣  PHASE 3 — MUTATE                                                ║
╚══════════════════════════════════════════════════════════════════════╝

✅ Phase 3 MUTATE — Gate passed.
   5 adversarial probe scripts generated:

   ┌────┬─────────────────────┬───────────────────────┐
   │ #  │ Type                │ Target                │
   ├────┼─────────────────────┼───────────────────────┤
   │ 01 │ SKIP_STEP           │ /api/approve          │
   │ 02 │ ROLE_SWAP           │ /api/access-level     │
   │ 03 │ DATA_TAMPER         │ /api/orders           │
   │ 04 │ REPLAY_ATTACK       │ /api/approve          │
   │ 05 │ FORCED_BROWSING     │ /api/users/search     │
   └────┴─────────────────────┴───────────────────────┘

╔══════════════════════════════════════════════════════════════════════╗
║  🔍  PHASE 4 — PROBE               ★  B O S S  F I G H T  ★        ║
╚══════════════════════════════════════════════════════════════════════╝

✅ Phase 4 PROBE — Complete. All gates passed.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🏗️  FlowBusters Final Report  ·  https://qa.example.com/your-workflow
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

   ┌──────────────────────────────┬─────────────────────┬──────────────────┐
   │ Probe                        │ Mutation            │ Outcome          │
   ├──────────────────────────────┼─────────────────────┼──────────────────┤
   │ 01_skip_step_approve         │ SKIP_STEP           │ 🔴 BUG_FOUND     │
   │ 02_role_swap_access_level    │ ROLE_SWAP           │ ✅ REJECTED       │
   │ 03_data_tamper_orders        │ DATA_TAMPER         │ 🔴 BUG_FOUND     │
   │ 04_replay_approve            │ REPLAY_ATTACK       │ ✅ REJECTED       │
   │ 05_forced_browse_user_search │ FORCED_BROWSING     │ ✅ REJECTED       │
   └──────────────────────────────┴─────────────────────┴──────────────────┘

   🔴  2 bugs found  ·  3 properly rejected  ·  0 errors

   Verdict: 2 business logic vulnerabilities confirmed.
            reports/remediation.md generated. ⚠️

   ┌─────────────────────────────────────────────────────────────────────┐
   │  REMEDIATION REPORT                                                 │
   ├─────────────────────────────────────────────────────────────────────┤
   │  BUG 1 · CRITICAL  ·  CWE-285: Improper Authorization              │
   │  /api/approve accepted a request skipping the required              │
   │  prerequisite step. Server returned HTTP 200.                       │
   │  Fix: enforce workflow state server-side before processing          │
   │       approval; do not rely on UI step completion alone.            │
   ├─────────────────────────────────────────────────────────────────────┤
   │  BUG 2 · HIGH  ·  CWE-20: Improper Input Validation                │
   │  /api/orders accepted a tampered order ID belonging to a            │
   │  different user. Server returned HTTP 200.                          │
   │  Fix: validate resource ownership server-side on every              │
   │       write operation; never trust client-supplied IDs.             │
   └─────────────────────────────────────────────────────────────────────┘

   Artifacts:
     flows/demo.json          interaction trace
     flows/recording.har      network capture
     flows/state_map.json     8 transitions · 7 critical endpoints
     mutations/*.py           5 probe scripts
     reports/findings.json    full results
     reports/remediation.md   2 findings with CWE mapping  ← generated
```

---

## 🗡️  Attack Vectors

Saboteur generates scripts targeting these attack vectors:

| Attack | What it does |
|:---|:---|
| 💨 `SKIP_STEP` | Jump straight to a late-stage endpoint, bypassing required prerequisites |
| 🎭 `ROLE_SWAP` | Use Role A's session cookies to hit Role B's endpoints |
| 🔧 `DATA_TAMPER` | Mutate request body values — IDs, amounts, statuses — to unauthorized payloads |
| ⏪ `REPLAY_ATTACK` | Re-fire a captured request after the server should have invalidated it |
| 🚪 `FORCED_BROWSING` | Access endpoints directly, skipping the expected UI flow entirely |

---

## 🗂️  Artifact Structure

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

## 🎯  Probe Outcomes

| Outcome | Meaning |
|:---|:---|
| 🔴 `BUG_FOUND` | Server returned 2xx — accepted something it should have rejected |
| 🟢 `REJECTED` | Server correctly blocked the malicious request (4xx/5xx) |
| ⚪ `ERROR` | Script failed — timeout, import error, or malformed output |

When a `BUG_FOUND` outcome is recorded, Prober generates `reports/remediation.md` with CWE ID mapping, severity rating, and specific fix recommendations.

---

## 🛠️  Requirements

- **Playwright MCP** — headed browser automation for Phase 1 capture
- **Terminal (bash)** — script execution and file validation
- **An MCP-capable AI agent runtime** — LLM reasoning for analysis, mutation generation, and remediation authoring (e.g. any assistant that supports MCP tool use)
- **Python 3** — runtime for executing generated probe scripts (`playwright`, `httpx`)

---

## 🛡️  Safety

- **Scope enforcement:** If `scope.json` exists in the repo root, Captain validates the target URL against `allowed_domains` and `allowed_paths_prefix` before opening a browser. If `block_production` is `true`, URLs without a QA/staging indicator are rejected automatically.
- **Never** run against production URLs without explicit user confirmation
- Generated probe scripts use only `playwright.async_api`, `httpx`, `json`, and `asyncio` — no `eval`, `exec`, `os.system`, or `subprocess`
- All network operations in probe scripts carry a 30-second timeout
- Phases are strictly sequential; Captain will halt and report if any gate fails
