# FlowBusters Remediation Report (DRAFT — pending backend execution)

**Target:** http://localhost:3000/
**Flow:** return-demo
**Run Date:** 2026-09-23T00:20:00Z
**Findings drafted:** 7 (0 confirmed yet — 6 mutation probes are queued for backend execution, 1 is a coverage gap)

## Summary

This flow is a single unauthenticated "Order" return/price-protection page (Order #1042).
No login step was recorded, so authentication is out of scope for this run (see `auth_check`
in `findings.json`). Only one state-changing endpoint was actually exercised in the recording:
`POST /api/order/price-adjustment`, which returns $30.00 to the customer's original payment
method. All findings below concern that endpoint plus a coverage gap for two other money-moving
actions the UI advertises (`Cancel order`, `Request refund`) but the recording never invoked.

**Most significant concern (pending confirmation):** the "Request price adjustment" control
disappears from the UI after one use, and the order's `priceProtection.status` field moves from
`eligible` to `completed` — but this is a *client-side* gate. Findings F-001, F-002 and F-006 test
whether the server independently blocks a second $30 payout if the same endpoint is called again
directly (bypassing the UI). If the server does not, this is a direct, repeatable financial-loss bug.

## Findings

### 1. F-001 / F-002 / F-006 — Repeated price-adjustment calls may pay out more than once

- **CWE:** CWE-841: Improper Enforcement of Behavioral Workflow
- **Severity:** High
- **Source:** MUTATION_SCRIPT (scripts `01_replay_price_adjustment_twice.py`,
  `02_repeat_price_adjustment_three_times.py`, `06_replay_with_forged_status_reset.py`)
- **Endpoint:** `POST /api/order/price-adjustment`
- **Issue:** The UI hides the "Request price adjustment" button and the recorded response shows
  `priceProtection.status: "eligible" -> "completed"` after one successful call (observed UI rule
  UIR-001). All three scripts call the endpoint again — twice, three times, and once with a body
  that tries to claim `priceProtection.status: "eligible"` again — to check whether the *server*
  (not just the UI) refuses a second payout. Status is pending backend execution.
- **Evidence:** See `verification.rule` (UIR-001, facts UIR-001-F1/F2) in `findings.json`, and the
  draft plans `mutations/return-demo/01_*.plan.json`, `02_*.plan.json`, `06_*.plan.json`.
- **Fix (if confirmed):**
  - Enforce single-use of the price-adjustment action server-side, keyed off the order's own
    stored `priceProtection.status`/`completedAt`, not client-supplied state.
  - Reject any request body that attempts to set `priceProtection.status` or other server-owned
    fields; treat the endpoint as accepting no meaningful client input for this action.
  - Add a database-level constraint or transactional check so a race between two near-simultaneous
    calls cannot both succeed.

---

### 2. F-003 — Price-adjustment endpoint may trust client-supplied amount fields

- **CWE:** CWE-20: Improper Input Validation
- **Severity:** Medium
- **Source:** MUTATION_SCRIPT (`03_pricing_tamper_client_supplied_override.py`)
- **Endpoint:** `POST /api/order/price-adjustment`
- **Issue:** The recorded happy-path call sent no body at all, and the server computed the $30
  adjustment itself — a good sign. This probe resends the call with `adjustmentAmount`,
  `totalReturned`, and `netCustomerBenefit` all set to 9999 to confirm there is no alternate code
  path that reads these from the request body instead of recomputing them.
- **Fix (if confirmed):** Ignore all amount/total fields in the request body for this endpoint;
  always derive the adjustment from the server's own `currentPrice` vs. `originalAmount` logic.

---

### 3. F-004 — Mass assignment of privilege/status fields

- **CWE:** CWE-915: Improperly Controlled Modification of Dynamically-Determined Object Attributes
- **Severity:** Medium
- **Source:** MUTATION_SCRIPT (`04_mass_assignment_privilege_fields.py`)
- **Endpoint:** `POST /api/order/price-adjustment`
- **Issue:** Sends `status`, `isAdmin`, `verified`, `role`, and a nested `priceProtection.status`
  override that the UI never sends. This is a field-acceptance claim (not an amount bound), so it
  is marked `unsupported_business_rule` for the automated check; a human should diff the before/after
  order object for each injected key once the script runs.
- **Fix (if confirmed):** Use an explicit allow-list (or schema) for accepted request fields on this
  endpoint; reject or silently strip anything else, and log unexpected fields for monitoring.

---

### 4. F-005 — Malformed/extreme numeric and type-confused input

- **CWE:** CWE-20: Improper Input Validation
- **Severity:** Medium
- **Source:** MUTATION_SCRIPT (`05_data_tamper_invalid_numeric_types.py`)
- **Endpoint:** `POST /api/order/price-adjustment`
- **Issue:** Sends `adjustmentAmount: -5000`, `quantity: 0.5`, `totalReturned: "unlimited"` to probe
  for type-confusion or negative-number bypasses of the `totalReturned <= originalAmount` invariant.
- **Fix (if confirmed):** Validate types and ranges for all numeric fields server-side; reject
  negative/non-numeric input with a 4xx response rather than coercing it.

---

### 5. F-007 — Cancellation / refund double-reimbursement risk could not be tested (coverage gap)

- **CWE:** CWE-841: Improper Enforcement of Behavioral Workflow
- **Severity:** Medium (unverified — flagged for manual review)
- **Source:** ANALYSIS
- **Endpoint:** Unknown — inferred only (`/api/order/cancel`, `/api/order/refund/request`,
  `/api/order/refund/complete` are not in `endpoint_catalog.json`)
- **Issue:** The order page shows "Cancel order" and "Request refund" buttons alongside "Request
  price adjustment" in the very first UI snapshot, and the order object carries independent
  `cancellation.reimbursementAmount`, `refund.requestedAmount/completedAmount`, and
  `availableActions.cancelOrder/requestRefund/completeRefund` fields — but none of these paths were
  exercised in this recording, so their real endpoints are unknown and cannot be authorized for
  probing yet.
- **Fix / Next step:** Record a follow-up flow that actually clicks "Cancel order" and "Request
  refund" (and any staff-only "Complete refund" flow, if one exists) so their endpoints become
  validated. Then explicitly verify server-side that
  `cancellation.reimbursementAmount + priceProtection.adjustmentAmount + refund.completedAmount`
  can never exceed `originalAmount` for the same order, and that cancellation/refund are blocked
  once a price adjustment has completed (mirroring the client-side gating already observed).
