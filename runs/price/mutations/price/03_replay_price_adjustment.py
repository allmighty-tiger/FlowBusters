"""
FlowBusters Mutation: REPLAY_ATTACK
Target: POST http://localhost:3000/api/order/price-adjustment
Attack: The UI hides "Request price adjustment" once priceProtection.status=completed
       (observed in UIR-002). Send the same bodyless POST twice and read the final state.
       Business invariant: order.totalReturned <= order.originalAmount.
"""
import asyncio
import json
import httpx

BASE = "http://localhost:3000"
RESET_URL = f"{BASE}/api/demo/reset"
ORDER_URL = f"{BASE}/api/order"
ADJUST_URL = f"{BASE}/api/order/price-adjustment"

RULE = {
    "source": "observed_ui",
    "provenance": {
        "schema_version": 1,
        "source_run": "price",
        "artifact": "state_map.json",
        "rule_id": "UIR-002",
        "fact_ids": ["UIR-002-F1", "UIR-002-F2", "UIR-002-F3"],
    },
}

INV = {"operator": "sum_lte",
       "terms": [["order", "totalReturned"]],
       "limit": ["order", "originalAmount"]}


def _seq(resp):
    try:
        return int(resp.extensions["flowbusters_sequence"])
    except Exception:
        return None


def _capture(resp, method, url, body):
    seq = _seq(resp)
    if seq is None:
        raise RuntimeError("flowbusters_sequence missing")
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


def _observed(after_body):
    order = after_body["order"]
    tr = order["totalReturned"]
    oa = order["originalAmount"]
    if not isinstance(tr, (int, float)) or not isinstance(oa, (int, float)):
        raise RuntimeError("totalReturned/originalAmount not numeric")
    return tr > oa


async def probe():
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        reset_cap = _capture(await client.post(RESET_URL), "POST", RESET_URL, None)
        before = _capture(await client.get(ORDER_URL), "GET", ORDER_URL, None)
        first = _capture(await client.post(ADJUST_URL), "POST", ADJUST_URL, None)
        replay = _capture(await client.post(ADJUST_URL), "POST", ADJUST_URL, None)
        after = _capture(await client.get(ORDER_URL), "GET", ORDER_URL, None)

        observed = _observed(after["response"]["actual"])
        order = after["response"]["actual"]["order"]
        result = {
            "title": "REPLAY_ATTACK: POST /api/order/price-adjustment replayed after completion",
            "mutation_type": "REPLAY_ATTACK",
            "url": ADJUST_URL,
            "status_code": replay["status_code"],
            "outcome": "CONFIRMED" if observed else "NOT_REPRODUCED",
            "expected_rejection": True,
            "verification": {
                "predicate": "business_rule_must_hold",
                "rule": RULE,
                "setup": [reset_cap],
                "before": before,
                "actions": [first, replay],
                "after": after,
                "invariant": INV,
                "violation": {
                    "observed": bool(observed),
                    "description": (
                        f"Replayed price-adjustment after completion. Final totalReturned="
                        f"{order.get('totalReturned')} vs originalAmount={order.get('originalAmount')}."
                    ),
                },
            },
        }
        print(json.dumps(result))


if __name__ == "__main__":
    asyncio.run(probe())