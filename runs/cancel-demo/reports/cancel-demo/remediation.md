# FlowBusters Remediation Report (DRAFT — pending backend execution)

**Target:** http://localhost:3000/
**Flow:** cancel-demo
**Run Date:** 2026-09-22T23:55:00Z
**Findings:** 5 draft hypotheses (0 critical) — all currently `NEEDS_REVIEW`, pending backend execution of the compiled probe scripts and signed-transport verification.

## Summary

The recorded flow is a single "cancel order" workflow on `http://localhost:3000/`. The order object (`GET /api/order`) starts as `delivered`, `orderStatus` transitions to `canceled` via `POST /api/order/cancel`, which unconditionally issues a **$100.00 reimbursement** with **no request body** — the server derives everything it needs from server-side order/session state.

Because the request carries no client-supplied identifiers or amounts, and because `endpoint_catalog.json` only authorizes three routes (`GET /api/order`, `POST /api/order/cancel`, and the setup-only `POST /api/demo/reset`), the available attack surface is concentrated entirely on **replaying the cancel call**. All 5 draft probes target this single endpoint from different angles. None have been executed yet — the backend compiles the JSON plans in `mutations/cancel-demo/*.plan.json` into runnable scripts and executes them after this session ends. Findings below describe the *hypothesis under test*; **do not treat any as confirmed until the backend's execution receipts are attached.**

Most severe hypothesis if confirmed: **F-001 / F-002 — calling `POST /api/order/cancel` more than once may issue more than one $100.00 reimbursement for the same $100.00 order**, since the endpoint returns `200` with `cancellation.status: "completed"` and a fresh reimbursement irrespective of whether the order was already canceled.

## Findings

### 1. F-001 — Repeated cancellation may issue a duplicate $100.00 reimbursement (double payout)

- **CWE:** CWE-841: Improper Enforcement of Behavioral Workflow
- **Severity:** High (financial — pending confirmation)
- **Source:** MUTATION_SCRIPT (`01_double_cancel_amount_bound.py`, compiled from `01_double_cancel_amount_bound.plan.json`)
- **Endpoint:** `POST http://localhost:3000/api/order/cancel`
- **Issue:** The recorded happy path shows `POST /api/order/cancel` accepts an empty body and unconditionally sets `cancellation.status="completed"`, `reimbursementAmount=100`, `totalReturned=100`. Nothing observed in the recording proves the server checks `orderStatus != "canceled"` before processing a second call. The plan calls cancel twice from a clean reset and checks `order.totalReturned <= order.originalAmount` on the final read.
- **Evidence:** See `mutations/cancel-demo/01_double_cancel_amount_bound.plan.json`; awaiting execution receipt.
- **Fix (if confirmed):**
  - Before processing cancellation, verify `orderStatus != "canceled"` (and `cancellation.status != "completed"`) server-side and reject/no-op on a repeat call.
  - Make the payout idempotent per order (e.g. a single `reimbursementAmount` field that is set once, not incremented).
  - Return a 4xx/409 for a cancel request against an already-canceled order.

---

### 2. F-002 — Third sequential cancel request may compound the double-payout exposure

- **CWE:** CWE-841: Improper Enforcement of Behavioral Workflow
- **Severity:** High (financial — pending confirmation)
- **Source:** MUTATION_SCRIPT (`02_triple_cancel_compounding_amount_bound.py`)
- **Endpoint:** `POST http://localhost:3000/api/order/cancel`
- **Issue:** Same root cause as F-001, demonstrated with 3 repeats to show the exposure scales with the number of replayed calls rather than being capped at 2x.
- **Evidence:** See `mutations/cancel-demo/02_triple_cancel_compounding_amount_bound.plan.json`; awaiting execution receipt.
- **Fix:** Same as F-001.

---

### 3. F-003 — Cancel endpoint may not guard against being called again once the order is already 'canceled'

- **CWE:** CWE-841: Improper Enforcement of Behavioral Workflow
- **Severity:** Medium — pending confirmation
- **Source:** MUTATION_SCRIPT (`03_cancel_after_cancel_status_guard.py`)
- **Endpoint:** `POST http://localhost:3000/api/order/cancel`
- **Issue:** Independent of the monetary outcome, the workflow-state guard itself (rejecting a call once `orderStatus == "canceled"`) is a distinct correctness/authorization concern from the amount bound in F-001/F-002. This claim is not expressible via the `sum_lte` predicate and is retained as reviewable coverage — a human should manually verify the HTTP status/response of the second call.
- **Fix:** Same status-guard fix as F-001; additionally return an explicit rejection status/message rather than a silent `200`.

---

### 4. F-004 — Repeated cancellation may append duplicate 'order_canceled' entries to order history

- **CWE:** CWE-841: Improper Enforcement of Behavioral Workflow
- **Severity:** Medium — pending confirmation
- **Source:** MUTATION_SCRIPT (`04_duplicate_history_entry_integrity.py`)
- **Endpoint:** `POST http://localhost:3000/api/order/cancel`
- **Issue:** The audit/history ledger (`order.history[]`) recorded a single `evt-3` "order_canceled" entry in the demo run. If cancel is called again, the ledger may gain a second identical event, corrupting the audit trail even independent of the monetary double-payout. Not expressible as a numeric bound — retained as reviewable coverage.
- **Fix:** De-duplicate or guard history-entry creation behind the same status check recommended for F-001; consider ledger uniqueness constraints keyed by `(order_id, event_type)` for terminal events.

---

### 5. F-005 — Server may not enforce its own advertised `availableActions.cancelOrder=false` flag

- **CWE:** CWE-862: Missing Authorization
- **Severity:** Medium — pending confirmation
- **Source:** MUTATION_SCRIPT (`05_repeated_cancel_authorization_enforcement.py`)
- **Endpoint:** `POST http://localhost:3000/api/order/cancel`
- **Issue:** After cancellation, `GET /api/order` reports `availableActions.cancelOrder: false`, which the UI uses to hide the "Cancel order" button (see `state_map.json` → `UIR-001`). If the server does not independently re-check this condition when a raw `POST /api/order/cancel` is forced-browsed, the advertised capability flag is client-side decoration only. This is a distinct authorization-enforcement claim, not a numeric bound — retained as reviewable coverage.
- **Fix:** Ensure the same server-side check that determines `availableActions.cancelOrder` is also enforced as a hard gate inside the `POST /api/order/cancel` handler itself, not just reflected in the read-model.

---

## Coverage note

The validated `endpoint_catalog.json` for this flow authorizes only `GET /api/order`, `POST /api/order/cancel`, and the setup-only `POST /api/demo/reset`. The demo's "Request refund" and "Request price adjustment" buttons (and the observed UI rule `UIR-002`, price protection becoming "Unavailable" once cancellation is active) have **no corresponding recorded or catalogued endpoint**, so no mutation could be built against them without fabricating a URL. These remain **unresolved UI affordances** — recommend re-recording a flow that exercises "Request refund" and "Request price adjustment" so their endpoints can be captured and probed in a future run.
