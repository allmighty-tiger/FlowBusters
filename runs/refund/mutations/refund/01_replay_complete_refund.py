"""
FlowBusters Mutation: REPLAY_ATTACK
Target: http://localhost:3000/api/order/refund/complete
Attack: Complete the refund normally, then REPLAY the payout-completion call.
       The server must not pay out the customer twice. The business invariant
       that must hold: order.totalReturned <= order.originalAmount.
"""
import asyncio
import json
import httpx

ORIGIN = "http://localhost:3000"
RESET = ORIGIN + "/api/demo/reset"          # setup fixture only, never an attack action
READ = ORIGIN + "/api/order"                 # independent state reader
REQUEST_REFUND = ORIGIN + "/api/order/refund/request"
COMPLETE_REFUND = ORIGIN + "/api/order/refund/complete"
MUTATION_TYPE = "REPLAY_ATTACK"


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


def order_of(resp_obj):
    if isinstance(resp_obj, dict):
        o = resp_obj.get("order")
        if isinstance(o, dict):
            return o
    return {}


def run_invariant(after_obj):
    """Return (observed_bool, description) using the AFTER response only."""
    o = order_of(after_obj)
    total = o.get("totalReturned")
    original = o.get("originalAmount")
    if not isinstance(total, (int, float)) or isinstance(total, bool) \
       or not isinstance(original, (int, float)) or isinstance(original, bool):
        raise ValueError("totalReturned/originalAmount missing or non-numeric in AFTER state")
    observed = total > original
    return observed, f"totalReturned={total} > originalAmount={original}"


async def probe():
    async with httpx.AsyncClient(timeout=30.0) as client:
        setup = []
        r = await client.post(RESET); setup.append(cap(r, "POST", RESET, None))
        r = await client.post(RESET); setup.append(cap(r, "POST", RESET, None))

        r = await client.get(READ); before = cap(r, "GET", READ, None)

        actions = []
        r = await client.post(REQUEST_REFUND); actions.append(cap(r, "POST", REQUEST_REFUND, None))
        r = await client.post(COMPLETE_REFUND); actions.append(cap(r, "POST", COMPLETE_REFUND, None))
        # Replay the payout completion - must not double-pay
        r = await client.post(COMPLETE_REFUND); actions.append(cap(r, "POST", COMPLETE_REFUND, None))

        r = await client.get(READ); after = cap(r, "GET", READ, None)

        observed, description = run_invariant(after["response"]["actual"])

        verification = {
            "predicate": "business_rule_must_hold",
            "rule": {
                "source": "observed_ui",
                "provenance": {
                    "schema_version": 1,
                    "source_run": "refund",
                    "artifact": "state_map.json",
                    "rule_id": "UIR-002",
                    "fact_ids": ["UIR-002-F1"],
                },
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
            "title": "Replaying refund completion must not pay out twice (totalReturned <= originalAmount)",
            "mutation_type": MUTATION_TYPE,
            "url": COMPLETE_REFUND,
            "status_code": after["status_code"],
            "outcome": "CONFIRMED" if observed else "NOT_REPRODUCED",
            "expected_rejection": True,
            "verification": verification,
        }
        print(json.dumps(result))


if __name__ == "__main__":
    asyncio.run(probe())