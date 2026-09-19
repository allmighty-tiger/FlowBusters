"""
FlowBusters Cross-Flow Mutation: STATE_INTERLEAVING
Candidate: CF-001 (price + cancel)
Target: /api/order/price-adjustment then /api/order/cancel
Attack: Compose the price-adjustment payout ($30, observed in source 'price')
       with the cancellation reimbursement payout ($100, observed in source
       'cancel') on the same order. The UI removes the Cancel order affordance
       after a price adjustment (price/UIR-001, verified observed-UI provenance).
       This probe tests whether the server enforces that lock AND the monetary
       invariant totalReturned <= originalAmount when the two payouts are
       composed from different recorded flows.
Invariant: sum_lte — order.totalReturned <= order.originalAmount
"""
import asyncio
import json
import httpx

TARGET = "http://localhost:3000"
RESET_URL = f"{TARGET}/api/demo/reset"
GET_ORDER_URL = f"{TARGET}/api/order"
PRICE_ADJ_URL = f"{TARGET}/api/order/price-adjustment"
CANCEL_URL = f"{TARGET}/api/order/cancel"
MUTATION_TYPE = "STATE_INTERLEAVING"
CANDIDATE_ID = "CF-001"
TITLE = "Cross-flow: price adjustment then cancel may exceed originalAmount"

# Invariant: sum(order.totalReturned) <= order.originalAmount
# paths start at the HTTP response root (envelope key "order")
INVARIANT = {
    "operator": "sum_lte",
    "terms": [["order", "totalReturned"]],
    "limit": ["order", "originalAmount"],
}

# Rule provenance: cite the verified source rule from 'price' (UIR-001)
# The composed-sequence claim is agent_inference; the rule reference points
# to the verified observed-UI rule that motivates the test.
RULE = {
    "source": "agent_inference",
    "provenance": {
        "schema_version": 1,
        "source_run": "price",
        "artifact": "state_map.json",
        "rule_id": "UIR-001",
        "fact_ids": ["UIR-001-F1", "UIR-001-F2", "UIR-001-F3"],
    },
}


def _capture(method: str, url: str, body, resp: httpx.Response) -> dict:
    """Build a capture object from an actual httpx.Response.

    Uses response.extensions['flowbusters_sequence'] as the authoritative
    sequence number. Never maintains a local counter.
    """
    seq = resp.extensions.get("flowbusters_sequence")
    if seq is None:
        raise RuntimeError(
            "flowbusters_sequence missing from response.extensions — "
            "harness not injecting transport sequence"
        )
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


def _resolve_path(body: dict, path: list) -> float:
    """Resolve a dot-separated path from the response root and return a float."""
    val = body
    for key in path:
        val = val[key]
    return float(val)


def _compute_violation(after_body: dict) -> bool:
    """Compute the sum_lte violation from the captured AFTER response.

    Returns True if sum(terms) > limit (violation observed).
    """
    term_sum = 0.0
    for term_path in INVARIANT["terms"]:
        term_sum += _resolve_path(after_body, term_path)
    limit = _resolve_path(after_body, INVARIANT["limit"])
    return term_sum > limit


async def probe():
    async with httpx.AsyncClient(timeout=30.0) as client:
        # ── SETUP: establish clean fixture state ──────────────────────
        reset_resp = await client.post(RESET_URL)
        reset_cap = _capture("POST", RESET_URL, None, reset_resp)

        # ── BEFORE: independent state read ────────────────────────────
        before_resp = await client.get(GET_ORDER_URL)
        before_cap = _capture("GET", GET_ORDER_URL, None, before_resp)

        # ── ACTIONS: composed sequence from two different sources ─────
        # Action 1: price adjustment (observed in source 'price', HAR entry 1)
        #   Payout: $30. Expected: totalReturned 0→30, priceProtection.status→completed
        adj_resp = await client.post(PRICE_ADJ_URL)
        adj_cap = _capture("POST", PRICE_ADJ_URL, None, adj_resp)

        # Action 2: cancel (observed in source 'cancel', HAR entry 1)
        #   Payout: $100. Expected rejection: order already locked by price
        #   adjustment (priceProtection.status=completed, cancelOrder=false).
        #   If accepted, totalReturned 30→130 which violates originalAmount=100.
        cancel_resp = await client.post(CANCEL_URL)
        cancel_cap = _capture("POST", CANCEL_URL, None, cancel_resp)

        # ── AFTER: independent state read ─────────────────────────────
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
    cancellation_status = after_order.get("cancellation", {}).get("status")
    adj_msg = adj_cap["response"]["actual"]
    cancel_msg = cancel_cap["response"]["actual"]

    description = (
        f"After composing price-adjustment (source 'price', payout $30) then "
        f"cancel (source 'cancel', payout $100) on the same order: "
        f"totalReturned={total_returned}, originalAmount={original_amount}, "
        f"priceProtection.status={pp_status}, cancellation.status={cancellation_status}. "
        f"Invariant sum_lte: totalReturned({total_returned}) <= originalAmount({original_amount}). "
        f"Violated: {observed}."
    )

    verification = {
        "predicate": "business_rule_must_hold",
        "rule": RULE,
        "setup": [reset_cap],
        "before": before_cap,
        "actions": [adj_cap, cancel_cap],
        "after": after_cap,
        "invariant": INVARIANT,
        "violation": {
            "observed": observed,
            "description": description,
        },
    }

    # Determine outcome
    cancel_status = cancel_cap["status_code"]
    adj_status = adj_cap["status_code"]
    if observed:
        outcome = "CONFIRMED"
    elif cancel_status >= 400 or adj_status >= 400:
        outcome = "NOT_REPRODUCED"
    else:
        outcome = "NOT_REPRODUCED"

    result = {
        "title": TITLE,
        "mutation_type": MUTATION_TYPE,
        "url": CANCEL_URL,
        "status_code": cancel_status,
        "outcome": outcome,
        "candidate_id": CANDIDATE_ID,
        "source_runs": ["price", "cancel"],
        "analysis_source": "CROSS_FLOW",
        "expected_rejection": True,
        "response_body_snippet": json.dumps(after_body)[:500],
        "verification": verification,
    }
    print(json.dumps(result))


if __name__ == "__main__":
    asyncio.run(probe())