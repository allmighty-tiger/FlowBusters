# FlowBusters — Crew Team

## Project Context

**What:** A zero-config, multi-agent system that records business workflows via Playwright MCP, analyzes them for state transitions, generates adversarial mutation scripts, and probes the live environment for business logic flaws.

**Runtime:** FlowBusters uses VS Code Copilot as the runtime. No external LLM proxies or Python environments required.

**Tools Available:**
- Playwright MCP — headed browser automation, DOM interaction capture, HAR recording
- Terminal (bash) — script execution, file operations, subprocess management
- File Read/Write — JSON artifact exchange between phases
- LLM reasoning — analysis, mutation generation, remediation authoring

**Invocation Example:**
```
"Captain, run FlowBusters against https://example.com/login"
```

## Members

| Name | Role | Scope | Badge |
|------|------|-------|-------|
| Captain | Orchestrator | Phase sequencing, gate verification, final report assembly | 🏗️ Lead |
| Recorder | Flow Recorder | Playwright MCP browser capture, HAR/DOM recording | 🎥 Recorder |
| Analyst | State Analyst | HAR parsing, state transition extraction, endpoint classification | 🔬 Analyst |
| Saboteur | Mutation Engineer | Adversarial script generation, business logic attack vectors | 💣 Saboteur |
| Prober | Execution Prober | Script execution, outcome classification, remediation | 🔍 Prober |
| Scribe | Session Logger | Memory, decisions, session logs | 📋 Scribe |

## Workflow

Sequential 4-phase pipeline with blocking gates:

1. **RECORD** → Recorder captures user demo via Playwright MCP
2. **ANALYZE** → Analyst extracts state transitions from HAR
3. **MUTATE** → Saboteur generates adversarial scripts
4. **PROBE** → Prober executes scripts and reports findings

Each phase must pass its verification gate before the next phase begins. Captain manages state and artifact handoff.
