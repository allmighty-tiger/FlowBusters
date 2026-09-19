"""
FlowBusters Mutation: SKIP_STEP
Target: http://localhost:3000/api/order/refund/complete
Attack: The refund workflow is request -> complete. availableActions shows
completeRefund=false until a refund is requested. This endpoint is reachable with
NO authentication and NO prior refund request. We call refund/complete directly
on a fresh order and re-read: does it pay out without a prior request, and does
that payout exceed/stack the original amount? Invariant: order.totalReturned
must not exceed order.originalAmount.
"""
import asyncio
import json
import httpx

BASE = "http://localhost:3000"
READ = BASE + "/api/order"
RESET = BASE + "/api/demo/reset"
COMPLETE = BASE + "/api/order/refund/complete"
MUTATION_TYPE = "SKIP_STEP"

INVARIANT = {
    "operator": "sum_lte",
    "terms": [["order", "totalReturned"]],
    "limit": ["order", "originalAmount"],
}

RULE = {
    "source": "agent",
    "reference": (
        "Refund workflow must complete only after /api/order/refund/request has "
        "established a refund (catalog 'refund' chain; availableActions.completeRefund "
        "is false until a refund is requested). Inferred from state_map.json; "
        "requires user/specification confirmation before CONFIRMED."
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
        actions = [cap(await client.post(COMPLETE), "POST", COMPLETE, None)]
        after = cap(await client.get(READ), "GET", READ, None)

    order = after["response"]["actual"]["order"]
    observed = compute_violation(after)
    result = {
        "title": "Refund/complete called with no prior refund request and no auth",
        "mutation_type": MUTATION_TYPE,
        "url": COMPLETE,
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
                    "order.totalReturned=%s vs order.originalAmount=%s "
                    "(refund.status=%r) after direct refund/complete with no prior "
                    "request (violation=%s)"
                    % (
                        order.get("totalReturned"),
                        order.get("originalAmount"),
                        (order.get("refund") or {}).get("status"),
                        observed,
                    )
                ),
            },
        },
    }
    print(json.dumps(result))


if __name__ == "__main__":
    asyncio.run(probe())