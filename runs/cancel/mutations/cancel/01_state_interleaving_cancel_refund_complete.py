"""
FlowBusters Mutation: STATE_INTERLEAVING
Target: http://localhost:3000/api/order/cancel + http://localhost:3000/api/order/refund/complete
Attack: The recorded flow shows a paid, delivered order where "Cancel order" and
"Request refund" are CO-VISIBLE (availableActions.cancelOrder / requestRefund
both true at step 1). Cancellation issues a $100 reimbursement; refund completion
issues a separate payout. We test A->B (cancel then refund/complete), B->A
(refund/complete then cancel) and a concurrent cancel+complete race, then re-read
the order. Invariant: order.totalReturned must never exceed order.originalAmount
(Northstar monetary cap; 100 > 100 = false).
"""
import asyncio
import json
import httpx

BASE = "http://localhost:3000"
READ = BASE + "/api/order"
RESET = BASE + "/api/demo/reset"
CANCEL = BASE + "/api/order/cancel"
COMPLETE = BASE + "/api/order/refund/complete"
MUTATION_TYPE = "STATE_INTERLEAVING"

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
        "rule_id": "UIR-001",
        "fact_ids": ["UIR-001-F1", "UIR-001-F2"],
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
            "totalReturned=%r originalAmount=%r not finite numbers"
            % (total, original)
        )
    return total > original


def scenario(setup, before, actions, after):
    observed = compute_violation(after)
    order = after["response"]["actual"]["order"]
    return {
        "setup": setup,
        "before": before,
        "actions": actions,
        "after": after,
        "invariant": INVARIANT,
        "violation": {
            "observed": observed,
            "description": (
                "order.totalReturned=%s vs order.originalAmount=%s (violation=%s)"
                % (order.get("totalReturned"), order.get("originalAmount"), observed)
            ),
        },
    }


async def probe():
    async with httpx.AsyncClient(timeout=30.0) as client:
        # Scenario A (primary): cancel THEN refund/complete
        s_a = [cap(await client.post(RESET), "POST", RESET, None)]
        b_a = cap(await client.get(READ), "GET", READ, None)
        act_a = [
            cap(await client.post(CANCEL), "POST", CANCEL, None),
            cap(await client.post(COMPLETE), "POST", COMPLETE, None),
        ]
        f_a = cap(await client.get(READ), "GET", READ, None)
        sc_a = scenario(s_a, b_a, act_a, f_a)

        # Scenario B: refund/complete THEN cancel
        s_b = [cap(await client.post(RESET), "POST", RESET, None)]
        b_b = cap(await client.get(READ), "GET", READ, None)
        act_b = [
            cap(await client.post(COMPLETE), "POST", COMPLETE, None),
            cap(await client.post(CANCEL), "POST", CANCEL, None),
        ]
        f_b = cap(await client.get(READ), "GET", READ, None)
        sc_b = scenario(s_b, b_b, act_b, f_b)

        # Scenario C: concurrent cancel + refund/complete race
        s_c = [cap(await client.post(RESET), "POST", RESET, None)]
        b_c = cap(await client.get(READ), "GET", READ, None)
        r_cancel, r_complete = await asyncio.gather(
            client.post(CANCEL), client.post(COMPLETE)
        )
        act_c = [
            cap(r_cancel, "POST", CANCEL, None),
            cap(r_complete, "POST", COMPLETE, None),
        ]
        f_c = cap(await client.get(READ), "GET", READ, None)
        sc_c = scenario(s_c, b_c, act_c, f_c)

    result = {
        "title": "Cancel + refund-complete interleaving can stack two reimbursements on order #1042",
        "mutation_type": MUTATION_TYPE,
        "url": CANCEL,
        "status_code": f_a["status_code"],
        "outcome": "NEEDS_REVIEW",
        "expected_rejection": True,
        "verification": {
            "predicate": "business_rule_must_hold",
            "rule": RULE,
            "setup": s_a,
            "before": b_a,
            "actions": act_a,
            "after": f_a,
            "invariant": sc_a["invariant"],
            "violation": sc_a["violation"],
            "supplementary_scenarios": {
                "refund_complete_then_cancel": sc_b,
                "cancel_and_complete_concurrent": sc_c,
            },
        },
    }
    print(json.dumps(result))


if __name__ == "__main__":
    asyncio.run(probe())