"""
FlowBusters Mutation: SKIP_STEP
Target: POST http://localhost:3000/api/order/refund/complete (inferred from endpoint_catalog.json)
Attack: Call the completion endpoint WITHOUT first requesting a refund.
       UI gates this on refund.status != 'none'. Server must reject.
       Business invariant: order.totalReturned <= order.originalAmount.
"""
import asyncio
import json
import httpx

BASE = "http://localhost:3000"
RESET_URL = f"{BASE}/api/demo/reset"
ORDER_URL = f"{BASE}/api/order"
REFUND_COMPLETE_URL = f"{BASE}/api/order/refund/complete"

RULE = {
    "source": "observed_ui",
    "provenance": {
        "schema_version": 1,
        "source_run": "price",
        "artifact": "state_map.json",
        "rule_id": "UIR-001",
        "fact_ids": ["UIR-001-F2"],
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
        body = {"order_id": "1042"}
        complete_cap = _capture(await client.post(REFUND_COMPLETE_URL, json=body),
                                "POST", REFUND_COMPLETE_URL, body)
        after = _capture(await client.get(ORDER_URL), "GET", ORDER_URL, None)

        observed = _observed(after["response"]["actual"])
        order = after["response"]["actual"]["order"]
        result = {
            "title": "SKIP_STEP: refund/complete called without a prior refund request",
            "mutation_type": "SKIP_STEP",
            "url": REFUND_COMPLETE_URL,
            "status_code": complete_cap["status_code"],
            "outcome": "CONFIRMED" if observed else "NOT_REPRODUCED",
            "expected_rejection": True,
            "verification": {
                "predicate": "business_rule_must_hold",
                "rule": RULE,
                "setup": [reset_cap],
                "before": before,
                "actions": [complete_cap],
                "after": after,
                "invariant": INV,
                "violation": {
                    "observed": bool(observed),
                    "description": (
                        f"Called /refund/complete with refund.status=none. Final totalReturned="
                        f"{order.get('totalReturned')} vs originalAmount={order.get('originalAmount')}."
                    ),
                },
            },
        }
        print(json.dumps(result))


if __name__ == "__main__":
    asyncio.run(probe())