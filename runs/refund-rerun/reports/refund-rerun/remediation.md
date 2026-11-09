# FlowBusters Remediation Report

**Target:** http://localhost:3000/
**Flow:** refund-rerun
**Run Date:** 2026-09-22T23:27:18.215219Z
**Findings:** 5 drafted (2 potentially Critical, pending backend execution)

## Summary

This flow is a single order-detail page ("Order #1042") with two real, recorded
API actions: **requesting a refund** and **completing a refund**, for a $100.00
order. There is no login/authentication anywhere in this recording, and the two
mutating endpoints send no request body at all — so the realistic attack surface
here is almost entirely about *workflow ordering and repetition*, not credential
or field-tampering attacks.

The most severe risk, if confirmed, is a **double payout**: the UI hides the
"Complete refund" button and disables `availableActions.completeRefund` after
the first completion, but that is a client-side control. If the server does
not independently re-check `refund.status` before paying out, replaying the
completion call — or reopening the order with a new request afterward — could
return the customer's $100.00 more than once.

All five findings below are currently **drafts awaiting backend execution**:
the agent session prepared declarative test plans (`mutations/refund-rerun/*.plan.json`)
but does not execute HTTP itself in this environment. Two plans (F-001, F-002)
use a supported automatic money-bound check (`totalReturned <= originalAmount`)
and will resolve to CONFIRMED/NOT_REPRODUCED once run. Three plans (F-003,
F-004, F-005) test workflow-ordering rules that cannot be expressed as a
numeric bound and will remain NEEDS_REVIEW pending manual inspection of the
final order state (`refund.status`, `refund.requestedAt`, `order.history`)
after each plan runs.

## Findings

### 1. Refund completion may not be idempotent — calling refund/complete twice could pay out twice

- **CWE:** CWE-841: Improper Enforcement of Behavioral Workflow; CWE-694: Use of Multiple Resources with Duplicate Identifier
- **Severity:** Critical (if confirmed)
- **Source:** MUTATION_SCRIPT (`02_double_complete_sequential_replay.plan.json`)
- **Endpoint:** POST `/api/order/refund/complete`
- **Issue:** The recorded happy path calls `refund/complete` exactly once, after which the UI removes the "Complete refund" button and `availableActions.completeRefund` flips to `false`. This test calls the same endpoint a second time immediately afterward and checks whether `order.totalReturned` exceeds `order.originalAmount` (100) in the final state.
- **Evidence:** UI: `demo.json` `ui_states[3].elements[66]` (button present) → `ui_states[5].changes_from_previous.disappeared[3]` (button removed). API: `recording.har` entry 1 → entry 2, `availableActions.completeRefund` `true` → `false`.
- **Fix:**
  - Before paying out, re-check `refund.status === 'pending'` server-side (not just client-side) inside the `refund/complete` handler.
  - Use a transactional state guard (e.g. atomic compare-and-swap on `refund.status`) so a second concurrent or sequential call cannot re-execute the payout.
  - Return `409 Conflict` (not `200`) for a completion attempt on an order whose refund is already `completed`.

---

### 2. A completed order may be reopened via refund/request, enabling a second payout

- **CWE:** CWE-841: Improper Enforcement of Behavioral Workflow; CWE-694
- **Severity:** Critical (if confirmed)
- **Source:** MUTATION_SCRIPT (`04_reopen_completed_order_via_request_then_complete.plan.json`)
- **Endpoint:** POST `/api/order/refund/request` → POST `/api/order/refund/complete`
- **Issue:** After completion the UI shows "This order has no actions requiring your attention." / "No further actions are available for this order." — implying a terminal state. This test calls `refund/request` again on a completed order, and if that succeeds, calls `refund/complete` again, checking whether `totalReturned` exceeds `originalAmount`.
- **Evidence:** `demo.json` `ui_states[5].changes_from_previous.appeared[4]` and `[5]`.
- **Fix:**
  - Reject `refund/request` when `refund.status` is anything other than the expected pre-request state (e.g. `none`); an order with `refund.status === 'completed'` must be immutable for refund purposes.
  - Apply the same state guard described in Finding 1 to prevent the chained reopen-and-recomplete path.

---

### 3. refund/complete may be reachable without a prior refund/request (skipped step)

- **CWE:** CWE-841: Improper Enforcement of Behavioral Workflow
- **Severity:** High
- **Source:** MUTATION_SCRIPT (`01_skip_step_complete_without_request.plan.json`)
- **Endpoint:** POST `/api/order/refund/complete`
- **Issue:** The "Complete refund" button only appears in the UI after a refund has been requested. This test calls `refund/complete` directly on a freshly reset order with no prior request.
- **Evidence:** `demo.json` `ui_states[2].elements` (button absent) → `ui_states[3].changes_from_previous.appeared[9]` (button appears only once a request exists).
- **Note:** No supported numeric invariant distinguishes this from a correct flow (both end at `totalReturned == 100`). **Requires manual review**: after running the plan, inspect the final `order.refund.status` and `order.history` — if `refund.status` reads `completed` with no preceding `refund_requested` history entry, the workflow-order control is not enforced server-side.
- **Fix:**
  - Require `refund.status === 'pending'` (i.e., an existing request) as a precondition inside the `refund/complete` handler; reject with `409`/`400` otherwise.

---

### 4. refund/request may be accepted twice while a refund is already pending

- **CWE:** CWE-841: Improper Enforcement of Behavioral Workflow
- **Severity:** Medium
- **Source:** MUTATION_SCRIPT (`03_duplicate_refund_request_while_pending.plan.json`)
- **Endpoint:** POST `/api/order/refund/request`
- **Issue:** The "Request refund" button disappears and `availableActions.requestRefund` flips to `false` after the first request. This test calls `refund/request` a second time immediately after the first.
- **Evidence:** `demo.json` `ui_states[0].elements[63]` → `ui_states[3].changes_from_previous.disappeared[6]`; `recording.har` entries 0→1, `availableActions.requestRefund` `true` → `false`.
- **Note:** `requestedAmount` is fixed at `originalAmount` regardless of repetition, so no numeric bound proves a duplicate was wrongly accepted. **Requires manual review** of `order.history` for a duplicate `refund_requested` entry or a changed `requestedAt` timestamp.
- **Fix:**
  - Guard `refund/request` with the same `refund.status` precondition check (reject if status is already `pending` or `completed`).

---

### 5. A terminal order may accept a new refund/request

- **CWE:** CWE-841: Improper Enforcement of Behavioral Workflow
- **Severity:** Medium
- **Source:** MUTATION_SCRIPT (`05_request_after_completion_terminal_state.plan.json`)
- **Endpoint:** POST `/api/order/refund/request`
- **Issue:** Isolates the terminal-state-reopening question from Finding 2 by calling only a single trailing `refund/request` after completion (no second `complete` call), to test whether the terminal state itself can be reopened at all.
- **Evidence:** `demo.json` `ui_states[5].changes_from_previous.appeared[4]`, `[5]`.
- **Note:** A lone re-request does not change `totalReturned`, so this cannot be expressed as a numeric bound. **Requires manual review** of `refund.status`/`requestedAt` in the final GET.
- **Fix:** Same as Finding 2 — reject `refund/request` on a `completed` order.

---

## Coverage Gaps / Not Applicable

- **Authentication:** No login endpoint exists in this recording; no auth findings are reported (not in scope).
- **Role-swap / IDOR:** Only a single order resource and no distinct roles/credentials were observed — not testable in this flow.
- **Mass assignment / pricing tamper / data tamper:** Both mutating endpoints send an empty request body in the recorded flow — there are no client-supplied fields to manipulate.
- **"Cancel order" and "Request price adjustment" buttons:** Visible in the UI but no corresponding HTTP request was recorded and neither route is documented in `endpoint_catalog.json`. No URL was fabricated for them; these remain **unresolved coverage gaps** requiring a fresh recording that exercises those buttons before they can be probed.
