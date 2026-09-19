"""
FlowBusters Mutation: REPLAY_ATTACK
Target: http://localhost:3000/api/order/cancel
Attack: Cancellation issues a one-time $100 reimbursement. We cancel, then replay
the identical cancel request (and a concurrent double-cancel race) and re-read.
A correct server is idempotent: the second cancel must not issue a second
reimbursement. Invariant: order.totalReturned must not exceed
order.originalAmount (a double $100 = 200 > 100 = violation).
"""
import asyncio
import json
import httpx

BASE = "http://localhost:3000"
READ = BASE + "/api/order"
RESET = BASE + "/api/demo/reset"
CANCEL = BASE + "/api/order/cancel"
MUTATION_TYPE = "REPLAY_ATTACK"

INVARIANT = {
    "operator": "sum_lte",
    "terms": [["order", "totalReturned"]],
    "limit": ["order", "originalAmount"],
}

RULE = {
    "source": "agent",
    "reference": (
        "Cancellation reimbursement is a one-time payout (observed totalReturned "
        "reaches originalAmount=100 on a single cancel). Replaying the cancel must "
        "not issue a second reimbursement. Agent inference from state_map.json; "
        "requires user/specification confirmation before CONFIRMED."
    ),
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


async def probe():
    async with httpx.AsyncClient(timeout=30.0) as client:
        # Primary: cancel then replay the same cancel
        setup = [cap(await client.post(RESET), "POST", RESET, None)]
        before = cap(await client.get(READ), "GET", READ, None)
        actions = [
            cap(await client.post(CANCEL), "POST", CANCEL, None),
            cap(await client.post(CANCEL), "POST", CANCEL, None),
        ]
        after = cap(await client.get(READ), "GET", READ, None)

        # Supplementary: concurrent double-cancel race
        s_r = [cap(await client.post(RESET), "POST", RESET, None)]
        b_r = cap(await client.get(READ), "GET", READ, None)
        r1, r2 = await asyncio.gather(client.post(CANCEL), client.post(CANCEL))
        act_r = [
            cap(r1, "POST", CANCEL, None),
            cap(r2, "POST", CANCEL, None),
        ]
        f_r = cap(await client.get(READ), "GET", READ, None)

    order = after["response"]["actual"]["order"]
    observed = compute_violation(after)

    def viol(a):
        o = a["response"]["actual"]["order"]
        return {
            "observed": compute_violation(a),
            "description": "order.totalReturned=%s vs order.originalAmount=%s"
            % (o.get("totalReturned"), o.get("originalAmount")),
        }

    result = {
        "title": "Replaying the cancel request can issue a second $100 reimbursement",
        "mutation_type": MUTATION_TYPE,
        "url": CANCEL,
        "status_code": after["status_code"],
        "outcome": "NEEDS_REVIEW",
        "expected_rejection": True,
        "verification": {
            "predicate": "business_rule_must_hold",
            "rule": RULE,
            "setup": setup,
            "before": before,
            "actions": actions,
            "after": after,
            "invariant": INVARIANT,
            "violation": viol(after),
            "supplementary_scenarios": {
                "concurrent_double_cancel": {
                    "setup": s_r,
                    "before": b_r,
                    "actions": act_r,
                    "after": f_r,
                    "invariant": INVARIANT,
                    "violation": viol(f_r),
                }
            },
        },
    }
    print(json.dumps(result))


if __name__ == "__main__":
    asyncio.run(probe())