"""
FlowBusters Mutation: MASS_ASSIGNMENT
Target: POST http://localhost:3000/api/order/refund/request
Attack: Send privileged fields (role, is_admin, status, balance, verified, completedAmount)
       in the refund request body. The UI never sends these. A correct server ignores
       or rejects them; a vulnerable server persists them.
       Predicate: unsupported_business_rule — we cannot express "admin fields must not
       stick" via sum_lte; retain the evidence for manual review.
"""
import asyncio
import json
import httpx

BASE = "http://localhost:3000"
RESET_URL = f"{BASE}/api/demo/reset"
ORDER_URL = f"{BASE}/api/order"
REFUND_REQ_URL = f"{BASE}/api/order/refund/request"


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


async def probe():
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        reset_cap = _capture(await client.post(RESET_URL), "POST", RESET_URL, None)
        before = _capture(await client.get(ORDER_URL), "GET", ORDER_URL, None)
        body = {
            "order_id": "1042",
            "requestedAmount": 100,
            # Privileged fields the UI never sends:
            "role": "admin",
            "is_admin": True,
            "status": "completed",
            "balance": 999999,
            "verified": True,
            "completedAmount": 100,
        }
        req_cap = _capture(await client.post(REFUND_REQ_URL, json=body),
                           "POST", REFUND_REQ_URL, body)
        after = _capture(await client.get(ORDER_URL), "GET", ORDER_URL, None)

        # Inspect the after body for any privileged fields sticking (informational).
        order = after["response"]["actual"].get("order", {})
        leaked = {}
        for field in ("role", "is_admin", "balance", "verified"):
            if field in order:
                leaked[field] = order[field]
        # Also inspect refund sub-object for completedAmount sticking without completion.
        refund = order.get("refund", {})
        if isinstance(refund, dict) and refund.get("status") == "completed" and refund.get("requestedAt") is None:
            leaked["refund.status_completed_without_request"] = True

        result = {
            "title": "MASS_ASSIGNMENT: privileged fields in refund request body",
            "mutation_type": "MASS_ASSIGNMENT",
            "url": REFUND_REQ_URL,
            "status_code": req_cap["status_code"],
            "outcome": "NEEDS_REVIEW",
            "expected_rejection": True,
            "leaked_fields_observed": leaked,
            "verification": {
                "predicate": "unsupported_business_rule",
                "rule": {
                    "source": "agent_inference",
                    "reference": (
                        "Agent hypothesis: the refund-request endpoint must ignore "
                        "privileged body fields (role, is_admin, status, balance, verified). "
                        "UI never sends them; a vulnerable server would persist them."
                    ),
                },
                "unsupported_reason": (
                    "The intended check is a set-membership / field-absence predicate over "
                    "the AFTER response, which the sum_lte invariant cannot express. "
                    "Emit NEEDS_REVIEW; human review of the leaked_fields_observed field is required."
                ),
                "setup": [reset_cap],
                "before": before,
                "actions": [req_cap],
                "after": after,
            },
        }
        print(json.dumps(result))


if __name__ == "__main__":
    asyncio.run(probe())