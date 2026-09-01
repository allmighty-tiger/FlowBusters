# FlowBusters — Routing

## Invocation Format

Captain accepts:
- `Captain, run FlowBusters against {url}`
- `Captain, run FlowBusters against {url} --flow-name {flow-name}`

`--flow-name` is optional and defaults to `default`.
`flow-name` must be kebab-case: lowercase letters, numbers, and hyphens only.
Each run writes artifacts into flow-specific subdirectories so multiple flows can coexist without conflicts:
- `flows/{flow-name}/`
- `mutations/{flow-name}/`
- `reports/{flow-name}/`

## Workflow: Sequential 4-Phase Pipeline

**CRITICAL: Phases are strictly sequential. No parallel spawns. Captain manages state and passes artifacts between phases.**

---

## Phase 1: RECORD

| Field | Value |
|-------|-------|
| **Agent** | Recorder |
| **Trigger** | Captain receives target URL from user |
| **Invocation** | Captain passes target URL plus optional `--flow-name {flow-name}`. If omitted, use `default`. |
| **Input** | Target URL, resolved flow name |
| **Action** | Open headed browser via Playwright MCP, wait for user to complete full workflow demo (login + business flow) |
| **Output** | `flows/{flow-name}/demo.json` + `flows/{flow-name}/recording.har` |
| **Gate** | Recorder MUST confirm both files exist and contain valid data at the flow-specific paths. Captain verifies file existence before proceeding. |
| **Blocks** | Phase 2 cannot start until gate passes |

---

## Phase 2: ANALYZE

| Field | Value |
|-------|-------|
| **Agent** | Analyst |
| **Trigger** | Phase 1 gate passed |
| **Invocation** | Captain passes the resolved flow name and Phase 1 artifact paths for that flow. |
| **Input** | `flows/{flow-name}/demo.json`, `flows/{flow-name}/recording.har` |
| **Action** | Parse HAR, filter static assets, identify state-changing endpoints, extract auth tokens and role contexts |
| **Output** | `flows/{flow-name}/state_map.json` |
| **Gate** | Analyst MUST confirm `flows/{flow-name}/state_map.json` contains at least 1 transition and roles extracted. Captain validates schema. |
| **Blocks** | Phase 3 cannot start until gate passes |

---

## Phase 3: MUTATE

| Field | Value |
|-------|-------|
| **Agent** | Saboteur |
| **Trigger** | Phase 2 gate passed |
| **Invocation** | Captain passes the resolved flow name and `flows/{flow-name}/state_map.json`. |
| **Input** | `flows/{flow-name}/state_map.json` |
| **Action** | Generate 3-5 adversarial Python scripts targeting business logic flaws |
| **Output** | `mutations/{flow-name}/*.py` (3-5 scripts) |
| **Gate** | Saboteur MUST confirm all scripts are written under `mutations/{flow-name}/`, syntax-checked via py_compile, and describe each mutation type. Captain verifies file count. |
| **Blocks** | Phase 4 cannot start until gate passes |

---

## Phase 4: PROBE

| Field | Value |
|-------|-------|
| **Agent** | Prober |
| **Trigger** | Phase 3 gate passed |
| **Invocation** | Captain passes the resolved flow name and mutation directory for that flow. |
| **Input** | `mutations/{flow-name}/*.py` scripts |
| **Action** | Execute each script via terminal with 30s timeout, parse stdout JSON, classify outcomes |
| **Output** | `reports/{flow-name}/findings.json` + `reports/{flow-name}/remediation.md` (if bugs found) |
| **Gate** | Prober MUST print summary table showing each script's outcome (BUG_FOUND / REJECTED / ERROR). Captain includes it in the final report and verifies `reports/{flow-name}/findings.json` exists. |
| **Blocks** | None — final phase |

---

## Routing Rules

| Signal | Route To |
|--------|----------|
| `Captain, run FlowBusters against {url}` | Captain (orchestrates full pipeline with `flow-name=default`) |
| `Captain, run FlowBusters against {url} --flow-name {flow-name}` | Captain (orchestrates full pipeline for the named flow) |
| `Record the flow` / `Start recording` | Recorder (Phase 1 only) |
| `Analyze the recording` | Analyst (Phase 2 only, requires Phase 1 artifacts for the same flow) |
| `Generate mutations` / `Mutate` | Saboteur (Phase 3 only, requires Phase 2 artifacts for the same flow) |
| `Run probes` / `Probe` / `Execute` | Prober (Phase 4 only, requires Phase 3 artifacts for the same flow) |
| `Status` / `Where are we?` | Captain (report current phase) |

## Artifact Contracts

All inter-phase communication is via JSON files. No prose between agents. Strict schemas:

- `flows/{flow-name}/demo.json` — Array of DOM interaction events with timestamps
- `flows/{flow-name}/recording.har` — Standard HAR 1.2 format
- `flows/{flow-name}/state_map.json` — `{ "target_url": "...", "transitions": [...], "roles": [...], "critical_endpoints": [...] }`
- `mutations/{flow-name}/*.py` — Self-contained Python scripts, each prints one JSON line to stdout
- `reports/{flow-name}/findings.json` — Array of `{ "script": "...", "outcome": "BUG_FOUND|REJECTED|ERROR", "details": {...} }`
- `reports/{flow-name}/remediation.md` — Markdown with CWE mapping, severity ratings, and fix recommendations
