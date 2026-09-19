"""
FlowBusters Mutation: DATA_TAMPER
Target: http://localhost:3000/api/order/refund/request
Attack: The recorded refund UI sends a bodyless request. The server must not trust
a client-supplied requestedAmount larger than the order. We request a refund with
requestedAmount=99999, then complete it, and re-read. Two claims:
  (primary) The recorded refund workflow must not pay out more than the original
  amount -> sum_lte on order.totalReturned vs order.originalAmount.
  (review)  The requestedAmount itself should be clamped to the original amount;
  a sum_lte predicate cannot express that directly, so we also report the
  unsupported request-clamp predicate for review.
"""
import asyncio
import json
import httpx

BASE = "http://localhost:3000"
READ = BASE + "/api/order"
RESET = BASE + "/api/demo/reset"
REQUEST = BASE + "/api/order/refund/request"
COMPLETE = BASE + "/api/order/refund/complete"
MUTATION_TYPE = "DATA_TAMPER"

TAMPERED_BODY = {"requestedAmount": 99999, "amount": 99999}

INVARIANT = {
    "operator": "sum_lte",
    "terms": [["order", "totalReturned"]],
    "limit": ["order", "originalAmount"],
}

RULE = {
    "source": "agent",
    "reference": (
        "A refund must not pay the customer more than the order's original amount "
        "and a client-supplied requestedAmount above that must be rejected/clamped. "
        "Inferred from state_map.json (critical_endpoints refund/request); requires "
        "user/specification confirmation before CONFIRMED."
    ),
}


def seq_of(resp):
    try:
        return resp.extensions["flowbusters_sequence"]
    except Exception:
        return None


def parsed(resp):
    try:
        return resp.json()
    except Exception:
        return resp.text


def cap(resp, method, url, body):
    return {
        "sequence": seq_of(resp),
        "status_code": resp.status_code,
        "complete": True,
        "request": {"method": method, "url": url, "body": body},
        "response": {"actual": parsed(resp)},
    }


def is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def compute_violation(after):
    order = after["response"]["actual"]["order"]
    total = order.get("totalReturned")
    original = order.get("originalAmount")
    if not is_num(total) or not is_num(original):
        raise RuntimeError(
            "totalReturned=%r originalAmount=%r not finite numbers" % (total, original)
        )
    return total > original


async def probe():
    async with httpx.AsyncClient(timeout=30.0) as client:
        setup = [cap(await client.post(RESET), "POST", RESET, None)]
        before = cap(await client.get(READ), "GET", READ, None)
        actions = [
            cap(
                await client.post(REQUEST, json=TAMPERED_BODY),
                "POST",
                REQUEST,
                TAMPERED_BODY,
            ),
            cap(await client.post(COMPLETE), "POST", COMPLETE, None),
        ]
        after = cap(await client.get(READ), "GET", READ, None)

    order = after["response"]["actual"]["order"]
    refund = order.get("refund") or {}
    observed = compute_violation(after)
    result = {
        "title": "Refund request honors a client-supplied requestedAmount far above the order total",
        "mutation_type": MUTATION_TYPE,
        "url": REQUEST,
        "status_code": after["status_code"],
        "outcome": "NEEDS_REVIEW",
        "expected_rejection": True,
        "verification": {
            "predicate": "business_rule_must_hold",
            "rule": RULE,
            "setup": setup,
            "before": before,
            "actions": actions,
            "after": after,
            "invariant": INVARIANT,
            "violation": {
                "observed": observed,
                "description": (
                    "sent requestedAmount=99999; order.totalReturned=%s vs "
                    "order.originalAmount=%s (refund.requestedAmount=%r, "
                    "refund.completedAmount=%r) (violation=%s)"
                    % (
                        order.get("totalReturned"),
                        order.get("originalAmount"),
                        refund.get("requestedAmount"),
                        refund.get("completedAmount"),
                        observed,
                    )
                ),
            },
            "supplementary_review": {
                "predicate": "unsupported_business_rule",
                "unsupported_reason": (
                    "The hypothesis that requestedAmount must be clamped to "
                    "order.originalAmount cannot be expressed by sum_lte on "
                    "totalReturned: if the server honors the tamper but caps the "
                    "payout, totalReturned stays <= originalAmount while "
                    "refund.requestedAmount is still 99999. A supported predicate "
                    "that compares refund.requestedAmount to originalAmount is "
                    "needed to confirm the request-clamp rule."
                ),
                "observed_requested_amount": refund.get("requestedAmount"),
                "observed_completed_amount": refund.get("completedAmount"),
            },
        },
    }
    print(json.dumps(result))


if __name__ == "__main__":
    asyncio.run(probe())