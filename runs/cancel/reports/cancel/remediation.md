# FlowBusters Remediation Report

**Target:** http://localhost:3000/
**Application:** Northstar Market (`northstar-market`, identity v2, requirements 2026-09-17)
**Flow:** cancel
**Run Date:** 2026-09-18T20:20:00Z
**Status:** Draft (review-only). Findings are `NEEDS_REVIEW` until the backend executes
each mutation script and recomputes the `sum_lte` invariant from signed transport
evidence. `violation.observed = false` → control held (NOT_REPRODUCED);
`violation.observed = true` → control failed (CONFIRMED).

## Summary

8 adversarial probes target the order #1042 payout state machine, which has three
co-visible payout paths — **cancellation reimbursement ($100)**, **price-protection
adjustment ($30)**, and **refund completion** — all reachable without any
authentication. The central business rule under test is the Northstar monetary cap:

> `order.totalReturned` must never exceed `order.originalAmount` (100 > 100 = false).

The most severe class is **stacked/over reimbursement**: any combination in which two
payouts (or a tampered amount) land on the same order so the customer is refunded
more than they paid. A second, unverified coverage gap is the **absence of any
authentication/authorization** on the payout endpoints (F-009).

## Invariant under test

```json
{"operator": "sum_lte", "terms": [["order", "totalReturned"]], "limit": ["order", "originalAmount"]}
```

Evaluated against each scenario's own AFTER `GET /api/order` (fresh state via
`POST /api/demo/reset` before every scenario).

## Findings

### 1. Cancel + refund-complete interleaving (F-001)

- **CWE:** CWE-841: Improper Enforcement of Behavioral Workflow
- **Severity:** High
- **Source:** MUTATION_SCRIPT — `01_state_interleaving_cancel_refund_complete.py`
- **Endpoint:** POST `/api/order/cancel`, POST `/api/order/refund/complete`
- **Issue:** Cancel issues a $100 reimbursement; refund/complete issues a separate
  payout. If both succeed on the same order (either ordering, or a concurrent race),
  totalReturned can exceed originalAmount.
- **Verification rule:** observed_ui UIR-001 (facts UIR-001-F1, UIR-001-F2).
- **Fix:**
  - Enforce a single terminal payout: once `cancellation.status=completed`, reject
    `refund/complete`, and vice-versa.
  - Make total-return cumulative-capped: `totalReturned = min(requested, originalAmount - alreadyReturned)`.
  - Serialize per-order payout mutations (row lock / optimistic concurrency) to close the race window.

### 2. Refund/complete without a prior request and without auth (F-002)

- **CWE:** CWE-841, CWE-862: Missing Authorization
- **Severity:** High
- **Source:** MUTATION_SCRIPT — `02_skip_step_refund_complete.py`
- **Endpoint:** POST `/api/order/refund/complete`
- **Issue:** `completeRefund` is only enabled after a refund is requested, but the
  endpoint is directly callable with no auth and no prior `refund/request`.
- **Fix:**
  - Require `refund.status ∈ {requested, pending}` before completing; otherwise 409.
  - Add an authentication/authorization gate (see F-009).

### 3. Cancel honors client-supplied reimbursement amount (F-003)

- **CWE:** CWE-464: Assignment of Effective Rights / CWE-20: Improper Input Validation
- **Severity:** High
- **Source:** MUTATION_SCRIPT — `03_pricing_tamper_cancel_reimbursement.py`
- **Endpoint:** POST `/api/order/cancel`
- **Issue:** The UI sends a bodyless cancel with a fixed $100 reimbursement (UIR-002).
  A JSON body with `reimbursementAmount=1000` tests whether the server trusts the
  client amount.
- **Fix:** Never read the reimbursement amount from the request; always compute it
  server-side from `order.originalAmount`.

### 4. Replayed cancel issues a second reimbursement (F-004)

- **CWE:** CWE-294: Authentication Bypass by Capture-replay
- **Severity:** High
- **Source:** MUTATION_SCRIPT — `04_replay_attack_cancel_twice.py`
- **Endpoint:** POST `/api/order/cancel`
- **Issue:** Re-sending the identical cancel (sequentially or concurrently) may issue
  the $100 reimbursement twice.
- **Fix:** Make cancel idempotent keyed on `order.id` + transition; reject
  `cancelOrder` when `orderStatus` is already `canceled` (409) without re-paying.

### 5. Price adjustment stacks on a completed refund (F-005)

- **CWE:** CWE-841
- **Severity:** High
- **Source:** MUTATION_SCRIPT — `05_state_interleaving_adjustment_refund.py`
- **Endpoint:** POST `/api/order/price-adjustment`, `/api/order/refund/request`, `/api/order/refund/complete`
- **Issue:** The $30 adjustment and the refund payout chain can both add to
  totalReturned; combined they may exceed originalAmount.
- **Fix:** Apply the same cumulative cap to price-protection adjustments as to
  refunds; block the adjustment once a payout has completed.

### 6. Refund request accepts oversized requestedAmount (F-006)

- **CWE:** CWE-20, CWE-464
- **Severity:** Medium
- **Source:** MUTATION_SCRIPT — `06_data_tamper_refund_requested_amount.py`
- **Endpoint:** POST `/api/order/refund/request`
- **Issue:** `requestedAmount=99999` tests whether the request amount is clamped. The
  payout cap is checked via the invariant; the request-clamp rule itself is reported
  as `unsupported_business_rule` (a payout cap can mask a large accepted
  requestedAmount).
- **Fix:** Clamp `requestedAmount` to `min(requested, originalAmount - alreadyReturned)`
  at request time and reject negatives.

### 7. Adjustment pays out on a canceled/reimbursed order (F-007)

- **CWE:** CWE-841
- **Severity:** Medium
- **Source:** MUTATION_SCRIPT — `07_state_interleaving_cancel_adjustment.py`
- **Endpoint:** POST `/api/order/cancel`, `/api/order/price-adjustment`
- **Issue:** After cancellation (order terminal, reimbursed), the $30 adjustment may
  still apply, pushing totalReturned over originalAmount. Contradicts UIR-003
  ("price protection is not available while another order request is active").
- **Fix:** Gate the adjustment endpoint on `orderStatus`; reject when `canceled`.

### 8. Price adjustment on an already-canceled order (F-008)

- **CWE:** CWE-841
- **Severity:** Medium
- **Source:** MUTATION_SCRIPT — `08_skip_step_price_adjustment_after_cancel.py`
- **Endpoint:** POST `/api/order/price-adjustment`
- **Issue:** Direct terminal-state bypass of the removed "Request price adjustment"
  button (UIR-001). If the server applies the adjustment to a canceled order, it
  over-reimburses.
- **Verification rule:** observed_ui UIR-001 (facts UIR-001-F1, UIR-001-F2).
- **Fix:** Server-side lifecycle guard identical to F-007.

### 9. No authentication on payout endpoints (F-009)

- **CWE:** CWE-306: Missing Authentication for Critical Function, CWE-352: Cross-Site Request Forgery
- **Severity:** Medium (unverified coverage gap)
- **Source:** ANALYSIS
- **Endpoint:** all of `/api/order/cancel`, `/api/order/refund/request`, `/api/order/refund/complete`, `/api/order/price-adjustment`
- **Issue / Execution:** `NOT_EXECUTED`. The recording and catalog show no login
  endpoint, no session/token, and no per-user order ownership (single order #1042).
  All payout actions ran unauthenticated and returned 200. Per the contract this is
  **not** confirmed as a vulnerability because `scope.json` does not set
  `authentication_required: true` and no second principal exists to demonstrate broken
  object-level access control.
- **Fix / Next step:**
  - Confirm with the product owner whether authentication/authorization is in scope.
  - If it is: add an auth boundary + CSRF protection to all state-changing endpoints,
    scope every action to the authenticated principal's owned order, and re-probe with
    a second, non-owning user to demonstrate ownership enforcement server-side.

---
*Backend-owned coverage scripts (`backend_coverage/cancel/`) are generated separately
and are not part of this AI draft. Verdicts are recomputed by the backend from signed
transport evidence after this session.*