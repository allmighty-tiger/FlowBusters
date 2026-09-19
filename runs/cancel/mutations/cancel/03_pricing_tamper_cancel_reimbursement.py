"""
FlowBusters Mutation: PRICING_TAMPER
Target: http://localhost:3000/api/order/cancel
Attack: The cancel UI (observed rule UIR-002) presents a single fixed "$100.00
reimbursement". The recorded UI sends a bodyless POST. The server must NOT trust a
client-supplied reimbursement amount. We send a JSON body with a much larger
reimbursementAmount and re-read: does the server honor the tampered amount so that
order.totalReturned exceeds order.originalAmount? Invariant: totalReturned <=
originalAmount.
"""
import asyncio
import json
import httpx

BASE = "http://localhost:3000"
READ = BASE + "/api/order"
RESET = BASE + "/api/demo/reset"
CANCEL = BASE + "/api/order/cancel"
MUTATION_TYPE = "PRICING_TAMPER"

TAMPERED_BODY = {"reimbursementAmount": 1000, "amount": 1000, "total": 1000}

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
        "rule_id": "UIR-002",
        "fact_ids": ["UIR-002-F1"],
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
            cap(
                await client.post(CANCEL, json=TAMPERED_BODY),
                "POST",
                CANCEL,
                TAMPERED_BODY,
            )
        ]
        after = cap(await client.get(READ), "GET", READ, None)

    order = after["response"]["actual"]["order"]
    observed = compute_violation(after)
    result = {
        "title": "Cancel honors a client-supplied reimbursementAmount larger than the fixed $100",
        "mutation_type": MUTATION_TYPE,
        "url": CANCEL,
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
                    "sent reimbursementAmount=1000; order.totalReturned=%s vs "
                    "order.originalAmount=%s (cancellation.reimbursementAmount=%r) "
                    "(violation=%s)"
                    % (
                        order.get("totalReturned"),
                        order.get("originalAmount"),
                        (order.get("cancellation") or {}).get("reimbursementAmount"),
                        observed,
                    )
                ),
            },
        },
    }
    print(json.dumps(result))


if __name__ == "__main__":
    asyncio.run(probe())