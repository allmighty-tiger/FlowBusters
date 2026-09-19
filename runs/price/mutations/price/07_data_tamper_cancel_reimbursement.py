"""
FlowBusters Mutation: DATA_TAMPER
Target: POST http://localhost:3000/api/order/cancel
Attack: Send a body claiming reimbursementAmount=10000 on the cancel call.
       UI never exposes an amount; a trusted server recomputes reimbursement server-side.
       Business invariant: order.totalReturned <= order.originalAmount.
"""
import asyncio
import json
import httpx

BASE = "http://localhost:3000"
RESET_URL = f"{BASE}/api/demo/reset"
ORDER_URL = f"{BASE}/api/order"
CANCEL_URL = f"{BASE}/api/order/cancel"

RULE = {
    "source": "observed_ui",
    "provenance": {
        "schema_version": 1,
        "source_run": "price",
        "artifact": "state_map.json",
        "rule_id": "UIR-001",
        "fact_ids": ["UIR-001-F1"],
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
        body = {"order_id": "1042", "reimbursementAmount": 10000, "reason": "test"}
        cancel_cap = _capture(await client.post(CANCEL_URL, json=body),
                              "POST", CANCEL_URL, body)
        after = _capture(await client.get(ORDER_URL), "GET", ORDER_URL, None)

        observed = _observed(after["response"]["actual"])
        order = after["response"]["actual"]["order"]
        result = {
            "title": "DATA_TAMPER: cancel with reimbursementAmount=10000 in body",
            "mutation_type": "DATA_TAMPER",
            "url": CANCEL_URL,
            "status_code": cancel_cap["status_code"],
            "outcome": "CONFIRMED" if observed else "NOT_REPRODUCED",
            "expected_rejection": True,
            "verification": {
                "predicate": "business_rule_must_hold",
                "rule": RULE,
                "setup": [reset_cap],
                "before": before,
                "actions": [cancel_cap],
                "after": after,
                "invariant": INV,
                "violation": {
                    "observed": bool(observed),
                    "description": (
                        f"Cancel with reimbursementAmount=10000. Final totalReturned="
                        f"{order.get('totalReturned')} vs originalAmount={order.get('originalAmount')}."
                    ),
                },
            },
        }
        print(json.dumps(result))


if __name__ == "__main__":
    asyncio.run(probe())