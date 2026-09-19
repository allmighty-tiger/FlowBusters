"""
FlowBusters Mutation: DATA_TAMPER
Target: http://localhost:3000/api/order/cancel
Attack: The Cancel order action (co-visible in the baseline, inferred from the UI)
       grants a cancellation reimbursement. Send a tampered body trying to set
       the reimbursement amount to a huge value, then re-read the full order and
       check the monetary bound. A correct server recomputes the reimbursement
       server-side and totalReturned must stay <= originalAmount.
"""
import asyncio
import json
import httpx

ORIGIN = "http://localhost:3000"
RESET = ORIGIN + "/api/demo/reset"
READ = ORIGIN + "/api/order"
CANCEL = ORIGIN + "/api/order/cancel"
MUTATION_TYPE = "DATA_TAMPER"

TAMPER_BODY = {
    "reimbursementAmount": 100000,
    "amount": 100000,
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
        r = await client.post(CANCEL, json=TAMPER_BODY)
        actions.append(cap(r, "POST", CANCEL, TAMPER_BODY))

        r = await client.get(READ); after = cap(r, "GET", READ, None)

        observed, description = run_invariant(after["response"]["actual"])

        verification = {
            "predicate": "business_rule_must_hold",
            "rule": {
                "source": "user",
                "reference": "Cancellation reimbursement must never exceed the amount originally paid for the order.",
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
            "title": "Cancel order must not honor a client-inflated reimbursement amount",
            "mutation_type": MUTATION_TYPE,
            "url": CANCEL,
            "status_code": after["status_code"],
            "outcome": "CONFIRMED" if observed else "NOT_REPRODUCED",
            "expected_rejection": True,
            "verification": verification,
        }
        print(json.dumps(result))


if __name__ == "__main__":
    asyncio.run(probe())