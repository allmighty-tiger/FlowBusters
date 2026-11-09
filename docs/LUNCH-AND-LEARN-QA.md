# FlowBusters: technical defense and Q&A

Presenter preparation, 19 September 2026. Companion to [the talk and demo plan](LUNCH-AND-LEARN.md). These answers describe the inspected working tree, not a production-security guarantee. Start with the short answer; use the detail only when asked. Source references are for rehearsal, not spoken narration.

## 1. What is the engineering contribution, beyond asking an LLM to find bugs?

**Say:** “The experiment connects recorded workflow context to executable hypotheses, then evaluates their evidence independently. The model proposes a test; it does not get to decide that the test passed.”

**Detail:** There are separate gates for recording completeness, state-map provenance, cross-flow source references, probe contracts, transport evidence and verdict normalization. The current demo also contains explicit backend coverage plans. Therefore a successful Northstar result is not, by itself, evidence of novel AI discovery.

**Code:** `crew_runner.run_crew` in [crew_runner.py](../backend/runtime/crew_runner.py), `compile_plans` in [generated_probes.py](../backend/runtime/generated_probes.py), `normalize_report` in [verification.py](../backend/runtime/verification.py), [coverage_plans.py](../backend/runtime/coverage_plans.py).

## 2. Why not use property-based testing or a finite-state machine?

**Say:** “Those are complementary and more deterministic when the model and properties already exist. I am exploring whether recordings can reduce the work of identifying useful sequences and drafting tests.”

**Detail:** The application state model is an agent-generated artifact, not an exhaustively explored formal transition system. There is no proof of state-space completeness. A worthwhile next evaluation is the incremental coverage over hand-authored stateful tests, with the same time budget and known defects.

**Code:** `collect` and `prepare_inputs` in [application_model.py](../backend/runtime/application_model.py); [Analyst charter](../crew/agents/analyst/charter.md).

## 3. What did UI context add that HAR did not contain?

**Say:** “HAR shows requests and responses. UI samples add what the user could see: labels, available actions and changes in visible state. Neither source alone establishes every business rule.”

**Detail:** The recorder compacts accessibility snapshots into bounded semantic strings and a sampled timeline. It already had initial/final snapshot support before commit `b98c15c`; that commit expanded workflow context. Do not claim the project first acquired Playwright at that point.

**Code:** `compact_snapshot`, `add_ui_state` and `record` in [recorder.py](../backend/runtime/recorder.py).

## 4. Can you prove which click caused a request?

**Say:** “Not exactly from the current recording. UI snapshots are sampled, not instrumented per click. We use ordering and semantics as context, not a guaranteed causal join.”

**Detail:** Equal snapshots can be merged, short-lived states can be missed, and DOM references are stripped. The default sample interval is about one second. Cross-flow source-local UI step numbers require the source run as well as the pointer. Execution traces give a separate ordered record of what the probe sent; that does not retroactively make recording-time click attribution exact.

**Code:** `record` and `add_ui_state` in [recorder.py](../backend/runtime/recorder.py); provenance resolution in [ui_provenance.py](../backend/runtime/ui_provenance.py).

## 5. Where does the financial rule come from?

**Say:** “The test application's owner approved a product requirement: total compensation for an order must not exceed its original amount. It is registered in the backend independently of the probe.”

**Detail:** The current rule is `NSM-TOTAL-RETURN-CAP-2026-09-17`. Its predicate is exactly `sum_lte` with the term `order.totalReturned` and limit `order.originalAmount`. Button availability, an API capability flag and arithmetic are different things: the first two describe observations; arithmetic evaluates a condition; the requirement makes that condition normative.

**Code:** [business_requirements.json](../backend/runtime/business_requirements.json), `load_business_requirements` and `apply_backend_requirements` in [business_requirements.py](../backend/runtime/business_requirements.py).

## 6. How do you know the application is really Northstar?

**Say:** “For this controlled local demo, we check an identity endpoint against a backend registry and the permitted origin. That is a demo identification mechanism, not cryptographic proof of the process on the port.”

**Detail:** Another server can imitate the endpoint. The local HMAC protects a saved identity against later changes by someone without the key; it cannot correct a false identity assignment at discovery. Stronger deployment identity would be required before using this mechanism across untrusted targets.

**Code:** `discover_application_identity`, `issue_identity` and `validate_identity` in [application_identity.py](../backend/runtime/application_identity.py); [applications.json](../backend/runtime/applications.json).

## 7. What does a receipt actually prove?

**Say:** “It ties a local execution record to the backend that recorded it and lets the loader detect changes. It is not external attestation, and it is not proof against the machine's operator.”

**Detail:** The record includes the executed script identity, process outcome, parsed result and transport evidence. The loader verifies its HMAC and checks captures against that evidence. Someone controlling both artifacts and the signing key is outside this integrity boundary. The capture harness is also not a hardened sandbox for hostile Python.

**Code:** `execute`, `load_receipts` and `trace_error` in [probe_executor.py](../backend/runtime/probe_executor.py); [probe_capture.py](../backend/runtime/probe_capture.py).

## 8. Why are findings and remediation written before execution?

**Say:** “They are drafts produced during hypothesis generation. The report loader combines them with backend execution evidence to produce the final view.”

**Detail:** An authenticated receipt can replace stale pre-execution verification in the normalized view. Contradictory executed evidence must still fail validation. Draft files existing on disk are not a successful assessment, and process exit code zero is not a successful security check.

**Code:** `run_crew` in [crew_runner.py](../backend/runtime/crew_runner.py), execution/reconciliation in [probe_executor.py](../backend/runtime/probe_executor.py), `load_report` in [verification.py](../backend/runtime/verification.py).

## 9. How do you prevent the agent from hallucinating success?

**Say:** “We don't accept its boolean on trust. The backend checks the capture contract and recalculates the supported invariant from that scenario's final response.”

**Detail:** Captures must correspond to transport sequences, methods, URLs, bodies, statuses and responses. Missing requests and contradictory arithmetic are errors. Each independently supported supplementary invariant uses its own AFTER state; values must not be combined across resets. This validates a supported predicate, not every possible meaning of the agent's natural-language hypothesis.

**Code:** `trace_error` and probe-output validation in [probe_executor.py](../backend/runtime/probe_executor.py); `validate_plan` and `render_plan` in [generated_probes.py](../backend/runtime/generated_probes.py).

## 10. What do the verdicts mean, precisely?

**Say:** “Confirmed means the supported check violated an applicable requirement with valid evidence. Not reproduced means the tested predicate did not reproduce the claim. Needs review means the evidence or semantic coverage is insufficient for that conclusion.”

**Detail:** For the monetary business-rule path, a complete negative execution does not need a positive-rule promotion. But a convenient numerical bound cannot disprove mass assignment or a workflow gate. Invalid evidence is not rescued by a successful HTTP status. There are also authentication/deletion classification paths; do not generalize the monetary rule implementation to every vulnerability class.

**Code:** `classify`, `_classify_business_rule`, `_claim_coverage_error` and `_endpoint_coverage_error` in [verification.py](../backend/runtime/verification.py).

## 11. Is claim-to-predicate matching fully solved?

**Say:** “No. The implementation catches specific mismatches, but it is not a general proof that arbitrary prose and a predicate mean the same thing.”

**Detail:** Structured plans restrict supported monetary checks. The normalizer also has targeted semantic checks for incomplete refund chains and amount-tamper claims. A pending refund plus zero payout does not prove that a completed-refund scenario is safe. Unsupported predicates should remain reviewable, rather than being replaced with an unrelated financial limit.

**Code:** `validate_plan` in [generated_probes.py](../backend/runtime/generated_probes.py); `_claim_coverage_error` in [verification.py](../backend/runtime/verification.py).

## 12. Is the demonstrated overpayment uniquely cross-flow?

**Say:** “It is a combined-workflow failure: price adjustment followed by completed refund. In this implementation it is not exclusive to cross-flow mode; ordinary-run probes have also executed that sequence.”

**Detail:** Cross-flow mode explicitly assembles context from multiple authenticated source recordings. Ordinary probes are not confined to one endpoint and can discover or exercise combinations too. The saved cross-flow report demonstrates composition and evidence handling, not an experimentally established advantage over single-flow discovery.

**Code:** `collect` and `prepare_inputs` in [application_model.py](../backend/runtime/application_model.py); `validate_candidate` in [candidate_correction.py](../backend/runtime/candidate_correction.py). See the dated read-only report comparison in [the demo plan](LUNCH-AND-LEARN.md).

## 13. What makes a cross-flow candidate valid?

**Say:** “It needs real references to at least two different authenticated sources and an executable contract that matches the probe. Adding a second source name is not enough.”

**Detail:** Candidate validation checks source HAR entries against backend snapshots and their manifest. The script contract includes actions, request bodies and invariant. Binding is not by file order or similar name. Corrections are bounded, preserve source evidence and original hypotheses, and must validate before execution.

**Code:** `validate_candidate` and `correct_candidates` in [candidate_correction.py](../backend/runtime/candidate_correction.py); `binding_contract` in [generated_probes.py](../backend/runtime/generated_probes.py).

## 14. Why seven executions but six findings and one vulnerability?

**Say:** “Those are three units. Executions are attempts, findings represent tested chains after deduplication, and vulnerabilities are the report's grouping of confirmed product issues.”

**Detail:** In the saved demo, two executions support the same confirmed chain, so both receipts remain while the primary finding is deduplicated. Finding deduplication compares actions and invariant. Product-issue grouping is a broader heuristic and is not proof of shared implementation root cause. Always name the unit when quoting a count.

**Code:** `_dedup_key`, `_collapse_duplicate_findings`, `_unique_vulnerabilities` and `normalize_report` in [verification.py](../backend/runtime/verification.py).

## 15. Are these race conditions? What about asynchronous payments?

**Say:** “The demonstrated adjustment/refund failure needs only sequential requests. It does not prove a race condition.”

**Detail:** Northstar records compensation in an in-memory ledger. A real payment system may acknowledge a request before settlement, so an immediate GET may not be a sufficient business observation. A production extension needs explicit terminal-state polling and a settlement oracle. Do not claim that HTTP 200 establishes final payment or that concurrent client launch proves server-side overlap.

**Code:** The trace/timing checks are in `trace_error` in [probe_executor.py](../backend/runtime/probe_executor.py). Northstar's `order-store.js` implements the demo ledger; the file is outside this repository, at `C:\Users\nasti\Documents\northstar-market\order-store.js`.

## 16. How do reset and shared state affect the evidence?

**Say:** “Reset creates a clean test precondition; it is not an attack step. Every scenario must be evaluated within its own reset boundary.”

**Detail:** Reset requests remain in transport evidence even when hidden from the primary report narrative. The controlled demo uses one shared order, so overlapping assessments or manual changes can contaminate state. We do not have a general causal-isolation proof for unrelated concurrent users. Rehearse on the local fixture and never reset while an assessment is using it.

**Code:** `render_plan` in [generated_probes.py](../backend/runtime/generated_probes.py), trace validation in [probe_executor.py](../backend/runtime/probe_executor.py), setup filtering in [ReportView.tsx](../frontend/src/components/ReportView.tsx).

## 17. Can this test multi-user authorization and scale to a large application?

**Say:** “The code has some authentication-oriented checks, but this demo does not validate multi-user isolation or large-scale coverage.”

**Detail:** Cross-flow collection currently accepts a bounded set of sources, not an entire enterprise graph. Sessions, actor identity, asynchronous effects and state-space growth need separate evaluation. Do not imply support for all tenants and roles from a one-order local example.

**Code:** `_classify_auth` in [verification.py](../backend/runtime/verification.py); `collect` and source-size limits in [application_model.py](../backend/runtime/application_model.py).

## 18. What is the security boundary around generated code and secrets?

**Say:** “This is a trusted-operator local POC. Scope restrictions and transport capture are useful controls, but they are not a hardened execution sandbox or a data-loss prevention system.”

**Detail:** The worker runs generated Python locally, and the agent process has broad file access. Raw HAR can contain sensitive material despite minimization instructions. A production design needs isolated workers, explicit egress policy, credential separation, artifact access controls and redaction validated outside the prompt. Use synthetic data for this presentation.

**Code:** `run_crew` in [crew_runner.py](../backend/runtime/crew_runner.py), `execute` in [probe_executor.py](../backend/runtime/probe_executor.py), [probe_capture.py](../backend/runtime/probe_capture.py).

## 19. How does this compare with DAST, and what would convince you it works?

**Say:** “I have not run a comparative benchmark. My hypothesis is that workflow context helps generate useful stateful tests. That needs measurement, not a successful demo alone.”

**Detail:** Compare AI-only, backend-plan-only, HAR-only and semantic-context-assisted runs against the same seeded and nonseeded cases. Measure distinct valid chains, false confirmation, unsupported claims, reproducibility, generation latency and operator corrections. Keep backend coverage results separate so guaranteed demo cases do not inflate the measured AI contribution.

**Code:** Generation provenance and counts in `normalize_report` in [verification.py](../backend/runtime/verification.py); [coverage_plans.py](../backend/runtime/coverage_plans.py). These proposed experiments are not reported benchmark results.

## 20. This was AI-assisted coding. What do you personally stand behind?

**Say:** “I built this as a personal POC outside my normal work. AI helped implement it. I can explain the tested evidence path, the trust boundaries and the limitations; I am not presenting every generated line as production-reviewed.”

**Detail:** Show one plan, its actual requests, the final GET and the independent requirement. Explain what the tests cover and what they cannot establish. Be candid about current presentation defects: the confirmed-card explanation currently assumes a combined price/refund sequence even for some other confirmed scenarios. Do not defend misleading text; distinguish it from the underlying validated trace and flag it as a UI defect.

**Code:** `render_plan` in [generated_probes.py](../backend/runtime/generated_probes.py), `trace_error` in [probe_executor.py](../backend/runtime/probe_executor.py), `apply_backend_requirements` in [business_requirements.py](../backend/runtime/business_requirements.py), [ReportView.tsx](../frontend/src/components/ReportView.tsx).

## Three safe phrases when the question goes beyond the evidence

- “That is outside what this experiment has validated.”
- “The current code implements this narrower check; I would need a separate test for your case.”
- “That is a limitation, not something the model or the receipt signature solves.”

Do not use these to avoid a question the artifacts can answer. Use the execution trace for factual questions, the registry for the normative rule, and the source for implementation claims.
