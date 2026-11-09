# FlowBusters Remediation Report (DRAFT — pending backend execution)

**Target:** http://localhost:3000
**Flow:** cross-flow-075472db767d (cross-flow analysis of return-demo, refund-rerun, cancel-demo)
**Run Date:** 2026-09-22 / 2026-09-23
**Findings:** 2 (0 confirmed, 2 not yet executed — see queue_status in findings.json)

## Summary

Analysis of three independent recordings against the same order (#1042, Northstar
Weekender Pack, paid $100.00) shows the order supports three customer-initiated
payout actions — **cancel order**, **request refund**, and **request price
adjustment** — which the UI explicitly presents as mutually exclusive ("Price
protection is not available while another order request is active"). The server
correctly enforces this in most directions: completing a refund or a cancellation
both lock out the other two actions. **However, completing a price adjustment does
not lock out "Request refund"** — the button remains visible and
`availableActions.requestRefund` remains `true` in the response captured
immediately after the price adjustment completes. No single recording exercised
both actions back-to-back, so this is a cross-flow hypothesis requiring backend
execution to confirm, but the evidence for the asymmetry itself is a direct,
already-recorded API and UI fact (not an inference).

**Most severe (pending confirmation):** a customer could collect a $30.00 price
adjustment AND a $100.00 refund on the same $100.00 order, netting $130.00 —
a $30.00 loss to the business per order.

## Findings

### 1. Price adjustment does not disable subsequent refund request (potential $30 overpayment per order)

- **CWE:** CWE-841: Improper Enforcement of Behavioral Workflow
- **Severity:** Critical (pending execution confirmation)
- **Source:** ANALYSIS (cross-flow hypothesis; plan `01_price_adjustment_then_refund_double_dip.plan.json` not yet executed)
- **Endpoint chain:** `POST /api/order/price-adjustment` → `POST /api/order/refund/request` → `POST /api/order/refund/complete`
- **Issue:** After `POST /api/order/price-adjustment` completes (which pays out $30.00), the response's `availableActions.requestRefund` remains `true` and the "Request refund" button is still rendered — unlike after a refund or cancellation completes, where all three actions become `false`. If the subsequent refund request and completion succeed on the same order, `order.totalReturned` would reach $130.00 against an `originalAmount` of $100.00.
- **Evidence:** See `findings.json` F-001 for exact captured request/response bodies from `return-demo` (price-adjustment) and `refund-rerun` (refund request + complete), and `mutations/cross-flow-075472db767d/01_price_adjustment_then_refund_double_dip.plan.json` for the exact chain to execute.
- **Fix:**
  - Add a server-side guard so that `POST /api/order/price-adjustment` sets `availableActions.requestRefund = false` (and rejects `refund/request` / `refund/complete`) once a price adjustment has been issued for the order, mirroring the guard already applied by cancellation and refund.
  - Alternatively/additionally, enforce the invariant directly at the data layer: reject any action whose resulting `totalReturned` would exceed `originalAmount` for the order.
  - Add an integration test that exercises price-adjustment → refund-request → refund-complete on one order and asserts a 4xx rejection on the second action.

### 2. Refund completion replay not verified server-side (possible duplicate payout)

- **CWE:** CWE-841: Improper Enforcement of Behavioral Workflow
- **Severity:** Medium (pending execution confirmation)
- **Source:** ANALYSIS (plan `02_refund_complete_replay.plan.json` not yet executed)
- **Endpoint:** `POST /api/order/refund/complete`
- **Issue:** The recorded response after refund completion shows `availableActions.completeRefund = false`, which is consistent with — but does not prove — server-side rejection of a second, direct call to the same endpoint.
- **Evidence:** See `findings.json` F-002.
- **Fix:**
  - Confirm (or add) a server-side state check so that `refund/complete` is rejected with 4xx if `refund.status` is not `pending`, independent of the client hiding the button.
  - Add an integration test that calls `refund/complete` twice in a row and asserts the second call is rejected and `totalReturned` does not increase further.

---

**Next step for both findings:** execute the two JSON plans in
`mutations/cross-flow-075472db767d/` against a freshly reset instance of the
target application and recompute the `sum_lte` invariant from the actual final
`GET /api/order` response before treating either finding as CONFIRMED.
