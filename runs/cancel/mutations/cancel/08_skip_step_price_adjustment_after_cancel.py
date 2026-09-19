"""
FlowBusters Mutation: SKIP_STEP
Target: http://localhost:3000/api/order/price-adjustment
Attack: Observed rule UIR-001: after cancellation the order is terminal -- all
order action buttons are removed ("This order has no actions requiring your
attention."). This probe cancels the order, then bypasses the removed UI and calls
the price-adjustment endpoint directly on the canceled order. The server must
reject the adjustment (order already canceled / fully reimbursed). If it pays out,
totalReturned can exceed originalAmount. Invariant: order.totalReturned <=
order.originalAmount.
"""
import asyncio
import json
import httpx

BASE = "http://localhost:3000"
READ = BASE + "/api/order"
RESET = BASE + "/api/demo/reset"
CANCEL = BASE + "/api/order/cancel"
ADJUST = BASE + "/api/order/price-adjustment"
MUTATION_TYPE = "SKIP_STEP"

INVARIANT = {
    "operator": "sum_lte",
    "terms": [["order", "totalReturned"]],
    "limit": ["order", "originalAmount"],
}

RULE = {
    "source": "observed_ui",
    "provenance": {
        "schema_version": 1,
        "source_run": "cancel",
        "artifact": "state_map.json",
        "rule_id": "UIR-001",
        "fact_ids": ["UIR-001-F1", "UIR-001-F2"],
    },
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
            cap(await client.post(CANCEL), "POST", CANCEL, None),
            cap(await client.post(ADJUST), "POST", ADJUST, None),
        ]
        after = cap(await client.get(READ), "GET", READ, None)

    order = after["response"]["actual"]["order"]
    adjustment = order.get("priceProtection") or {}
    observed = compute_violation(after)
    result = {
        "title": "Price adjustment still applies to an order already canceled and reimbursed",
        "mutation_type": MUTATION_TYPE,
        "url": ADJUST,
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
                    "after cancel (terminal), direct price-adjustment -> "
                    "order.totalReturned=%s vs order.originalAmount=%s "
                    "(priceProtection.adjustmentAmount=%r, status=%r) (violation=%s)"
                    % (
                        order.get("totalReturned"),
                        order.get("originalAmount"),
                        adjustment.get("adjustmentAmount"),
                        adjustment.get("status"),
                        observed,
                    )
                ),
            },
        },
    }
    print(json.dumps(result))


if __name__ == "__main__":
    asyncio.run(probe())