# FlowBusters Remediation Report

**Target:** http://localhost:3000
**Flow:** cross-flow-c5239cec0713
**Application:** Northstar Market (northstar-market, identity v2, requirements 2026-09-17)
**Analysis Source:** CROSS_FLOW
**Source Runs:** price, cancel, refund
**Run Date:** 2026-09-19T00:17:52Z
**Findings:** 4 (all draft / NEEDS_REVIEW — pending backend execution)

## Summary

4 cross-flow state-interleaving hypotheses were identified by composing the
state-changing payout actions observed in three independent recorded flows
(price, cancel, refund) on the same order fixture (order 1042,
originalAmount=$100). Each source records one payout action in isolation,
and none of the single-source recordings violates the monetary invariant
`totalReturned <= originalAmount`. However, when the payout actions from
different sources are composed into sequences not recorded in any single
source, the server may allow cumulative payouts that exceed the original
payment amount.

All 4 findings are **draft status** (NEEDS_REVIEW). The backend will execute
each mutation script, capture the signed transport trace, recompute the
`sum_lte` invariant from the AFTER response, and assign final verdicts
(CONFIRMED / NOT_REPRODUCED / CHECK_ERROR).

**Shared invariant under test:** `order.totalReturned <= order.originalAmount`

---

## Finding F-001: Cross-flow CF-001 — price adjustment then cancel

- **CWE:** CWE-841: Improper Enforcement of Behavioral Workflow; CWE-367:
  Race Condition (TOCTOU)
- **Severity:** Critical (if confirmed — financial loss of up to $30 per order)
- **Source:** MUTATION_SCRIPT (01_state_interleaving_price_then_cancel.py)
- **Candidate:** CF-001
- **Source Runs:** price, cancel
- **Endpoints:**
  - POST /api/order/price-adjustment (observed in source 'price')
  - POST /api/order/cancel (observed in source 'cancel')
- **Issue:** The UI removes the Cancel order affordance after a price
  adjustment is issued (price/UIR-001, verified observed-UI provenance:
  "After a price adjustment is issued, the cancel-order affordance is removed
  from the order page"). The cross-flow hypothesis is that the server should
  also reject POST /api/order/cancel once priceProtection.status is completed.
  If the server allows the cancel after the price adjustment, totalReturned
  would be 30 + 100 = 130 > 100 (originalAmount), resulting in a $30 overpayment.
- **Evidence:** Script composes the price-adjustment payout (source 'price',
  HAR entry 1, totalReturned 0->30) with the cancellation reimbursement payout
  (source 'cancel', HAR entry 1, totalReturned 0->100) on the same order.
  The final GET /api/order after the composed sequence is checked against
  the invariant `sum_lte: order.totalReturned <= order.originalAmount`.
- **Fix:**
  - Enforce the priceProtection.status=completed lock server-side: reject
    POST /api/order/cancel when priceProtection.status is "completed"
  - Add a server-side invariant check: before any payout action, verify that
    `totalReturned + payout_amount <= originalAmount`; reject the action if
    the invariant would be violated
  - Add an integration test that composes the price-adjustment and
    cancellation actions on the same order and asserts totalReturned <=
    originalAmount

---

## Finding F-002: Cross-flow CF-002 — price adjustment then refund completion

- **CWE:** CWE-841: Improper Enforcement of Behavioral Workflow; CWE-367:
  Race Condition (TOCTOU)
- **Severity:** Critical (if confirmed — financial loss of up to $30 per order)
- **Source:** MUTATION_SCRIPT (02_state_interleaving_price_then_refund.py)
- **Candidate:** CF-002
- **Source Runs:** price, refund
- **Endpoints:**
  - POST /api/order/price-adjustment (observed in source 'price')
  - POST /api/order/refund/request (observed in source 'refund')
  - POST /api/order/refund/complete (observed in source 'refund')
- **Issue:** The UI removes the price-adjustment affordance while a refund
  request is active (refund/UIR-001, verified observed-UI provenance), and the
  price source shows the price-adjustment button disappears after an
  adjustment is issued (price/UIR-002). The cross-flow hypothesis is that the
  server should reject a refund completion that, combined with a prior price
  adjustment, exceeds originalAmount. If the server allows the refund
  completion after the price adjustment, totalReturned would be 30 + 100 =
  130 > 100 (originalAmount), resulting in a $30 overpayment.
- **Evidence:** Script composes the price-adjustment payout (source 'price',
  $30) with the refund request + refund completion chain (source 'refund',
  $100) on the same order. The final GET /api/order after the composed
  sequence is checked against the invariant.
- **Fix:**
  - Enforce the priceProtection.status=completed lock server-side: reject
    POST /api/order/refund/request and POST /api/order/refund/complete when
    priceProtection.status is "completed"
  - Add a server-side invariant check: before completing a refund, verify that
    `totalReturned + refund_completedAmount <= originalAmount`; reject the
    completion if the invariant would be violated
  - Add an integration test that composes the price-adjustment and refund
    completion actions on the same order and asserts totalReturned <=
    originalAmount

---

## Finding F-003: Cross-flow CF-003 — cancel then refund completion

- **CWE:** CWE-841: Improper Enforcement of Behavioral Workflow; CWE-367:
  Race Condition (TOCTOU); CWE-799: Improper Control of Interaction Frequency
- **Severity:** Critical (if confirmed — financial loss of up to $100 per
  order, i.e. double reimbursement)
- **Source:** MUTATION_SCRIPT (03_state_interleaving_cancel_then_refund.py)
- **Candidate:** CF-003
- **Source Runs:** cancel, refund
- **Endpoints:**
  - POST /api/order/cancel (observed in source 'cancel')
  - POST /api/order/refund/request (observed in source 'refund')
  - POST /api/order/refund/complete (observed in source 'refund')
- **Issue:** The UI removes all customer order actions after a cancellation
  (cancel/UIR-001, verified observed-UI provenance: "After the order is
  canceled, all customer order actions are removed"). The cross-flow
  hypothesis is that the server should reject a refund request or completion
  after the order is canceled. If the server allows the refund completion
  after the cancel, totalReturned would be 100 + 100 = 200 > 100
  (originalAmount), resulting in a $100 double reimbursement.
- **Evidence:** Script composes the cancellation reimbursement payout
  (source 'cancel', $100) with the refund request + refund completion chain
  (source 'refund', $100) on the same order. The final GET /api/order after
  the composed sequence is checked against the invariant.
- **Fix:**
  - Enforce the orderStatus=canceled terminal lock server-side: reject
    POST /api/order/refund/request and POST /api/order/refund/complete when
    orderStatus is "canceled"
  - Add a server-side invariant check: before completing a refund, verify that
    `totalReturned + refund_completedAmount <= originalAmount`; reject the
    completion if the invariant would be violated
  - Add an integration test that composes the cancel and refund completion
    actions on the same order and asserts totalReturned <= originalAmount

---

## Finding F-004: Cross-flow CF-004 — all three payout actions composed

- **CWE:** CWE-841: Improper Enforcement of Behavioral Workflow; CWE-367:
  Race Condition (TOCTOU); CWE-799: Improper Control of Interaction Frequency
- **Severity:** Critical (if confirmed — financial loss of up to $130 per
  order, i.e. triple-stack of payouts)
- **Source:** MUTATION_SCRIPT (04_state_interleaving_price_refund_cancel.py)
- **Candidate:** CF-004
- **Source Runs:** price, cancel, refund
- **Endpoints:**
  - POST /api/order/price-adjustment (observed in source 'price')
  - POST /api/order/refund/request (observed in source 'refund')
  - POST /api/order/refund/complete (observed in source 'refund')
  - POST /api/order/cancel (observed in source 'cancel')
- **Issue:** The most aggressive cross-flow composed sequence. If the server
  allows all three payout actions (price adjustment $30, refund completion
  $100, cancel $100) on the same order without enforcing the pairwise locks
  or the monetary invariant, totalReturned could reach 30 + 100 + 100 = 230,
  far exceeding originalAmount = 100. This would be a $130 overpayment per
  order.
- **Evidence:** Script composes all three payout actions from all three
  recorded sources on the same order: price adjustment (source 'price'),
  refund request + completion (source 'refund'), and cancel (source
  'cancel'). The final GET /api/order after the composed sequence is checked
  against the invariant.
- **Fix:**
  - Implement a global server-side invariant check: before ANY payout action
    (price adjustment, cancel, refund completion), verify that
    `totalReturned + payout_amount <= originalAmount`; reject the action if
    the invariant would be violated
  - Enforce all pairwise locks server-side:
    - priceProtection.status=completed blocks cancel and refund
    - orderStatus=canceled blocks refund and price adjustment
    - refund.status=pending or completed blocks price adjustment and cancel
  - Add an integration test that composes all three payout actions on the
    same order and asserts totalReturned <= originalAmount

---

## Scope Notes

- **Setup path:** POST /api/demo/reset is the documented setup path for
  restoring the demo order fixture. It is never an attack target and is used
  only to establish a clean precondition for each scenario.
- **Authentication:** No login/credential endpoint exists in any source
  recording. scope.json does not set authentication_required: true, so
  authentication is out of scope. Not a missing-auth vulnerability.
- **Cross-flow nature:** Each source recording captures one payout action in
  isolation against the same order fixture. The composed sequences tested
  here are not recorded in any single source; they are cross-flow
  hypotheses that the backend state machine must enforce even when actions
  from different recorded flows are composed.
- **No backend coverage:** No backend_coverage/ scripts are present for this
  flow. The 4 AI-generated mutation scripts are the only probes.