"""
FlowBusters Cross-Flow Mutation: STATE_INTERLEAVING
Candidate: CF-002 (price + refund)
Target: /api/order/price-adjustment then /api/order/refund/request then /api/order/refund/complete
Attack: Compose the price-adjustment payout ($30, observed in source 'price')
       with the refund request -> refund completion chain ($100, observed in
       source 'refund') on the same order. The UI removes the price-adjustment
       affordance after a refund request (refund/UIR-001, verified observed-UI
       provenance), but the server may not enforce this lock. If both payouts
       succeed, totalReturned = 30 + 100 = 130 > 100.
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
MUTATION_TYPE = "STATE_INTERLEAVING"
CANDIDATE_ID = "CF-002"
TITLE = "Cross-flow: price adjustment then refund completion may exceed originalAmount"

HEADERS = {"accept": "application/json"}

INVARIANT = {
    "operator": "sum_lte",
    "terms": [["order", "totalReturned"]],
    "limit": ["order", "originalAmount"],
}

RULE = {
    "source": "agent_inference",
    "provenance": {
        "schema_version": 1,
        "source_run": "refund",
        "artifact": "state_map.json",
        "rule_id": "UIR-001",
        "fact_ids": ["UIR-001-F1", "UIR-001-F2"],
    },
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

        # --- Action 1: price adjustment (observed in 'price' source) ---
        # Payout: $30. Expected: totalReturned 0->30, priceProtection.status->completed
        adj_resp = await client.post(PRICE_ADJ_URL)
        adj_cap = _capture("POST", PRICE_ADJ_URL, None, adj_resp)

        # --- Action 2: refund request (observed in 'refund' source) ---
        # Creates a $100 pending refund. Should be rejected if priceProtection
        # is already completed (price/UIR-002: one-time eligibility).
        req_resp = await client.post(REFUND_REQ_URL)
        req_cap = _capture("POST", REFUND_REQ_URL, None, req_resp)

        # --- Action 3: refund completion (observed in 'refund' source) ---
        # Payout: $100. If accepted after price adjustment, totalReturned
        # would be 30 + 100 = 130 > 100 (originalAmount).
        cpl_resp = await client.post(REFUND_CPL_URL)
        cpl_cap = _capture("POST", REFUND_CPL_URL, None, cpl_resp)

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
    pp_status = after_order.get("priceProtection", {}).get("status")
    refund_status = after_order.get("refund", {}).get("status")

    description = (
        f"Cross-flow CF-002: price-adjustment (source 'price', $30 payout) then "
        f"refund request + refund completion (source 'refund', $100 payout). "
        f"Final state: totalReturned={total_returned}, originalAmount={original_amount}, "
        f"priceProtection.status={pp_status}, refund.status={refund_status}. "
        f"Invariant sum_lte: totalReturned <= originalAmount. "
        f"Violated: {observed}."
    )

    verification = {
        "predicate": "business_rule_must_hold",
        "rule": RULE,
        "setup": [reset_cap],
        "before": before_cap,
        "actions": [adj_cap, req_cap, cpl_cap],
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
        "url": REFUND_CPL_URL,
        "status_code": cpl_cap["status_code"],
        "outcome": outcome,
        "candidate_id": CANDIDATE_ID,
        "source_runs": ["price", "refund"],
        "analysis_source": "CROSS_FLOW",
        "expected_rejection": True,
        "response_body_snippet": json.dumps(after_body)[:500],
        "verification": verification,
    }
    print(json.dumps(result))


if __name__ == "__main__":
    asyncio.run(probe())