<p align="center">
  <img src="assets/flowbusters-banner.svg" alt="FlowBusters — The lock was only painted" width="100%"/>
</p>

<br/>

<p align="center">
  <strong>Your app passed every unit test. Every pen test. Every scan.<br/>
  But nobody tested whether the <em>workflow itself</em> can be cheated.</strong>
</p>

<p align="center">
  Most security tools check <em>endpoints</em>. FlowBusters checks <strong>business logic</strong> —<br/>
  the multi-step rules your app assumes users will follow but never actually enforces.
</p>

<h3 align="center">One demo. A self-serve portal. Full attack surface.</h3>

<p align="center">
  You walk through your workflow once in a real browser. The crew watches, then unleashes<br/>
  skip-step, role-swap, data-tamper, mass-assignment, pricing-tamper, double-spend, replay,<br/>
  and forced-browsing attacks against the <em>live app</em> — and delivers a CWE-mapped,<br/>
  expandable findings report in minutes.
</p>

<p align="center">
  <strong>No config to author. No infrastructure. A web portal + one recorded flow.</strong>
</p>

---

## ⚔️  What It Is

A **self-service web portal** that turns a live browser recording of a business
workflow into a vulnerability report. You drive a real browser once; the backend
spawns a single Claude Code session that plays a whole **crew** of specialist
agents in sequence and does the hunting for you.

|  | Piece | What it does |
|:---:|:---|:---|
| 🖥️ | **Portal** (FastAPI + React) | Start an assessment, watch live progress (SSE), read the report |
| 🎬 | **Recorder** | Opens a headed browser (Playwright MCP), captures your clicks + network traffic |
| 🔬 | **Analyst** | Turns the raw HAR into a state map: steps, roles, critical endpoints |
| 💣 | **Saboteur** | Forges adversarial probe scripts from the state map |
| 🔍 | **Prober** | Fires every script at the live target (raw HTTP, no browser), classifies each hit |
| 🧭 | **Captain** | Runs the whole thing, gates each phase, assembles the final report |

> ⚠️ **Always headed, always user-driven.** You must perform the workflow for the
> crew to capture it — there is no headless / fully-automated mode.

---

## 🚀  Quickstart

```bash
cp .env.example .env          # set ANTHROPIC_API_KEY

# backend
python -m venv .venv && source .venv/bin/activate
pip install -r pyproject.toml
cp mcp.json.example mcp.json  # point "command" at your node_modules/.bin/playwright-mcp
uvicorn backend.api.main:app --port 8000        # do NOT use --reload — it kills the in-flight crew

# frontend
npm install && cd frontend && npm run dev       # http://localhost:3000
```

| Variable | Purpose | Default |
|:---|:---|:---|
| `ANTHROPIC_API_KEY` | LLM credentials | — |
| `ANTHROPIC_MODEL` | Model to run the crew | `claude-sonnet-5` |
| `CREW_EFFORT` | Reasoning effort | `medium` |
| `MCP_CONFIG_PATH` / `CREW_DIR` / `ARTIFACTS_DIR` | MCP config / crew files / run root | `mcp.json` / `./crew` / `.` |

Then in the portal: enter a **Target URL** → **Start Assessment** → drive the
browser (log in, complete the flow) → **Finish recording**. The crew takes it from there.

---

## 🗺️  How It Works

```
   🎥 Record  ──►  🔬 Analyze  ──►  💣 Mutate  ──►  🔍 Probe  ──►  📜 Report
```

Sequential, gate-gated: **each phase must pass its gate before the next starts** —
the Captain halts the whole run on any failure.

| Phase | Agent | What happens | Gate → produces |
|:---:|:---|:---|:---|
| 🎥 **RECORD** | Recorder | Opens a **headed** browser (Playwright MCP); you drive the workflow, it captures clicks + network | `demo.json` + `recording.har` |
| 🔬 **ANALYZE** | Analyst | Dissects the HAR into the state map: transitions, roles, critical endpoints | `state_map.json` |
| 💣 **MUTATE** | Saboteur | Forges 5–8 adversarial scripts from the state map (see [Attack Vectors](#attack-vectors) below) | `mutations/*.py` |
| 🔍 **PROBE** ⚔️ | Prober | Fires each script at the live target (raw HTTP); classifies **BUG_FOUND · REJECTED · ERROR** | `findings.json` |
| 📜 **REPORT** | Captain | Assembles the CWE-mapped, expandable report (only if bugs found) | `remediation.md` |

---

## 📖  In Plain English

### The three capture files

| File | Made in | What it is | Analogy |
|:---|:---|:---|:---|
| `demo.json` | RECORD | **What you clicked/typed** — the interaction trace | a *video* of your demo |
| `recording.har` | RECORD | **Every network request/response** the app made (raw wire data) | the *packet capture* of the same demo |
| `state_map.json` | ANALYZE | The AI's **interpretation**: steps, roles, which endpoints matter | the *highlight reel + playbook* |

`demo.json` + `recording.har` are two layers of Phase 1 (UI actions vs the backend
calls they trigger) — together they tell you "clicking **Approve** sent
`POST /api/order/5/approve`." `state_map.json` is derived from both: it maps
**transitions** (create → pay → approve), **roles** (user vs admin, from the
captured cookies/tokens), and **critical endpoints** (the state-changing ones —
the attack surface).

### Probe: browser or curl?

**Raw HTTP, not a browser.** Each `mutations/*.py` script is a tiny `httpx`
program: it fires *one* request at the live target, gets a status code, and the
Prober judges — **200 when it should've been rejected → `BUG_FOUND`** ·
**401/403 → `REJECTED`** · **crash → `ERROR`**. Dropping the UI is what lets the
crew do things a browser can't: request with *no login*, re-fire an old request,
set the price to −1. (Phase 1 *does* use a real browser — that's where your demo
is captured.)

### Terms used in the report

| Term | Meaning |
|:---|:---|
| **CWE-mapped** | Each finding is tagged with its CWE number — the standard dictionary of bug types (`CWE-287` = improper auth, `CWE-862` = missing authorization, `CWE-20` = bad input validation) |
| **SSE** | Server-Sent Events — how the backend *pushes* live progress to the page |
| **HAR** | HTTP Archive — standard format recording every request/response (a Wireshark dump) |
| **mutation script** | The tiny throwaway program the Saboteur writes to try *one* cheat |
| **BUG_FOUND / REJECTED / ERROR** | The probe was accepted (bug) / correctly blocked / failed to run |

---

## 🧠  AI vs. Script

**One LLM plays all five "agents."** The crew is a *single* Claude Code session
spawned by the backend; the "Captain spawns the Recorder, then the Analyst…"
framing is a persona + protocol inside one reasoning run. Every phase below is
genuine agentic AI — what's *deterministic code* is only the coordinator and the
helpers:

| Who | What it is |
|:---|:---|
| 🧭 **Captain** | The LLM — validates scope, sequences phases, checks gates, assembles the report (lightest role: mostly gate-checking) |
| 🎬 **Recorder** | The LLM driving the browser · helper: `crew/scripts/synthesize_har.py` (fixed HAR builder, no AI) |
| 🔬 **Analyst** | The LLM reading the HAR + demo and *judging* the state map |
| 💣 **Saboteur** | The LLM, at full creativity — **writes novel probe scripts from scratch** per target (most agentic role) |
| 🔍 **Prober** | The LLM — runs each script, interprets responses, runs the mandatory auth sanity check, writes `findings.json` + `remediation.md` |
| ⚙️ **backend/** | *No AI.* Plain FastAPI: spawns `claude`, streams progress, watches the filesystem, enforces timeouts. Makes **no** security decisions |
| 🐍 **mutations/*.py** (at runtime) | *No AI while running* — deterministic `httpx` scripts · but **invented by the Saboteur (AI) each run** |

The Markdown **charters/skills** (`crew/`) are the fourth category: written
procedures that pin down "what order, what gates, what to check" around the
agentic core.

---

## 💻  The Progress Page

Start an assessment and the portal walks you through the five phases in real time
(SSE — pushed the moment each happens). You drive the browser during **Record**,
then the crew runs the rest unattended. When **Report** goes green, the **View
report** button links straight to the CWE-mapped findings page.

<p align="center">
  <img src="assets/progress.png" alt="FlowBusters progress page — all five phases complete with the live event log" width="480"/>
</p>

The expandable **Event log** is the crew's own narration: browser opened, your
*Finish recording* click, *Browser closed — recording secured*, each artifact as
it lands, and the Probe→Report handoff as `findings.json` is written.

---

## 🗡️  Attack Vectors

| Attack | What it does |
|:---|:---|
| 💨 `SKIP_STEP` | Jump straight to a late-stage endpoint, bypassing required prerequisites |
| 🎭 `ROLE_SWAP` | Use Role A's session to hit Role B's endpoints |
| 🔧 `DATA_TAMPER` | Mutate body values — IDs, amounts, statuses — to unauthorized values |
| 🏷️ `MASS_ASSIGNMENT` | Send extra/privileged fields the UI never shows (`is_admin: true`) and check whether they stick |
| 💲 `PRICING_TAMPER` | Forge prices — negative, zero, 100% discount — in a payment request |
| ♾️ `DOUBLE_SPEND` | Re-fire the same request (sequentially, or 5× concurrently) to test idempotency |
| ⏪ `REPLAY_ATTACK` | Re-fire a captured request after the server should have invalidated it |
| 🚪 `FORCED_BROWSING` | Access an endpoint directly, skipping the expected UI flow entirely |

---

## 🗂️  Artifacts

```
runs/{flow-name}/
  flows/
    demo.json          # what you clicked/typed        (Phase 1)
    recording.har      # raw network capture           (Phase 1)
    state_map.json     # steps, roles, attack surface  (Phase 2)
  mutations/
    01_*.py … 08_*.py  # one script per attack         (Phase 3)
  reports/
    findings.json      # findings[] (confirmed vulns, Critical-first)
                       # + results[] (raw per-script log) + summary
    remediation.md     # CWE-mapped fixes, one section per finding (only if bugs)
```

`findings.json` summary: `{ "bugs_found": N, "critical_findings": N,
"rejected": N, "errors": N }`. Every finding carries `severity`, `cwe[]`,
`evidence` (request/response pairs), `source` (`AUTH_CHECK` | `MUTATION_SCRIPT`
| `ANALYSIS`).

**Flow names** are kebab-case (`end2end-test`); omitted → `default`.

---

## 🎯  Outcomes

| Outcome | Meaning |
|:---|:---|
| ⚡ `BUG_FOUND` | Server accepted a cheat it should have rejected |
| ✅ `REJECTED` | Server correctly blocked it (4xx/5xx) |
| ⚠️ `ERROR` | The probe itself failed to run |

`remediation.md` is written only when at least one `BUG_FOUND` exists —
Critical first, each with CWE mapping and a concrete fix.

---

## 🛡️  Safety

- **Scope enforcement:** if `scope.json` exists, the Captain validates the target against `allowed_domains` / `allowed_paths_prefix` before opening a browser; `block_production: true` rejects non-QA URLs.
- **Never** run against production without explicit confirmation.
- Generated probes are restricted by charter to `playwright.async_api`, `httpx`, `json`, `asyncio` — no `eval`, `exec`, `os.system`, `subprocess` (instruction-level, not sandboxed).
- 30-second timeout on every probe request; phases are strictly sequential.
- **No `--reload`** on the backend during an assessment — it orphans the in-flight crew.

**Runs** live under `runs/<flow-name>/`; the `/reports` page lists every run on
disk, and `/<flow>/report` shows the full findings page.

<p align="center">
  <sub>⚔️ FlowBusters — hunt business logic flaws before attackers do.</sub>
</p>