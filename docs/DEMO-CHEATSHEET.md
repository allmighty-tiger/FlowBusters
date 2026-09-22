# FlowBusters demo cheatsheet

## What the new-run gate checks

The AI proposes a rule from the recording and states that it is an inference.
The backend checks its source references before executing the test. Each test
starts from an independently reset state, performs its declared actions, and reads
the final state. Confirmation means that this complete execution violated the
recorded inference; it does not mean a business owner approved that inference.
An unsupported predicate remains reviewable. A missing terminal action cannot
be replaced with a successful HTTP status.

For an isolated reanalysis, `--setup-path /api/demo/reset` authorizes the local
fixture reset independently of business logic. It does not add a business rule
or change the original recording. See `generated_probes.validate_plan_set`,
`inferred_invariants.validate_support`, and `reanalyze.prepare_reanalysis_inputs`.

## Reading an inconclusive report

A failed check does not cancel the other independent tests. Invalid plans receive
one correction attempt before execution; plans that still fail are rejected and
listed separately. COMPLETED_WITH_ERRORS means the queue reached its end but some
checks failed. FAILED means the assessment did not finish. Read planned, executed,
skipped, failed and pending counts separately. An idea with no generated probe is
an unexecuted hypothesis, not an extra finding or execution result.
Implementation: `probe_executor.execute_run`, `reconcile`, `queue_status.assessment_status`.

“All probes executed” means the requests ran and their evidence was checked.
“Needs review” means the test did not establish the claimed business behavior.
For example, an amount below a limit does not establish that duplicate records
were prevented or that submitted fields were ignored. Read the explanation and
recorded requests in that card. An unavailable action is shown as not executed.
Unverified draft recommendations are collapsed, not presented as required fixes.
Pending draft results are replaced in memory by authenticated execution results;
saved evidence is unchanged. See `probe_executor.reconcile`,
`verification._claim_coverage_error`, and `ReportView`.

## Two live runs: what to click and say

Before presenting: use Northstar version 2 and a clean initial order. Keep the
prepared refund and cancel sources available; do not record them again on stage.
Use a new name for the live price recording. Never reset or restart during probes.

### 1. Record one ordinary price workflow

**Click:** Start Assessment → target `http://localhost:3000` → perform the normal
price adjustment in the recording browser → Finish Recording → wait for the report.

**Say:** “I record a legitimate workflow. The normal adjustment is $30.
FlowBusters then tests whether requests can violate the product's payout limit.”

**Show:** the displayed verdict, submitted request body, final GET and evaluated
`totalReturned <= originalAmount`. If confirmed, read the actual values shown.
Do not promise a particular count before execution finishes.

### 2. Run cross-flow using existing sources

**Click:** Application Model → select the new eligible price run,
`demo-ready-refund-v1` and `demo-ready-cancel-v1` → confirm compatibility → Analyze.
Refund must include request and completion. No new recording browser is needed.

Only start this after the recorded-flow assessment has finished; the backend
supports one active assessment. Do not promise that both fresh runs will finish
inside the live demo window. If generation is still running, identify the saved
report below as a previous completed run and continue the walkthrough there.

**Say:** “Now I combine recorded workflows. We test whether ordinary operations
become unsafe when combined. The backend validates the sources and executes the tests.”

**If CONFIRMED:** “The backend-captured final response shows a $30 adjustment plus a
completed $100 refund: $130 returned against a $100 order.”

**If NEEDS_REVIEW:** “The report identifies what is still missing. I cannot call
this a confirmed vulnerability.” Read the specific reason, not a guessed explanation.

**Trust boundary:** “AI proposes tests. The backend checks the actual requests,
final state and independently approved rule. An AI-written finding is not proof.”

**Important wording:** call this a combined-workflow defect. Do not say that it
can only be found in cross-flow mode: ordinary probes can also execute the same
multi-endpoint sequence. Cross-flow demonstrates that the candidate was composed
from multiple recorded sources and then executed.

## Five questions you may get

1. **Did AI discover every test?** No. AI-generated probes and versioned backend
   coverage plans are labelled separately. Neither supplies an expected verdict.
2. **Why is this a violation?** The owner approved the payout cap on September 17,
   2026. The backend checks application identity, requirement version and exact
   predicate before evaluating signed state. Buttons and API fields do not establish the rule.
3. **Why more executions than vulnerabilities?** Several probes can reproduce one
   issue. All execution records remain visible; equivalent checks and grouped
   issues have separate counts. “One unique vulnerability” is report grouping,
   not proof of one internal source-code root cause.
4. **Does NOT_REPRODUCED mean safe?** Only the covered predicate held in that
   execution. A 404, pending refund or unrelated predicate cannot prove protection.
   Partial coverage and unverified candidates are not successful security checks.
5. **What do signatures prove?** Local evidence integrity, not external attestation.
   An operator controlling the artifacts and signing key is outside that guarantee.
   The local identity endpoint is a demo check, not proof of which process owns the port.

## Presenter backup — not part of the spoken script

Local validation observation, September 18, 2026: `cross-flow-f9dbb26f37e2`
completed with 6 validated candidates, 7/7 authenticated receipts and no
process/contract/trace errors. It showed 1 confirmed finding, 5 not reproduced
and 1 unique vulnerability. Two confirmed executions reproduced that same issue.
Two additional AI scripts were excluded as unverified candidate coverage.
These are historical counts, not promised results or a permanent test fixture.

The saved confirmed finding is a good cross-flow demonstration, but the same
business defect is not exclusive to cross-flow mode. Do not claim otherwise.

Prepared backup reports: `demo-ready-price-v1`, `demo-ready-refund-v1`,
`demo-ready-cancel-v1`, and the cross-flow above. If a live run fails, show the
explicit failure and identify the backup as a previous completed run.

## Local addresses and commands

- Northstar: [localhost:3000](http://localhost:3000)
- Portal: [127.0.0.1:3001](http://127.0.0.1:3001)
- Backend: [127.0.0.1:8000](http://127.0.0.1:8000)

From the FlowBusters repository root, in separate terminals:

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.api.main:app --host 127.0.0.1 --port 8000
npm --prefix frontend run dev -- --host 127.0.0.1 --port 3001 --strictPort
```

If the port is occupied, do not start a second instance. Do not restart an active
assessment. Northstar runs separately; existing compatible sources are reused.

## Technical references

- Start/Analyze: [PortalPage](../frontend/src/pages/portal.tsx),
  [ApplicationsPage](../frontend/src/pages/applications.tsx);
  [application_model.py — collect, prepare_inputs](../backend/runtime/application_model.py).
- AI plans and candidate gate:
  [generated_probes.py — compile_plans, render_plan](../backend/runtime/generated_probes.py);
  [candidate_correction.py — correct_candidates, validate_candidate](../backend/runtime/candidate_correction.py).
- Backend coverage: [coverage_plans.py — prepare_coverage_probes](../backend/runtime/coverage_plans.py).
- Identity and rule: [application_identity.py — discover_application_identity, validate_identity](../backend/runtime/application_identity.py);
  [business_requirements.py — apply_backend_requirements](../backend/runtime/business_requirements.py).
- Signed evidence and verdicts: [probe_executor.py — validate_probe_output, reconcile](../backend/runtime/probe_executor.py);
  [verification.py — normalize_report, _claim_coverage_error, _unique_vulnerabilities](../backend/runtime/verification.py).
- Detailed contracts, error handling, sampled UI provenance and limitations:
  [ARCHITECTURE.md](ARCHITECTURE.md).
- Lead-engineer review questions, trust claims and candid limitations:
  [TECHNICAL-DEFENSE.md](TECHNICAL-DEFENSE.md).
- Full 25–30 minute talk, visuals and timed fallback:
  [LUNCH-AND-LEARN.md](LUNCH-AND-LEARN.md).
- Expanded rehearsal Q&A:
  [LUNCH-AND-LEARN-QA.md](LUNCH-AND-LEARN-QA.md).
