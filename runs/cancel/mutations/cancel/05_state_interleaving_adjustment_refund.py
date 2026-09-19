"""
FlowBusters Mutation: STATE_INTERLEAVING
Target: /api/order/price-adjustment + /api/order/refund/request + /api/order/refund/complete
Attack: Price protection adjustment and the refund payout chain are co-visible on
the same delivered order. Each can add money to totalReturned. Test (A) adjustment
then the full refund chain, (B) the full refund chain then adjustment, and (C) a
concurrent adjustment + refund/complete race (after a refund request is
established). Invariant: order.totalReturned must not exceed order.originalAmount.
"""
import asyncio
import json
import httpx

BASE = "http://localhost:3000"
READ = BASE + "/api/order"
RESET = BASE + "/api/demo/reset"
ADJUST = BASE + "/api/order/price-adjustment"
REQUEST = BASE + "/api/order/refund/request"
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
    order = after["response"]["actual"]["order"]
    return {
        "setup": setup,
        "before": before,
        "actions": actions,
        "after": after,
        "invariant": INVARIANT,
        "violation": {
            "observed": compute_violation(after),
            "description": (
                "order.totalReturned=%s vs order.originalAmount=%s"
                % (order.get("totalReturned"), order.get("originalAmount"))
            ),
        },
    }


async def probe():
    async with httpx.AsyncClient(timeout=30.0) as client:
        # A (primary): adjustment THEN full refund chain
        s_a = [cap(await client.post(RESET), "POST", RESET, None)]
        b_a = cap(await client.get(READ), "GET", READ, None)
        act_a = [
            cap(await client.post(ADJUST), "POST", ADJUST, None),
            cap(await client.post(REQUEST), "POST", REQUEST, None),
            cap(await client.post(COMPLETE), "POST", COMPLETE, None),
        ]
        f_a = cap(await client.get(READ), "GET", READ, None)
        sc_a = scenario(s_a, b_a, act_a, f_a)

        # B: full refund chain THEN adjustment
        s_b = [cap(await client.post(RESET), "POST", RESET, None)]
        b_b = cap(await client.get(READ), "GET", READ, None)
        act_b = [
            cap(await client.post(REQUEST), "POST", REQUEST, None),
            cap(await client.post(COMPLETE), "POST", COMPLETE, None),
            cap(await client.post(ADJUST), "POST", ADJUST, None),
        ]
        f_b = cap(await client.get(READ), "GET", READ, None)
        sc_b = scenario(s_b, b_b, act_b, f_b)

        # C: establish a refund, then race adjustment + refund/complete
        s_c = [cap(await client.post(RESET), "POST", RESET, None)]
        b_c = cap(await client.get(READ), "GET", READ, None)
        act_c = [cap(await client.post(REQUEST), "POST", REQUEST, None)]
        r_adj, r_complete = await asyncio.gather(
            client.post(ADJUST), client.post(COMPLETE)
        )
        act_c += [
            cap(r_adj, "POST", ADJUST, None),
            cap(r_complete, "POST", COMPLETE, None),
        ]
        f_c = cap(await client.get(READ), "GET", READ, None)
        sc_c = scenario(s_c, b_c, act_c, f_c)

    result = {
        "title": "Price adjustment can stack on a completed refund to exceed original amount",
        "mutation_type": MUTATION_TYPE,
        "url": ADJUST,
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
                "refund_chain_then_adjustment": sc_b,
                "adjustment_and_complete_concurrent": sc_c,
            },
        },
    }
    print(json.dumps(result))


if __name__ == "__main__":
    asyncio.run(probe())