# FlowBusters demo cheatsheet

Choose source flows once. Cross-flow hypotheses must cite real actions from two
authenticated sources. If those references are invalid, the backend gives the AI
two correction attempts before execution. Unresolved hypotheses are unverified
coverage, not findings. If none qualify, cross-flow could not be checked; that is
not a clean security result. AI candidates and backend coverage probes are distinct.

Reference: [`candidate_correction.py`](../backend/runtime/candidate_correction.py),
`correct_candidates` and `validate_candidate`.

All generated scripts are preflight-checked before any planned probe executes.
A supported monetary check must declare its exact final-state invariant; other
claims stay reviewable with a specific unsupported-predicate explanation.
Static checks do not replace signed execution and final-state validation.

Pending is not paid. A refund check stopped before completion remains reviewable,
not a validated control. A zero payout also does not prove that a submitted amount
was handled correctly. Those checks need a predicate for the requested behavior.
The receipts remain visible even when coverage is incomplete; one proven price
issue does not imply complete coverage of every AI hypothesis.

Technical reference: [`verification.py`](../backend/runtime/verification.py),
`_claim_coverage_error` and `_classify_business_rule`.

## One-version demo: Northstar identity 2

Two probes can reproduce one vulnerability with different amounts. The report
shows one unique issue, with both findings and signed executions available for
inspection. Findings, unique vulnerabilities and execution attempts are separate
counts. The execution log labels AI-generated probes and backend coverage plans;
older matching templates explicitly say that authorship was not recorded.

A 404 on a required action means the scenario was not established. An unchanged
or in-bounds total cannot turn that into “protection worked.” Such a result needs
review and has incomplete coverage. New generation uses the authenticated
endpoint catalog, including full documented request/completion chains.

References: `_unique_vulnerabilities`, `_endpoint_coverage_error`, `normalize_report`
in [`verification.py`](../backend/runtime/verification.py); `prepare_endpoint_catalog`,
`preflight_endpoints` in [`endpoint_catalog.py`](../backend/runtime/endpoint_catalog.py);
`receipt_probe_origin` in [`coverage_plans.py`](../backend/runtime/coverage_plans.py).

Record three ordinary workflows, each from a reset order: price adjustment;
refund request **and completion**; cancellation. Each should show the ordinary
amount: $30, $100, $100. Then select these three sources for cross-flow.

The demo has four distinct intentional defects. A price amount override can pay
$130. A repeated refund completion can accumulate $200. A cancellation amount
override can pay $200. Ordinary adjustment followed by a complete refund can
return $130 without tampering or replay. Pending refund now blocks cancellation;
the old refund-request/cancel/complete sequence is not one of these four cases.

Say: “We combine AI-generated tests with versioned backend coverage inputs.
The tests do not contain expected verdicts. The backend verifies signed requests,
the final state and the independently approved product rule.”

Each configured check resets once and ends with GET /api/order. The arithmetic
is always totalReturned versus originalAmount. A contract failure stops the
remaining queue; exit code zero alone proves nothing. Only narrate a confirmed
violation when the displayed verdict is actually CONFIRMED. NEEDS_REVIEW means
the evidence, rule applicability or claim coverage still needs review.

References: `prepare_coverage_probes` / `render_probe` in
[`coverage_plans.py`](../backend/runtime/coverage_plans.py),
[`coverage_plans.json`](../backend/runtime/coverage_plans.json),
`execute_validated_probes` in [`crew_runner.py`](../backend/runtime/crew_runner.py),
`execute_run` / `validate_probe_output` in
[`probe_executor.py`](../backend/runtime/probe_executor.py), and
[`NorthstarCoverageIntegrationTests`](../backend/tests/test_northstar_coverage_integration.py).

## Current saved cross-flow example

On 2026-09-17, the Northstar Market owner approved the product rule `order.totalReturned <= order.originalAmount`. It is not inferred from a probe. Future reports apply it only to a backend-authenticated `northstar-market` identity and an exact executable predicate. Earlier evidence needs a separate verified identity migration and an explicit retrospective label.

The mass-assignment probe sent inflated amounts and a completed status. The server returned a pending $100 refund request with $0 completed. Those observations are visible, but the probe's amount-bound predicate cannot establish the full input-assignment rule. That finding remains NEEDS_REVIEW and does not count as a held control.

Technical references: [backend requirement registry](../backend/runtime/business_requirements.json), [`apply_backend_requirements`](../backend/runtime/business_requirements.py), [`_claim_coverage_error` and `_classify_business_rule`](../backend/runtime/verification.py), [`remediationFor`](../frontend/src/components/ReportView.tsx).

## What to say first

**Single flow:** “I record one legitimate workflow. The AI proposes abuse cases. The backend runs each valid probe and verifies its output against captured HTTP evidence.”

**Cross-flow:** “I combine several recorded workflows. FlowBusters looks for unsafe interactions between actions that were previously tested only in isolation.”

**Trust boundary:** “The AI proposes; the backend assigns request sequence numbers, captures, authenticates, validates the evidence contract, and decides. A model-written finding is never proof by itself.”

## What happens after I click Start or Analyze?

**Start Assessment:** FlowBusters opens the authorized target in a Playwright-controlled browser. I perform a normal workflow and finish the recording. The AI models the saved workflow and drafts test scenarios. The backend preflights generated output contracts, executes probes, captures their HTTP traces, and derives the report verdicts. If executed output breaks the evidence contract, its terminal receipt is retained and later probes are not started.

**Analyze selected flows:** I select two to eight compatible source flows. The backend copies and authenticates their evidence. The AI proposes cross-flow conflicts. The backend validates the references and probe contracts, runs the valid probes, and builds the final normalized report.

A source needs a validated recording and a valid schema-v2 state map, not a
successful security assessment. Zero probes do not invalidate the recording.
The backend checks marker/counts, scope, raw evidence, UI facts and matching
origins. Missing report metadata does not block a valid source; conflicting
target origins do. Legacy state maps need fresh validated analysis before new
cross-flow selection.

“0 probes generated / no security checks executed” means **not assessed**, not
“no vulnerabilities.” The report uses the validated recording's flow and end
date when draft metadata is missing. That date is labelled “Recording completed.”

Progress comes from real backend phases and execution results. Raw filenames and process details stay in the collapsed **Technical details** view.

If the state map fails validation, probes stay blocked. After draft generation
exits, a dedicated Analyst receives the exact error and immutable evidence for
up to two correction attempts. The backend revalidates every proposal; if neither
passes, the run fails with the precise rule/fact error and no probes execute.
Visible text must reference a unique
snapshot element, not a list of appeared/disappeared elements.

For an existing validated `cancel` recording, start a separate reanalysis from
the repository root only when ready (the destination must not exist):

```powershell
.\.venv\Scripts\python.exe -m backend.runtime.reanalyze --source cancel --destination cancel-reanalysis-v1
```

This copies validated recording evidence into the new run without opening a
browser or copying the rejected state map; it then runs analysis and, only if
the gates pass, backend probes. The source recording remains unchanged.

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
- New probe output accounts for setup requests with harness-assigned transport
  sequences; a zero process exit with invalid evidence is not a successful check.
- The verifier prevents unsupported results from becoming `CONFIRMED`.
- Before creating a recorded run, FlowBusters checks Northstar's stable `/.well-known/flowbusters-identity` response against the backend registry and allowed origin. A missing, failing, or mismatched endpoint produces an unbound run, so the Northstar product rule cannot apply. This is a deterministic check for the controlled local demo, not cryptographic proof of which process owns port 3000. The saved identity is HMAC-protected against later artifact substitution.
- The normalized report separates security findings, execution results, partial coverage, and excluded fixture executions instead of forcing them into one total.
- The final verified-UI demo still requires fresh schema-v2 source flows. Existing legacy source maps remain unverified provenance.

## Who controls what?

| AI controls | Backend controls |
|---|---|
| Workflow interpretation and attack hypotheses | Recording lifecycle and scope enforcement |
| State maps, application models, candidates, and mutation scripts | State-map, candidate, and provenance validation |
| Draft findings, explanations, and remediation suggestions | Probe execution, independent HTTP capture, receipt signing, controlled requirement matching, invariant evaluation, verdicts, deduplication, and counts |

The HMAC is a local pipeline-integrity check. It is not external attestation and does not defend against an operator who controls both the run artifacts and the local signing key.

## What does the final report prove?

- `CONFIRMED`: authenticated execution violated a supported invariant, the predicate actually expresses the claim, and the rule has an accepted user/specification source or an exact backend-controlled requirement match.
- `NOT_REPRODUCED`: the authenticated scenario completed, but the evaluated invariant held. This is not a universal safety claim.
- `NEEDS_REVIEW`: the execution or hypothesis is useful, but a required semantic or provenance element is missing.
- `CHECK_ERROR`: execution or verification could not be completed reliably.

The evidence view shows the ordered signed HTTP trace and the backend-resolved invariant. Setup steps are separated from attack actions. Proven facts are displayed separately from remediation recommendations. Agent-authored summary numbers cannot override backend-derived counts.

If the agent's preserved pre-execution draft still says `NOT_EXECUTED`, an authenticated terminal receipt replaces that stale verification in the normalized in-memory view. The original draft remains preserved for audit. A post-execution claim that contradicts the receipt is still rejected.

The report deliberately uses different units:

- **Security findings** are the atomic primary claims that receive security verdicts—at most one primary security finding per executed probe.
- **Primary execution results** show what every authenticated probe receipt produced before presentation filtering.
- **Partial coverage** means a supplementary path was captured, but it did not have its own complete trace, invariant, and violation result. It is evidence for follow-up, not a verdict.
- **Excluded setup-path execution** means the probe tested fixture setup, such as the demo reset endpoint. It stays visible in the execution log but is not counted as attack surface.
- **Probe accounting** reports planned probes, attempts, authenticated terminal
  receipts, pending probes, process errors, evidence-contract errors, and signed
  trace mismatches independently of finding counts.

That is why the number of execution results can differ from the number of security findings without any receipt disappearing.

Equivalent findings are collapsed only when their authenticated state-changing
request chain and evaluated invariant match. A different CWE, severity, title,
or claimed scheduling mode does not create a second vulnerability. The extra
receipt remains visible as deduplicated evidence for the canonical finding.
The displayed verdict explanation is generated from the backend-evaluated
invariant; free-form probe prose cannot promote a concurrency or impact claim.

## Northstar narration

### Use only when the displayed verdict is `CONFIRMED`

“The probe applied a `$30` price adjustment and completed a `$100` refund. The final signed response showed `$130` returned against a `$100` original amount. The backend resolved those values, evaluated the invariant, and verified the business-rule provenance. That is why this report is confirmed.”

### Use when the displayed verdict is `NEEDS_REVIEW`

“The probe executed and the report shows its authenticated HTTP evidence. The backend did not promote the result to confirmed because the report identifies a missing requirement, such as verified business-rule provenance. This is a reviewable hypothesis, not a proven vulnerability.”

For UI-derived arithmetic claims: “The UI evidence can prove which controls were visible. It does not by itself prove a payout ceiling. Until an accepted user/specification source or an exact backend-controlled requirement supplies that rule, the measured violation remains `NEEDS_REVIEW`.”

For a product-rule result: “The backend authenticated the application identity, selected the applicable requirement version, matched the exact executable predicate, and then evaluated signed state evidence. If evidence predates assertion, the report says retrospective evaluation explicitly.”

For client-input tampering: “The signed trace shows the submitted value, the action response, and the final state. A final-state sum can show that accounting stayed bounded, but it cannot prove whether the server trusted, clamped, ignored, or recomputed that input. That claim remains `NEEDS_REVIEW` unless an executable request-to-expected-state predicate covers it.”

## Six likely technical questions

### 1. “Why should I trust the finding if an LLM wrote the test?”

Do not trust the LLM claim by itself. The backend snapshots the script, independently captures supported HTTP transports, signs the receipt, and requires the report evidence to match it.

Setup and reset requests receive sequence numbers too. Generated probes copy
the sequence assigned by the harness instead of maintaining a second counter.
A missing invariant or malformed sequence is an evidence-contract error; a
crashed or timed-out process is a separate process error.
For a supported numeric invariant, the generated probe must emit a real JSON
boolean computed from its AFTER response. `null` is rejected before execution
when visible as a literal, and backend recomputation never fills it in.

### 2. “Can the model invent a UI rule or set `validated: true`?”

It can emit text, but that flag has no authority. Verified UI provenance must resolve exact structured facts to the copied raw semantic timeline.

The model cannot create a trusted business requirement by writing an application name, run ID, URL, sentence, or `validated` flag. The backend requires a signed run identity issued from its application registry, an accepted identity version, an applicable requirement version, and an exact executable predicate. Missing or substituted identity fails closed.

### 3. “Does HTTP 200 mean the attack worked?”

No. The verifier checks the complete before/action/after sequence and the resulting state. Status alone does not establish a vulnerability.

### 4. “How do you stop stale or fabricated report counts?”

The backend enumerates planned scripts, authenticates receipts, derives execution counts, reclassifies results, and computes deduplication during report loading.

### 5. “Is this a sandbox, and does it replace a pentest?”

No. The harness enforces HTTP scope and captures supported clients, but it is not an operating-system sandbox. Use a disposable authorized target. FlowBusters also does not cover workflows or invariants it did not observe and support.

### 6. “Why are there two `NEEDS_REVIEW` executions but only one `NEEDS_REVIEW` finding?”

Both authenticated receipts stay in the execution log. One is the reviewable security finding. The other tested the authorized demo-reset setup path, so it is labelled **Excluded setup-path execution** and does not inflate the security-finding count.

## References

- Application identity handshake: [`backend/api/main.py` — `start_assessment`](../backend/api/main.py); [`backend/runtime/application_identity.py` — `discover_application_identity`, `issue_identity`, `validate_identity`](../backend/runtime/application_identity.py); [`backend/runtime/applications.json`](../backend/runtime/applications.json)
- State-map failure handling and isolated reanalysis: [`backend/runtime/crew_runner.py` — `run_crew`, `ArtifactWatcher.check`](../backend/runtime/crew_runner.py); [`backend/runtime/reanalyze.py` — `validate_recording_source`, `validate_destination`, `_parser`, `_run`](../backend/runtime/reanalyze.py); [`backend/runtime/ui_provenance.py` — `_validate_ui_text`, `_validate_ui_transition`](../backend/runtime/ui_provenance.py)
- Assessment entry points and progress: [`frontend/src/pages/portal.tsx` — `PortalPage.handleSubmit`](../frontend/src/pages/portal.tsx); [`frontend/src/pages/applications.tsx` — `ApplicationsPage.start`](../frontend/src/pages/applications.tsx); [`frontend/src/pages/progress.tsx` — `ProgressPage`](../frontend/src/pages/progress.tsx); [`backend/api/main.py` — `start_assessment`, `start_cross_flow`, `stream_progress`](../backend/api/main.py)
- Recording and semantic snapshots: [`backend/runtime/recorder.py` — `record`, `compact_snapshot`, `add_ui_state`, `check_scope`](../backend/runtime/recorder.py)
- Cross-flow source preparation and candidates: [`backend/runtime/application_model.py` — `collect`, `prepare_inputs`, `validate_cross_flow_candidates`](../backend/runtime/application_model.py)
- State-map and UI provenance validation: [`backend/runtime/ui_provenance.py` — `validate_state_map`, `normalize_observed_ui_rules`, `validate_rule_reference`](../backend/runtime/ui_provenance.py)
- Controlled business requirements: [`backend/runtime/business_requirements.json`](../backend/runtime/business_requirements.json); [`backend/runtime/business_requirements.py` — `load_business_requirements`, `apply_backend_requirements`](../backend/runtime/business_requirements.py); [`backend/runtime/verification.py` — `load_report`, `_classify_business_rule`](../backend/runtime/verification.py)
- Probe execution, output-contract validation, receipt precedence, partial coverage, trace verification, and counts: [`backend/runtime/probe_executor.py` — `key_for`, `preflight_script_contract`, `execute`, `validate_probe_output`, `load_receipts`, `_is_stale_unexecuted_draft`, `_partial_coverage_for`, `trace_error`, `reconcile`](../backend/runtime/probe_executor.py); [`backend/runtime/probe_capture.py` — `main`](../backend/runtime/probe_capture.py)
- Atomic verdict normalization, setup exclusion, and deduplication: [`backend/runtime/verification.py` — `load_report`, `_is_setup_path_centered`, `_dedup_key`, `_collapse_duplicate_findings`, `classify`, `_classify_business_rule`, `normalize_report`](../backend/runtime/verification.py)
- Cross-flow final count wording: [`backend/runtime/crew_runner.py` — `_cross_flow_final_message`](../backend/runtime/crew_runner.py)
- Evidence rendering: [`frontend/src/components/ReportView.tsx` — `invariantFor`, `traceViews`, `ReportView`](../frontend/src/components/ReportView.tsx)
- Local commands and ports: [`frontend/package.json` — `scripts.dev`](../frontend/package.json); [`backend/api/main.py` — `app`, `health`](../backend/api/main.py)
