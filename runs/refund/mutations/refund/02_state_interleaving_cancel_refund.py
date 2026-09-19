"""
FlowBusters Mutation: STATE_INTERLEAVING
Targets: http://localhost:3000/api/order/cancel (A) and /api/order/refund/complete (B)
Attack: In the recorded baseline, "Cancel order" and "Request refund" were
        CO-VISIBLE. A completed refund and a cancellation reimbursement are two
        forms of the same reimbursement. From each clean starting state we test:
           A->B (refund then cancel), B->A (cancel then refund), and a concurrent
           A/B race, re-reading the full order after each scenario and asserting
           the business invariant order.totalReturned <= order.originalAmount.
"""
import asyncio
import json
import httpx

ORIGIN = "http://localhost:3000"
RESET = ORIGIN + "/api/demo/reset"
READ = ORIGIN + "/api/order"
REQUEST_REFUND = ORIGIN + "/api/order/refund/request"
COMPLETE_REFUND = ORIGIN + "/api/order/refund/complete"
CANCEL = ORIGIN + "/api/order/cancel"
MUTATION_TYPE = "STATE_INTERLEAVING"

INVARIANT = {
    "operator": "sum_lte",
    "terms": [["order", "totalReturned"]],
    "limit": ["order", "originalAmount"],
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


def order_of(resp_obj):
    if isinstance(resp_obj, dict):
        o = resp_obj.get("order")
        if isinstance(o, dict):
            return o
    return {}


def eval_invariant(after_obj):
    o = order_of(after_obj)
    total = o.get("totalReturned")
    original = o.get("originalAmount")
    if not isinstance(total, (int, float)) or isinstance(total, bool) \
       or not isinstance(original, (int, float)) or isinstance(original, bool):
        raise ValueError("totalReturned/originalAmount missing or non-numeric in AFTER state")
    return total > original, f"totalReturned={total} > originalAmount={original}"


async def scenario_ab(client):
    """Complete refund (B), then cancel (A)."""
    setup = []
    r = await client.post(RESET); setup.append(cap(r, "POST", RESET, None))
    r = await client.get(READ); before = cap(r, "GET", READ, None)
    actions = []
    r = await client.post(REQUEST_REFUND); actions.append(cap(r, "POST", REQUEST_REFUND, None))
    r = await client.post(COMPLETE_REFUND); actions.append(cap(r, "POST", COMPLETE_REFUND, None))
    r = await client.post(CANCEL); actions.append(cap(r, "POST", CANCEL, None))
    r = await client.get(READ); after = cap(r, "GET", READ, None)
    observed, desc = eval_invariant(after["response"]["actual"])
    return {"setup": setup, "before": before, "actions": actions, "after": after,
            "invariant": INVARIANT, "violation": {"observed": observed, "description": desc}}, observed


async def scenario_ba(client):
    """Cancel (A), then request+complete refund (B)."""
    setup = []
    r = await client.post(RESET); setup.append(cap(r, "POST", RESET, None))
    r = await client.get(READ); before = cap(r, "GET", READ, None)
    actions = []
    r = await client.post(CANCEL); actions.append(cap(r, "POST", CANCEL, None))
    r = await client.post(REQUEST_REFUND); actions.append(cap(r, "POST", REQUEST_REFUND, None))
    r = await client.post(COMPLETE_REFUND); actions.append(cap(r, "POST", COMPLETE_REFUND, None))
    r = await client.get(READ); after = cap(r, "GET", READ, None)
    observed, desc = eval_invariant(after["response"]["actual"])
    return {"setup": setup, "before": before, "actions": actions, "after": after,
            "invariant": INVARIANT, "violation": {"observed": observed, "description": desc}}, observed


async def scenario_race(client):
    """Concurrent A/B from a clean pending state."""
    setup = []
    r = await client.post(RESET); setup.append(cap(r, "POST", RESET, None))
    r = await client.get(READ); before = cap(r, "GET", READ, None)
    r = await client.post(REQUEST_REFUND); setup.append(cap(r, "POST", REQUEST_REFUND, None))
    results = await asyncio.gather(client.post(CANCEL), client.post(COMPLETE_REFUND))
    actions = []
    for resp in results:
        if str(resp.url).endswith("/cancel"):
            actions.append(cap(resp, "POST", CANCEL, None))
        else:
            actions.append(cap(resp, "POST", COMPLETE_REFUND, None))
    r = await client.get(READ); after = cap(r, "GET", READ, None)
    observed, desc = eval_invariant(after["response"]["actual"])
    return {"setup": setup, "before": before, "actions": actions, "after": after,
            "invariant": INVARIANT, "violation": {"observed": observed, "description": desc}}, observed


async def probe():
    async with httpx.AsyncClient(timeout=30.0) as client:
        ab, ab_obs = await scenario_ab(client)
        ba, ba_obs = await scenario_ba(client)
        race, race_obs = await scenario_race(client)

        verification = {
            "predicate": "business_rule_must_hold",
            "rule": {
                "source": "observed_ui",
                "provenance": {
                    "schema_version": 1,
                    "source_run": "refund",
                    "artifact": "state_map.json",
                    "rule_id": "UIR-001",
                    "fact_ids": ["UIR-001-F1", "UIR-001-F2"],
                },
            },
            "setup": ab["setup"],
            "before": ab["before"],
            "actions": ab["actions"],
            "after": ab["after"],
            "invariant": ab["invariant"],
            "violation": ab["violation"],
            "supplementary_scenarios": {
                "cancel_then_refund": ba,
                "cancel_refund_race": race,
            },
        }

        observed = bool(ab_obs or ba_obs or race_obs)
        result = {
            "title": "Cancel order + complete refund must not double-reimburse (totalReturned <= originalAmount)",
            "mutation_type": MUTATION_TYPE,
            "url": CANCEL,
            "status_code": ab["after"]["status_code"],
            "outcome": "CONFIRMED" if observed else "NOT_REPRODUCED",
            "expected_rejection": True,
            "verification": verification,
        }
        print(json.dumps(result))


if __name__ == "__main__":
    asyncio.run(probe())