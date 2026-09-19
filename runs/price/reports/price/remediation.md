# FlowBusters Remediation Report — DRAFT

**Target:** http://localhost:3000
**Flow:** price
**Run Date:** 2026-09-18T20:12:46Z
**Findings:** 7 (0 confirmed in this draft — all pending backend execution)

> **DRAFT STATUS.** This document is a review-only draft. Verdicts (CONFIRMED /
> NOT_REPRODUCED / CHECK_ERROR / NOT_EXECUTED) will be recomputed by the
> backend from the signed transport capture after the agent session exits.
> Each section below describes the hypothesis, the evidence shape, and the
> fix to apply **if** the probe confirms it. Do not treat any section as a
> confirmed vulnerability until the backend receipt says so.

## Summary

The `price` flow on Northstar Market exposes a monetary workflow (price
adjustment on a delivered order) plus a refund workflow (request → complete)
and a cancel workflow, all co-visible in the same UI state. The UI hides
affordances after each payout (UIR-001: Cancel disappears after adjustment;
UIR-002: Request price adjustment disappears after completion), but the
server-side enforcement is unproven. Seven probes target the highest-impact
paths: a client-trusted amount on the refund request, a replayed adjustment,
an unsolicited refund completion, a cancel with a tampered reimbursement
amount, and the full payout chain (adjustment → refund → completion). All
share one supported executable invariant: `order.totalReturned ≤
order.originalAmount` after each scenario's final GET.

## Findings

### 1. F-001 — Co-visible cancel + price-adjustment may double-reimburse

- **CWE:** CWE-841 (Improper Enforcement of Behavioral Workflow), CWE-362 (Race Condition)
- **Severity:** High
- **Source:** MUTATION_SCRIPT (`01_state_interleaving_cancel_then_price_adjustment.py`)
- **Endpoint:** POST `/api/order/cancel` + POST `/api/order/price-adjustment`
- **Issue:** UI step 3 shows `button "Cancel order"` and `button "Request
  price adjustment"` co-visible on a delivered $100 order. UIR-001 (fact
  UIR-001-F1) shows the cancel affordance disappears once a price adjustment
  has been issued. If the server allows both, the order can be reimbursed by
  cancellation **and** by price adjustment simultaneously.
- **Evidence shape:** A→B (cancel then adjust) and a concurrent race from a
  clean `POST /api/demo/reset`. Final `GET /api/order`. Invariant
  `sum_lte(order.totalReturned ≤ order.originalAmount)`.
- **Fix (if confirmed):**
  - Treat cancellation and price adjustment as mutually exclusive state
    transitions in the server's state machine: once `priceProtection.status
    == "completed"`, reject `POST /api/order/cancel` with 409 and do not
    mutate `cancellation.status`.
  - Symmetrically, once `cancellation.status != "none"`, reject
    `POST /api/order/price-adjustment`.
  - Serialize state transitions under a row lock or optimistic-concurrency
    token so concurrent cancel/adjust cannot both succeed.
  - Add an integration test that fires both concurrently against a fixture
    order and asserts `totalReturned ≤ originalAmount`.

---

### 2. F-002 — Client-supplied requestedAmount on `/api/order/refund/request`

- **CWE:** CWE-468 (Reliance on Client-Side Inputs), CWE-20 (Improper Input Validation)
- **Severity:** High
- **Source:** MUTATION_SCRIPT (`02_pricing_tamper_refund_request_amount.py`)
- **Endpoint:** POST `/api/order/refund/request` (then POST `/api/order/refund/complete`)
- **Issue:** The UI never exposes a refund-amount input. If the server reads
  `requestedAmount` from the request body instead of recomputing it from
  `order.originalAmount`, an attacker can claim any amount and then complete
  the refund to trigger the payout.
- **Evidence shape:** `POST /api/demo/reset`, `GET /api/order`, then
  `POST /api/order/refund/request` with `requestedAmount: 5000` against a
  $100 order, then `POST /api/order/refund/complete`, then final `GET
  /api/order`. Invariant `sum_lte(order.totalReturned ≤ order.originalAmount)`.
- **Fix (if confirmed):**
  - Ignore `requestedAmount` in the request body. Compute the refundable
    amount server-side as `originalAmount - priceProtection.adjustmentAmount
    - cancellation.reimbursementAmount`.
  - Validate `requestedAmount ≤ refundable_amount` and clamp to that value
    before writing `refund.requestedAmount`.
  - Reject the request with 422 if the client amount exceeds the
    server-computed refundable amount by more than a small epsilon.

---

### 3. F-003 — Replaying POST `/api/order/price-adjustment` after completion

- **CWE:** CWE-841, CWE-307 (Improper Restriction of Excessive Authentication Attempts / Replay)
- **Severity:** High
- **Source:** MUTATION_SCRIPT (`03_replay_price_adjustment.py`)
- **Endpoint:** POST `/api/order/price-adjustment`
- **Issue:** UIR-002 (facts F1-F3) shows the UI affordance and the
  `availableActions.requestPriceAdjustment` flag both flip to false after
  `priceProtection.status` becomes `completed`. If the server does not
  re-check this state on every POST, a replayed request re-issues the $30
  adjustment. Two issuances ($60) stay under the $100 cap, but a third or a
  replay combined with a refund can exceed it.
- **Evidence shape:** `POST /api/demo/reset`, `GET /api/order`, then the same
  bodyless `POST /api/order/price-adjustment` twice, then final `GET
  /api/order`. Invariant `sum_lte(order.totalReturned ≤ order.originalAmount)`.
- **Fix (if confirmed):**
  - Make the adjustment idempotent on the server: if
    `priceProtection.status == "completed"`, return 200 with the existing
    `adjustmentAmount` (no state mutation) or 409.
  - Use a unique constraint on `priceProtection.completedAt` (or a
    `priceProtection.lockedAt` token) so a second issuance cannot write a
    second payout row.
  - Add an integration test that replays the adjustment 5 times and asserts
    `totalReturned == adjustmentAmount` exactly once.

---

### 4. F-004 — POST `/api/order/refund/complete` without a prior request

- **CWE:** CWE-841
- **Severity:** Medium
- **Source:** MUTATION_SCRIPT (`04_skip_step_refund_complete.py`)
- **Endpoint:** POST `/api/order/refund/complete`
- **Issue:** The UI only exposes refund completion when `refund.status !=
  "none"` (`availableActions.completeRefund=false` in the baseline). A
  direct call to `/refund/complete` on a fresh order with no prior request
  should be rejected; if it pays out, the workflow prerequisite is not
  enforced server-side.
- **Evidence shape:** `POST /api/demo/reset`, `GET /api/order` (baseline
  shows `refund.status=none`), then `POST /api/order/refund/complete` with
  `{"order_id": "1042"}`, then final `GET /api/order`. Invariant
  `sum_lte(order.totalReturned ≤ order.originalAmount)`.
- **Fix (if confirmed):**
  - In the completion handler, require `refund.status == "pending"` (or
    `"requested"`) before transitioning to `"completed"`; otherwise return
    409.
  - Validate that `refund.requestedAmount` is set before completing.
  - Add an integration test that calls `/refund/complete` on a fresh order
    and asserts 409 and `totalReturned == 0`.

---

### 5. F-005 — Price adjustment + completed refund chain exceeds originalAmount

- **CWE:** CWE-841
- **Severity:** High
- **Source:** MUTATION_SCRIPT (`05_state_interleaving_adjust_then_refund.py`)
- **Endpoint:** POST `/api/order/price-adjustment` → POST `/api/order/refund/request` → POST `/api/order/refund/complete`
- **Issue:** Composes the full recorded monetary workflow. If the server does
  not recompute the payout cap against `originalAmount` at each payout step,
  the $30 adjustment plus a full $100 refund can push `totalReturned` to
  $130, exceeding the $100 cap.
- **Evidence shape:** `POST /api/demo/reset`, `GET /api/order`, then the
  three payout steps, then final `GET /api/order`. Invariant
  `sum_lte(order.totalReturned ≤ order.originalAmount)`.
- **Fix (if confirmed):**
  - Centralize payout accounting: every payout (adjustment, cancellation
    reimbursement, refund completion) must check `current_totalReturned +
    this_payout ≤ originalAmount` before writing.
  - Refuse the payout (or clamp to the remaining refundable amount) when
    the cap would be exceeded.
  - Add an integration test that runs adjustment → refund → completion on a
    $100 fixture and asserts `totalReturned ≤ 100`.

---

### 6. F-006 — Privileged fields in refund request body

- **CWE:** CWE-915 (Improper Use of the Privileged Method), CWE-639 (Authorization Bypass Through User-Controlled Key)
- **Severity:** Medium
- **Source:** MUTATION_SCRIPT (`06_mass_assignment_refund_request.py`)
- **Endpoint:** POST `/api/order/refund/request`
- **Issue:** The probe sends `role`, `is_admin`, `status`, `balance`, and
  `verified` in addition to the normal refund fields. A server that
  whitelists only the expected fields ignores these; one that mass-assigns
  may persist them and leak privilege.
- **Evidence shape:** `POST /api/demo/reset`, `GET /api/order`, then
  `POST /api/order/refund/request` with the privileged fields, then final
  `GET /api/order`. The script's `leaked_fields_observed` field reports which
  (if any) privileged fields stuck on the AFTER GET.
- **Predicate:** `unsupported_business_rule` — a field-absence /
  privilege-persistence check cannot be expressed as `sum_lte`; the result
  stays NEEDS_REVIEW pending human review of the script's stdout.
- **Fix (if confirmed):**
  - Use a strict request schema (e.g., Pydantic with `extra="forbid"`, or
    an explicit allow-list of field names) on `/api/order/refund/request`.
  - Never map arbitrary body keys to ORM model attributes.
  - Add an integration test that sends privileged fields and asserts the
    AFTER GET does not contain them.

---

### 7. F-007 — Client-supplied reimbursementAmount on `/api/order/cancel`

- **CWE:** CWE-468, CWE-20
- **Severity:** High
- **Source:** MUTATION_SCRIPT (`07_data_tamper_cancel_reimbursement.py`)
- **Endpoint:** POST `/api/order/cancel`
- **Issue:** The UI never exposes a reimbursement amount. If the server reads
  `reimbursementAmount` from the request body, an attacker can claim any
  amount on the cancel call.
- **Evidence shape:** `POST /api/demo/reset`, `GET /api/order`, then
  `POST /api/order/cancel` with `reimbursementAmount: 10000`, then final
  `GET /api/order`. Invariant `sum_lte(order.totalReturned ≤
  order.originalAmount)`.
- **Fix (if confirmed):**
  - Ignore `reimbursementAmount` in the request body. Compute the
    cancellation reimbursement server-side (e.g., `originalAmount -
    priceProtection.adjustmentAmount` when the order is in a
    cancellable state).
  - Reject the request with 422 if the client amount differs from the
    server-computed value beyond epsilon.

---

## Next Steps

1. **Wait for the backend execution receipts.** Each script is run once by
   the backend with a 30-second timeout; the backend signs the transport
   trace and recomputes the `sum_lte` invariants from the AFTER captures.
2. **Review CONFIRMED findings** in the order above (F-001, F-002, F-005,
   F-003, F-007 are the highest-impact monetary workflows).
3. **Re-run after fixes.** Each fix should be covered by an integration
   test that asserts the same `sum_lte` invariant the probe uses.
4. **F-006 (MASS_ASSIGNMENT)** will remain NEEDS_REVIEW unless the backend
   adds a supported field-absence predicate; a human should inspect the
   script's `leaked_fields_observed` stdout field directly.