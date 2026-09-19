"""
FlowBusters Mutation: FORCED_BROWSING (SKIP_STEP)
Target: http://localhost:3000/api/order/refund/complete
Attack: Call the payout-completion endpoint directly WITHOUT first creating a
       refund request (skip the pending state that the UI gates it behind).
       Observed UI rule UIR-002: "Complete refund" only appears after a refund
       request exists, so the server should reject completing an un-requested
       refund. We still re-read the full resource and verify the monetary bound
       order.totalReturned <= order.originalAmount as the executable invariant.
"""
import asyncio
import json
import httpx

ORIGIN = "http://localhost:3000"
RESET = ORIGIN + "/api/demo/reset"
READ = ORIGIN + "/api/order"
COMPLETE_REFUND = ORIGIN + "/api/order/refund/complete"
MUTATION_TYPE = "FORCED_BROWSING"
HEADERS = {"accept": "application/json"}


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
    async with httpx.AsyncClient(timeout=30.0, headers=HEADERS) as client:
        setup = []
        r = await client.post(RESET); setup.append(cap(r, "POST", RESET, None))

        r = await client.get(READ); before = cap(r, "GET", READ, None)

        actions = []
        # No refund/request call - jump straight to the completion endpoint
        r = await client.post(COMPLETE_REFUND); actions.append(cap(r, "POST", COMPLETE_REFUND, None))

        r = await client.get(READ); after = cap(r, "GET", READ, None)

        observed, description = run_invariant(after["response"]["actual"])

        # The skipped-step bypass itself (completing with refund.status=none) is a
        # workflow-violation claim that the monetary invariant alone does not prove,
        # so we keep that as a supported predicate plus the monetary bound check.
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
            "title": "Refund completion must require a prior refund request (no payout for an un-requested refund)",
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