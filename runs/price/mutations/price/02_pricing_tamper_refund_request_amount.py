"""
FlowBusters Mutation: PRICING_TAMPER
Target: POST http://localhost:3000/api/order/refund/request (inferred from endpoint_catalog.json)
Attack: Send a refund request with an inflated requestedAmount, then complete it, and read
       the final GET. Business invariant: order.totalReturned <= order.originalAmount.
       The UI never exposes an amount field; a trusted server recomputes payout.
"""
import asyncio
import json
import httpx

BASE = "http://localhost:3000"
RESET_URL = f"{BASE}/api/demo/reset"
ORDER_URL = f"{BASE}/api/order"
REFUND_REQ_URL = f"{BASE}/api/order/refund/request"
REFUND_COMPLETE_URL = f"{BASE}/api/order/refund/complete"

RULE = {
    "source": "observed_ui",
    "provenance": {
        "schema_version": 1,
        "source_run": "price",
        "artifact": "state_map.json",
        "rule_id": "UIR-002",
        "fact_ids": ["UIR-002-F2"],
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

        # Attacker body: claim $5000, far above the $100 originalAmount.
        tampered_body = {"order_id": "1042", "requestedAmount": 5000, "reason": "customer complaint"}
        req_cap = _capture(await client.post(REFUND_REQ_URL, json=tampered_body),
                           "POST", REFUND_REQ_URL, tampered_body)
        # Complete the refund so a payout actually lands (a pending request is not a payout test).
        complete_body = {"order_id": "1042"}
        complete_cap = _capture(await client.post(REFUND_COMPLETE_URL, json=complete_body),
                                "POST", REFUND_COMPLETE_URL, complete_body)
        after = _capture(await client.get(ORDER_URL), "GET", ORDER_URL, None)

        observed = _observed(after["response"]["actual"])
        order = after["response"]["actual"]["order"]
        result = {
            "title": "PRICING_TAMPER: client-supplied requestedAmount=5000 on refund request",
            "mutation_type": "PRICING_TAMPER",
            "url": REFUND_REQ_URL,
            "status_code": req_cap["status_code"],
            "outcome": "CONFIRMED" if observed else "NOT_REPRODUCED",
            "expected_rejection": True,
            "verification": {
                "predicate": "business_rule_must_hold",
                "rule": RULE,
                "setup": [reset_cap],
                "before": before,
                "actions": [req_cap, complete_cap],
                "after": after,
                "invariant": INV,
                "violation": {
                    "observed": bool(observed),
                    "description": (
                        f"Sent requestedAmount=5000 then completed. Final order.totalReturned="
                        f"{order.get('totalReturned')} vs originalAmount={order.get('originalAmount')}."
                    ),
                },
            },
        }
        print(json.dumps(result))


if __name__ == "__main__":
    asyncio.run(probe())