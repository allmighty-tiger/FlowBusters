# Queue validation — local observation, 2026-09-22

This is a dated test observation, not a permanent fixture or an approved product
rule. No links to ignored run artifacts are required to read this report.

## Outcome

The original recording and `refund-completed-reanalysis-v1` were preserved.
The one authorized new assessment is `refund-completed-reanalysis-v2`.
It generated six new plans, received one bounded regeneration proposal, and
rejected three plans. A real endpoint-preflight exception initially prevented
queue admission. After fixing that exception, the same unexecuted queue was
continued through `crew_runner.execute_validated_probes`. It was not another
assessment, generation attempt, or replay of an executed script.

Final backend-derived status:

`Assessment COMPLETED_WITH_ERRORS: planned 6, executed 3, skipped 3, failed 3, pending 0`

All three actual attempts have authentic terminal receipts, exit code 0, complete
matching transport captures, and reviewable unsupported predicates. There are
zero process, executed-evidence-contract, or trace errors. The three failures
are preflight rejections, not failed HTTP attempts. No new CONFIRMED finding was
produced. The preserved v1 finding remains CONFIRMED.

## New run: one plan / one primary finding

| Planned script | Receipt execution_id or decision | Final finding | Verdict |
| --- | --- | --- | --- |
| 01_double_spend_complete_refund_twice.py | Authenticated preflight rejection; no HTTP attempt | F-001 | CHECK_ERROR |
| 02_replay_duplicate_refund_request.py | 02_replay_duplicate_refund_request-ee93abe5304d4add84f84b31a67cf919 | F-002 | NEEDS_REVIEW |
| 03_skip_step_complete_without_request.py | 03_skip_step_complete_without_request-2f22809ba58446e288afa49b0c6d97bd | F-003 | NEEDS_REVIEW |
| 04_mass_assignment_refund_request_amount.py | Authenticated preflight rejection; no HTTP attempt | F-004 | CHECK_ERROR |
| 05_pricing_tamper_complete_amount_override.py | Authenticated preflight rejection; no HTTP attempt | F-005 | CHECK_ERROR |
| 06_state_interleaving_request_refund_vs_cancel_order.py | 06_state_interleaving_request_refund_vs_cancel_order-56a7a63d7b954635a142f8b3aea2b15e | F-006 | NEEDS_REVIEW |

Six security rows = three rejected plans + three reviewable executed checks.
Three execution results = three NEEDS_REVIEW. Zero pending. There is no invented
receipt for a rejected plan. F-006's capture-free ANALYSIS draft and the
executor-created row refer to one exact script/title/receipt; normalization keeps
one primary row and preserves the extra draft under `associated_agent_drafts`.

### Actual action chains and final reads

Every executed chain independently begins with POST `/api/demo/reset` (200),
then GET `/api/order` (200), showing totalReturned=0, originalAmount=100,
refund.status=none, requestedAmount=0, completedAmount=0.

| Probe | Actions after before-read | Final GET /api/order | Why review rather than a negative verdict |
| --- | --- | --- | --- |
| 02 | POST /api/order/refund/request → 201; repeat → 409 | status=pending; requestedAmount=100; completedAmount=0; totalReturned=0; originalAmount=100 | No supported executable uniqueness/idempotency predicate. 409 alone is not the predicate. |
| 03 | POST /api/order/refund/complete → 409 | status=none; requestedAmount=0; completedAmount=0; totalReturned=0; originalAmount=100 | No supported executable workflow-order predicate. |
| 06 | POST /api/order/refund/request → 201 | status=pending; requestedAmount=100; completedAmount=0; totalReturned=0; originalAmount=100 | Cancellation endpoint was not recorded, so only one side of the proposed interleaving ran. No race or complete interleaving verdict is asserted. |

All final reads returned 200. The signed evidence is preserved; these unsupported
plans do not have an evaluated monetary invariant or a manufactured violation
boolean. Each receipt passed the transport/output gate with status `reviewable`.

### Regeneration and remaining generation defect

Plans 01, 04 and 05 declared `business_rule_must_hold` / `sum_lte` with non-
`amount_bound` claim categories. The one regeneration proposal attempted an
explicit downgrade to unsupported predicates, but its full-plan form tripped an
overly broad immutable-field guard. This orchestration defect is fixed and
regression-tested for future assessments; the rejected saved plans are not edited
or retried. Source instructions now explicitly distinguish schema `claim_type`
from attack labels and prohibit paraphrased observation values.

The new generation did not produce a valid executable amount-bound plan for the
known repeated-payout outcome. A new positive result cannot honestly be claimed
from this assessment. No second assessment was launched to hide that limitation.

## Why v1 showed seven items for six probes

F-007 was an unexecuted hypothesis with no script or receipt, not a seventh probe.
It now loads as a separate coverage note. Raw v1 files are unchanged.

| v1 script | Receipt / state | Normalized finding |
| --- | --- | --- |
| 01_double_spend_refund_complete_twice.py | 01_double_spend_refund_complete_twice-bee504bd21774f468b325e614fc743ee | F-001 CONFIRMED |
| 02_skip_step_complete_without_request.py | 02_skip_step_complete_without_request-3004f9998bda48eeb054915028b09d03; exit 1 | F-002 CHECK_ERROR |
| 03_replay_refund_request_twice.py | No receipt: old queue stopped | F-003 pending / NEEDS_REVIEW |
| 04_mass_assignment_refund_request_amount.py | No receipt: old queue stopped | F-004 pending / NEEDS_REVIEW |
| 05_pricing_tamper_refund_complete_amount.py | No receipt: old queue stopped | F-005 pending / NEEDS_REVIEW |
| 06_mass_assignment_status_fields_unsupported.py | No receipt: old queue stopped | F-006 pending / NEEDS_REVIEW |
| No planned script | No receipt | F-007 unexecuted coverage, excluded from findings |

The preserved CONFIRMED chain is reset → before GET → refund/request (201) →
refund/complete (200) → refund/complete (200) → final GET (200).
Final `order.totalReturned=200`, `order.originalAmount=100`; exact invariant:
`sum_lte(terms=[["order","totalReturned"]], limit=["order","originalAmount"])`.
Backend recomputation gives `200 > 100`, matching boolean `violation.observed=true`.
Receipt authentication, complete captures and raw supporting inference remain valid.

## Preservation checks

SHA256 of the sorted JSON map of relative Windows paths to file SHA256 values:

- refund-completed: `9e4ac5130797ff4f86f00c85f4f56de6745264937bef3c847be86d2adc5a3a7b`
- refund-completed-reanalysis-v1: `c8e1f8320123c8f61948617969dcbe173915de2b71bdf3cbbcd28d2303583121`

Both match their pre-change values. Existing scripts, receipts and keys were not
edited; no commits, pushes, installations, or second new assessment occurred.

## Verification commands and results

The repository venv launcher is broken in this environment. Tests used the
installed Python runtime with the already-installed repository site-packages;
no dependency was installed.

```powershell
$env:PYTHONPATH="$PWD;$PWD\.venv\Lib\site-packages"
& 'C:\Users\nasti\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m unittest discover -s backend/tests -v
$testFiles = @(Get-ChildItem frontend/tests -Filter *.test.cjs | ForEach-Object FullName)
node --test $testFiles
npm --prefix frontend run build
git -c safe.directory=C:/Users/nasti/Documents/FlowBustersPortal/FlowBusters diff --check
```

Full backend: 306 passed, no failures/errors/skips. Focused backend: 56 passed.
Frontend: 31 passed. Production build passed. Diff whitespace check passed.
The complete classification of the previous 19 failures and 8 errors is in
[TEST-CONTRACT-AUDIT.md](TEST-CONTRACT-AUDIT.md).
