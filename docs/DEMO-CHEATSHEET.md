# FlowBusters demo cheatsheet

## What to say first

**Single flow:** “I record one legitimate workflow. The AI proposes abuse cases. The backend runs the probes and verifies the result against captured HTTP evidence.”

**Cross-flow:** “I combine several recorded workflows. FlowBusters looks for unsafe interactions between actions that were previously tested only in isolation.”

**Trust boundary:** “The AI proposes; the backend executes and decides. A model-written finding is never proof by itself.”

## What happens after I click Start or Analyze?

**Start Assessment:** FlowBusters opens the authorized target in a Playwright-controlled browser. I perform a normal workflow and finish the recording. The AI models the saved workflow and drafts test scenarios. The backend then executes each probe, captures its HTTP trace, and derives the report verdict.

**Analyze selected flows:** I select two to eight compatible source flows. The backend copies and authenticates their evidence. The AI proposes cross-flow conflicts. The backend validates the references, runs the probes, and builds the final normalized report.

Progress comes from real backend phases and execution results. Raw filenames and process details stay in the collapsed **Technical details** view.

## Local demo addresses

- Northstar Market: [http://localhost:3000](http://localhost:3000)
- FlowBusters frontend: [http://127.0.0.1:3001](http://127.0.0.1:3001)
- FlowBusters backend: [http://127.0.0.1:8000](http://127.0.0.1:8000)

Start the backend from the repository root:

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.api.main:app --host 127.0.0.1 --port 8000
```

Start the frontend from `frontend/` with an explicit non-conflicting port:

```powershell
npm run dev -- --host 127.0.0.1 --port 3001
```

Northstar is external to this repository. Start it from its own project on port `3000`, and ensure that exact non-production target is allowed by FlowBusters scope.

## What is true today?

- Recording and sampled semantic accessibility snapshots work.
- Cross-flow probes execute and receive locally signed receipts.
- The verifier prevents unsupported results from becoming `CONFIRMED`.
- The normalized report separates security findings, execution results, partial coverage, and excluded fixture executions instead of forcing them into one total.
- The final verified-UI demo still requires fresh schema-v2 source flows. Existing legacy source maps remain unverified provenance.

## Who controls what?

| AI controls | Backend controls |
|---|---|
| Workflow interpretation and attack hypotheses | Recording lifecycle and scope enforcement |
| State maps, application models, candidates, and mutation scripts | State-map, candidate, and provenance validation |
| Draft findings, explanations, and remediation suggestions | Probe execution, independent HTTP capture, receipt signing, invariant evaluation, verdicts, deduplication, and counts |

The HMAC is a local pipeline-integrity check. It is not external attestation and does not defend against an operator who controls both the run artifacts and the local signing key.

## What does the final report prove?

- `CONFIRMED`: authenticated execution violated a supported invariant and has acceptable business-rule provenance.
- `NOT_REPRODUCED`: the authenticated scenario completed, but the evaluated invariant held. This is not a universal safety claim.
- `NEEDS_REVIEW`: the execution or hypothesis is useful, but a required semantic or provenance element is missing.
- `CHECK_ERROR`: execution or verification could not be completed reliably.

The evidence view shows the ordered signed HTTP trace and the backend-resolved invariant. Setup steps are separated from attack actions. Agent-authored summary numbers cannot override backend-derived counts.

If the agent's preserved pre-execution draft still says `NOT_EXECUTED`, an authenticated terminal receipt replaces that stale verification in the normalized in-memory view. The original draft remains preserved for audit. A post-execution claim that contradicts the receipt is still rejected.

The report deliberately uses different units:

- **Security findings** are the atomic primary claims that receive security verdicts—at most one primary security finding per executed probe.
- **Primary execution results** show what every authenticated probe receipt produced before presentation filtering.
- **Partial coverage** means a supplementary path was captured, but it did not have its own complete trace, invariant, and violation result. It is evidence for follow-up, not a verdict.
- **Excluded setup-path execution** means the probe tested fixture setup, such as the demo reset endpoint. It stays visible in the execution log but is not counted as attack surface.
- **Probe accounting** reports planned probes, attempts, authenticated receipts, pending probes, and errors independently of finding counts.

That is why the number of execution results can differ from the number of security findings without any receipt disappearing.

## Northstar narration

### Use only when the displayed verdict is `CONFIRMED`

“The probe applied a `$30` price adjustment and completed a `$100` refund. The final signed response showed `$130` returned against a `$100` original amount. The backend resolved those values, evaluated the invariant, and verified the business-rule provenance. That is why this report is confirmed.”

### Use when the displayed verdict is `NEEDS_REVIEW`

“The probe executed and the report shows its authenticated HTTP evidence. The backend did not promote the result to confirmed because the report identifies a missing requirement, such as verified business-rule provenance. This is a reviewable hypothesis, not a proven vulnerability.”

## Six likely technical questions

### 1. “Why should I trust the finding if an LLM wrote the test?”

Do not trust the LLM claim by itself. The backend snapshots the script, independently captures supported HTTP transports, signs the receipt, and requires the report evidence to match it.

### 2. “Can the model invent a UI rule or set `validated: true`?”

It can emit text, but that flag has no authority. Verified UI provenance must resolve exact structured facts to the copied raw semantic timeline.

### 3. “Does HTTP 200 mean the attack worked?”

No. The verifier checks the complete before/action/after sequence and the resulting state. Status alone does not establish a vulnerability.

### 4. “How do you stop stale or fabricated report counts?”

The backend enumerates planned scripts, authenticates receipts, derives execution counts, reclassifies results, and computes deduplication during report loading.

### 5. “Is this a sandbox, and does it replace a pentest?”

No. The harness enforces HTTP scope and captures supported clients, but it is not an operating-system sandbox. Use a disposable authorized target. FlowBusters also does not cover workflows or invariants it did not observe and support.

### 6. “Why are there two `NEEDS_REVIEW` executions but only one `NEEDS_REVIEW` finding?”

Both authenticated receipts stay in the execution log. One is the reviewable security finding. The other tested the authorized demo-reset setup path, so it is labelled **Excluded setup-path execution** and does not inflate the security-finding count.

## References

- Assessment entry points and progress: [`frontend/src/pages/portal.tsx` — `PortalPage.handleSubmit`](../frontend/src/pages/portal.tsx); [`frontend/src/pages/applications.tsx` — `ApplicationsPage.start`](../frontend/src/pages/applications.tsx); [`frontend/src/pages/progress.tsx` — `ProgressPage`](../frontend/src/pages/progress.tsx); [`backend/api/main.py` — `start_assessment`, `start_cross_flow`, `stream_progress`](../backend/api/main.py)
- Recording and semantic snapshots: [`backend/runtime/recorder.py` — `record`, `compact_snapshot`, `add_ui_state`, `check_scope`](../backend/runtime/recorder.py)
- Cross-flow source preparation and candidates: [`backend/runtime/application_model.py` — `collect`, `prepare_inputs`, `validate_cross_flow_candidates`](../backend/runtime/application_model.py)
- State-map and UI provenance validation: [`backend/runtime/ui_provenance.py` — `validate_state_map`, `normalize_observed_ui_rules`, `validate_rule_reference`](../backend/runtime/ui_provenance.py)
- Probe execution, receipt precedence, partial coverage, trace verification, and counts: [`backend/runtime/probe_executor.py` — `key_for`, `execute`, `load_receipts`, `_is_stale_unexecuted_draft`, `_partial_coverage_for`, `trace_error`, `reconcile`](../backend/runtime/probe_executor.py); [`backend/runtime/probe_capture.py` — `main`](../backend/runtime/probe_capture.py)
- Atomic verdict normalization, setup exclusion, and deduplication: [`backend/runtime/verification.py` — `load_report`, `_is_setup_path_centered`, `_dedup_key`, `_collapse_duplicate_findings`, `classify`, `_classify_business_rule`, `normalize_report`](../backend/runtime/verification.py)
- Cross-flow final count wording: [`backend/runtime/crew_runner.py` — `_cross_flow_final_message`](../backend/runtime/crew_runner.py)
- Evidence rendering: [`frontend/src/components/ReportView.tsx` — `invariantFor`, `traceViews`, `ReportView`](../frontend/src/components/ReportView.tsx)
- Local commands and ports: [`frontend/package.json` — `scripts.dev`](../frontend/package.json); [`backend/api/main.py` — `app`, `health`](../backend/api/main.py)
