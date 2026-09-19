"""
FlowBusters Mutation: PRICING_TAMPER
Target: http://localhost:3000/api/order/price-adjustment
Attack: The UI offers a $30.00 price adjustment. Attempt to apply an inflated
        adjustment, then also complete a full refund, then re-read. If the server
        trusts a client-supplied adjustment amount it can push cumulative
        reimbursement past the original amount. Executable check:
        order.totalReturned <= order.originalAmount.
"""
import asyncio
import json
import httpx

ORIGIN = "http://localhost:3000"
RESET = ORIGIN + "/api/demo/reset"
READ = ORIGIN + "/api/order"
PRICE_ADJUSTMENT = ORIGIN + "/api/order/price-adjustment"
REQUEST_REFUND = ORIGIN + "/api/order/refund/request"
COMPLETE_REFUND = ORIGIN + "/api/order/refund/complete"
MUTATION_TYPE = "PRICING_TAMPER"

TAMPER_BODY = {
    "adjustmentAmount": 100000,
    "amount": 100000,
    "requestedAmount": 100000,
    "totalReturned": 100000,
    "originalAmount": 5,
    "id": "1042",
}


def seq(resp):
    try:
        return resp.extensions["flowbusters_sequence"]
    except Exception:
        return None


def cap(resp, method, url, body):
    try:
        parsed = resp.json()
    except Exception:
        parsed = resp.text
    return {
        "sequence": seq(resp),
        "status_code": resp.status_code,
        "complete": True,
        "request": {"method": method, "url": url, "body": body},
        "response": {"actual": parsed},
    }


def order_of(payload):
    if isinstance(payload, dict):
        o = payload.get("order")
        if isinstance(o, dict):
            return o
    return {}


def run_invariant(after_obj):
    o = order_of(after_obj)
    total = o.get("totalReturned")
    original = o.get("originalAmount")
    if not isinstance(total, (int, float)) or isinstance(total, bool) \
       or not isinstance(original, (int, float)) or isinstance(original, bool):
        raise ValueError("totalReturned/originalAmount missing or non-numeric in AFTER state")
    return total > original, f"totalReturned={total} > originalAmount={original}"


async def probe():
    async with httpx.AsyncClient(timeout=30.0) as client:
        setup = []
        r = await client.post(RESET); setup.append(cap(r, "POST", RESET, None))

        r = await client.get(READ); before = cap(r, "GET", READ, None)

        actions = []
        r = await client.post(PRICE_ADJUSTMENT, json=TAMPER_BODY)
        actions.append(cap(r, "POST", PRICE_ADJUSTMENT, TAMPER_BODY))
        r = await client.post(REQUEST_REFUND)
        actions.append(cap(r, "POST", REQUEST_REFUND, None))
        r = await client.post(COMPLETE_REFUND)
        actions.append(cap(r, "POST", COMPLETE_REFUND, None))

        r = await client.get(READ); after = cap(r, "GET", READ, None)

        observed, description = run_invariant(after["response"]["actual"])

        verification = {
            "predicate": "business_rule_must_hold",
            "rule": {
                "source": "user",
                "reference": "A customer's cumulative refunds and adjustments must never exceed the amount originally paid for the order.",
            },
            "setup": setup,
            "before": before,
            "actions": actions,
            "after": after,
            "invariant": {
                "operator": "sum_lte",
                "terms": [["order", "totalReturned"]],
                "limit": ["order", "originalAmount"],
            },
            "violation": {"observed": observed, "description": description},
        }

        result = {
            "title": "Price adjustment + refund must not honor a client-inflated adjustment (totalReturned <= originalAmount)",
            "mutation_type": MUTATION_TYPE,
            "url": PRICE_ADJUSTMENT,
            "status_code": after["status_code"],
            "outcome": "CONFIRMED" if observed else "NOT_REPRODUCED",
            "expected_rejection": True,
            "verification": verification,
        }
        print(json.dumps(result))


if __name__ == "__main__":
    asyncio.run(probe())