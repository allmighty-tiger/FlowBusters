"""
FlowBusters Mutation: STATE_INTERLEAVING
Target: /api/order/cancel + /api/order/price-adjustment
Attack: "Cancel order" and "Request price adjustment" are co-visible at step 1
(both payout paths). Test (A) cancel then price-adjustment, (B) price-adjustment
then cancel, and (C) a concurrent cancel + price-adjustment race, then re-read.
After cancellation the UI removes the adjustment button (UIR-003); the server must
also refuse to pay out the $30 adjustment on a canceled/reimbursed order.
Invariant: order.totalReturned must not exceed order.originalAmount.
"""
import asyncio
import json
import httpx

BASE = "http://localhost:3000"
READ = BASE + "/api/order"
RESET = BASE + "/api/demo/reset"
CANCEL = BASE + "/api/order/cancel"
ADJUST = BASE + "/api/order/price-adjustment"
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
        "rule_id": "UIR-003",
        "fact_ids": ["UIR-003-F1", "UIR-003-F2"],
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


def scenario(setup, before, actions, after):
    o = after["response"]["actual"]["order"]
    return {
        "setup": setup,
        "before": before,
        "actions": actions,
        "after": after,
        "invariant": INVARIANT,
        "violation": {
            "observed": compute_violation(after),
            "description": "order.totalReturned=%s vs order.originalAmount=%s"
            % (o.get("totalReturned"), o.get("originalAmount")),
        },
    }


async def probe():
    async with httpx.AsyncClient(timeout=30.0) as client:
        # A (primary): cancel THEN price-adjustment
        s_a = [cap(await client.post(RESET), "POST", RESET, None)]
        b_a = cap(await client.get(READ), "GET", READ, None)
        act_a = [
            cap(await client.post(CANCEL), "POST", CANCEL, None),
            cap(await client.post(ADJUST), "POST", ADJUST, None),
        ]
        f_a = cap(await client.get(READ), "GET", READ, None)
        sc_a = scenario(s_a, b_a, act_a, f_a)

        # B: price-adjustment THEN cancel
        s_b = [cap(await client.post(RESET), "POST", RESET, None)]
        b_b = cap(await client.get(READ), "GET", READ, None)
        act_b = [
            cap(await client.post(ADJUST), "POST", ADJUST, None),
            cap(await client.post(CANCEL), "POST", CANCEL, None),
        ]
        f_b = cap(await client.get(READ), "GET", READ, None)
        sc_b = scenario(s_b, b_b, act_b, f_b)

        # C: concurrent cancel + price-adjustment race
        s_c = [cap(await client.post(RESET), "POST", RESET, None)]
        b_c = cap(await client.get(READ), "GET", READ, None)
        r_cancel, r_adjust = await asyncio.gather(
            client.post(CANCEL), client.post(ADJUST)
        )
        act_c = [
            cap(r_cancel, "POST", CANCEL, None),
            cap(r_adjust, "POST", ADJUST, None),
        ]
        f_c = cap(await client.get(READ), "GET", READ, None)
        sc_c = scenario(s_c, b_c, act_c, f_c)

    result = {
        "title": "Price adjustment can still pay out on a canceled/reimbursed order",
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
                "adjustment_then_cancel": sc_b,
                "cancel_and_adjustment_concurrent": sc_c,
            },
        },
    }
    print(json.dumps(result))


if __name__ == "__main__":
    asyncio.run(probe())