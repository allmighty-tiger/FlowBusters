# FlowBusters Remediation Report

**Target:** http://localhost:3000/
**Flow:** refund
**Run Date:** 2026-09-18T20:47:34Z
**Status:** DRAFT — verdicts are pending backend execution of the 8 mutation scripts.
**Findings:** 8 (0 confirmed, 8 NEEDS_REVIEW). All findings target the monetary
invariant `order.totalReturned <= order.originalAmount` and the lifecycle rule that
refund completion requires a prior request. The backend recomputes each `sum_lte`
invariant from the signed transport trace and sets the final verdict.

## Summary

The recorded flow is a single happy path (request refund → complete refund) on an
unauthenticated single-order demo fixture with no login endpoint. The probes therefore
focus on **business-logic / state-machine** flaws that the happy path does not expose:
double reimbursement, payout replay, skipping the pending-refund gate, and client-trusted
money amounts. These are draft hypotheses; none is confirmed until the backend executes
the scripts. No authentication finding is raised because no credential endpoint exists.

## Findings

### 1. Replayed refund completion pays out twice (F-001)

- **CWE:** CWE-841 (Improper Enforcement of Behavioral Workflow), CWE-294 (Capture-Replay)
- **Severity:** High
- **Source:** MUTATION_SCRIPT (`01_replay_complete_refund.py`, REPLAY_ATTACK)
- **Endpoint:** POST /api/order/refund/complete
- **Issue:** After a refund is completed (terminal state), the same payout-completion
  call is replayed. If the server lacks idempotency on the payout, the customer is paid
  a second time.
- **Fix:**
  - Make `refund/complete` idempotent on the refund's lifecycle: reject (or no-op) when
    `refund.status == "completed"`.
  - Guard the payout with a conditional state transition (`pending -> completed`) enforced
    in a single atomic operation / row lock.
  - Enforce the invariant `totalReturned <= originalAmount` server-side before any money
    moves; reject any completion that would exceed it.

### 2. Cancel order + complete refund double-reimburse (F-002)

- **CWE:** CWE-841
- **Severity:** High
- **Source:** MUTATION_SCRIPT (`02_state_interleaving_cancel_refund.py`, STATE_INTERLEAVING)
- **Endpoint:** POST /api/order/cancel + POST /api/order/refund/complete
- **Issue:** Cancellation reimbursement and a completed refund are two forms of the same
  reimbursement. Both orderings (refund→cancel, cancel→refund) and a concurrent race are
  tested; if both apply, the customer is reimbursed more than the original amount.
- **Fix:**
  - Treat cancellation and refund as mutually exclusive reimbursements on a shared
    budget equal to `originalAmount`; the second form must fail once the budget is met.
  - Serialize competing state changes (row lock / optimistic concurrency) so a race
    cannot apply both.

### 3. Second refund request on a completed order (F-003)

- **CWE:** CWE-841
- **Severity:** High
- **Source:** MUTATION_SCRIPT (`03_replay_request_refund_after_complete.py`, REPLAY_ATTACK)
- **Endpoint:** POST /api/order/refund/request
- **Issue:** After a refund is completed, a second refund request is created and
  completed. The server should refuse to open a second payout on a fully reimbursed order.
- **Fix:** Reject `refund/request` when `refund.status == "completed"` or
  `totalReturned >= originalAmount`.

### 4. Completing a refund without a prior request (F-004)

- **CWE:** CWE-841, CWE-425 (Direct Request / Forced Browsing)
- **Severity:** Medium
- **Source:** MUTATION_SCRIPT (`04_forced_browse_complete_refund.py`, FORCED_BROWSING)
- **Endpoint:** POST /api/order/refund/complete
- **Issue:** The UI gates "Complete refund" behind a refund request (observed rule UIR-002).
  Calling the completion endpoint directly with `refund.status == "none"` should be
  rejected by the server, not only hidden in the UI.
- **Fix:** Require `refund.status == "pending"` as a precondition for `refund/complete`;
  otherwise return 409/400.

### 5. Client-inflated refund amount (F-005)

- **CWE:** CWE-472 (Untrusted Price Computation), CWE-20 (Improper Input Validation)
- **Severity:** Medium
- **Source:** MUTATION_SCRIPT (`05_pricing_tamper_refund_amount.py`, PRICING_TAMPER)
- **Endpoint:** POST /api/order/refund/request (+ /refund/complete)
- **Issue:** The recorded request was bodyless (amount derived server-side). A tampered
  body trying to inflate `requestedAmount`/`completedAmount` must not change the payout.
- **Fix:** Ignore any client-supplied amount fields; always compute the refund from the
  order's stored `originalAmount`. Re-derive and cap at the original amount server-side.

### 6. Price adjustment + refund exceeds the original amount (F-006)

- **CWE:** CWE-472
- **Severity:** Medium
- **Source:** MUTATION_SCRIPT (`06_pricing_tamper_price_adjustment.py`, PRICING_TAMPER)
- **Endpoint:** POST /api/order/price-adjustment (+ refund chain)
- **Issue:** An inflated price adjustment followed by a full refund could push cumulative
  reimbursement past the original amount. (Endpoint not exercised in the recording.)
- **Fix:** Apply adjustments and refunds against a shared `originalAmount` budget; enforce
  `totalReturned <= originalAmount` across both; recompute the adjustment server-side
  from the price delta rather than trusting the request body.

### 7. Client-inflated cancellation reimbursement (F-007)

- **CWE:** CWE-20, CWE-472
- **Severity:** Medium
- **Source:** MUTATION_SCRIPT (`07_data_tamper_cancel_reimbursement.py`, DATA_TAMPER)
- **Endpoint:** POST /api/order/cancel
- **Issue:** A tampered cancel body trying to set a huge `reimbursementAmount`/status must
  not be honored. (Endpoint not exercised in the recording.)
- **Fix:** Compute the cancellation reimbursement server-side from the order; ignore or
  validate any client-supplied amount/status; cap by `originalAmount`.

### 8. Concurrent identical refund requests (F-008)

- **CWE:** CWE-362 (Race Condition), CWE-841
- **Severity:** Medium
- **Source:** MUTATION_SCRIPT (`08_double_spend_concurrent_refund.py`, DOUBLE_SPEND)
- **Endpoint:** POST /api/order/refund/request
- **Issue:** Racing multiple identical refund requests could create several pending
  refunds that, once completed, stack past the original amount.
- **Fix:** Enforce a single active/pending refund per order (unique constraint or atomic
  check-and-set); make `refund/request` idempotent so concurrent duplicates collapse to
  one.

---

*Note: Because the app exposes no login endpoint and the recording has no authenticated
principal, no authentication finding (CWE-287) is emitted; per scope the `auth_check`
is recorded as `performed: false`. Endpoints marked "not exercised in the recording"
(`/api/order/cancel`, `/api/order/price-adjustment`) are backend-contract targets inferred
from the co-visible UI controls; if the runtime returns 404/405, those scenarios are
NOT_EXECUTED (incomplete coverage), not evidence the control held.*