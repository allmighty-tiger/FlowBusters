"""
FlowBusters Mutation: STATE_INTERLEAVING
Target: POST http://localhost:3000/api/order/cancel + POST http://localhost:3000/api/order/price-adjustment
Attack: Co-visible at UI step 3 (button "Cancel order" + button "Request price adjustment").
       UI removes the Cancel affordance once a price adjustment completes (UIR-001).
       Test both orders A→B and a concurrent A/B race.
       Business invariant: after either or both, order.totalReturned <= order.originalAmount.
"""
import asyncio
import json
import httpx

BASE = "http://localhost:3000"
RESET_URL = f"{BASE}/api/demo/reset"
ORDER_URL = f"{BASE}/api/order"
CANCEL_URL = f"{BASE}/api/order/cancel"
ADJUST_URL = f"{BASE}/api/order/price-adjustment"

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

INVENTORY = [
    {"operator": "sum_lte",
     "terms": [["order", "totalReturned"]],
     "limit": ["order", "originalAmount"]},
]


def _seq(resp):
    # The backend harness writes the authoritative sequence onto the response.
    try:
        return int(resp.extensions["flowbusters_sequence"])
    except Exception:
        return None


def _capture(resp):
    seq = _seq(resp)
    if seq is None:
        raise RuntimeError("flowbusters_sequence missing from response.extensions")
    try:
        body = resp.json()
    except Exception:
        body = resp.text
    return {
        "sequence": seq,
        "status_code": resp.status_code,
        "complete": True,
        "request": None,  # filled in by caller per call site
        "response": {"actual": body},
    }


def _invariant_observed(after_body):
    """Compute sum_lte: order.totalReturned > order.originalAmount."""
    order = after_body.get("order") if isinstance(after_body, dict) else None
    if not isinstance(order, dict):
        raise RuntimeError("AFTER response missing order object")
    tr = order.get("totalReturned")
    oa = order.get("originalAmount")
    if not isinstance(tr, (int, float)) or not isinstance(oa, (int, float)):
        raise RuntimeError("totalReturned/originalAmount not numeric")
    return tr > oa


async def _reset(client):
    r = await client.post(RESET_URL)
    return _capture(r), {"method": "POST", "url": RESET_URL, "body": None}


async def _before(client):
    r = await client.get(ORDER_URL)
    cap = _capture(r)
    cap["request"] = {"method": "GET", "url": ORDER_URL, "body": None}
    return cap


async def _after(client):
    r = await client.get(ORDER_URL)
    cap = _capture(r)
    cap["request"] = {"method": "GET", "url": ORDER_URL, "body": None}
    return cap


async def _cancel(client):
    r = await client.post(CANCEL_URL)
    cap = _capture(r)
    cap["request"] = {"method": "POST", "url": CANCEL_URL, "body": None}
    return cap


async def _adjust(client):
    r = await client.post(ADJUST_URL)
    cap = _capture(r)
    cap["request"] = {"method": "POST", "url": ADJUST_URL, "body": None}
    return cap


async def _scenario_AB(client):
    """cancel -> price-adjustment. Invariant on the final GET."""
    setup = []
    setup.append(await _reset(client))
    before = await _before(client)
    a1 = await _cancel(client)
    a2 = await _adjust(client)
    after = await _after(client)
    inv = INVENTORY[0]
    observed = _invariant_observed(after["response"]["actual"])
    return {
        "setup": setup,
        "before": before,
        "actions": [a1, a2],
        "after": after,
        "invariant": inv,
        "violation": {
            "observed": bool(observed),
            "description": (
                f"After cancel then price-adjustment, order.totalReturned="
                f"{after['response']['actual']['order']['totalReturned']} "
                f"vs originalAmount={after['response']['actual']['order']['originalAmount']}."
            ),
        },
    }


async def _scenario_race(client):
    """Concurrent cancel + price-adjustment. Each scenario bracket is independent; this is a supplementary scenario."""
    setup = [await _reset(client)]
    before = await _before(client)
    c_res, a_res = await asyncio.gather(_cancel(client), _adjust(client))
    after = await _after(client)
    inv = INVENTORY[0]
    observed = _invariant_observed(after["response"]["actual"])
    return {
        "setup": setup,
        "before": before,
        "actions": [c_res, a_res],
        "after": after,
        "invariant": inv,
        "violation": {
            "observed": bool(observed),
            "description": (
                "After concurrent cancel + price-adjustment, "
                f"order.totalReturned={after['response']['actual']['order']['totalReturned']} "
                f"vs originalAmount={after['response']['actual']['order']['originalAmount']}."
            ),
        },
    }


async def probe():
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        # Primary scenario: A→B (cancel then price-adjustment)
        primary = await _scenario_AB(client)
        # Supplementary scenario: concurrent race
        supp = await _scenario_race(client)

        observed = primary["violation"]["observed"]
        outcome = "CONFIRMED" if observed else "NOT_REPRODUCED"

        result = {
            "title": "STATE_INTERLEAVING: cancel + price-adjustment may double-reimburse",
            "mutation_type": "STATE_INTERLEAVING",
            "url": CANCEL_URL,
            "status_code": primary["actions"][0]["status_code"],
            "outcome": outcome,
            "expected_rejection": True,
            "verification": {
                "predicate": "business_rule_must_hold",
                "rule": RULE,
                **primary,
                "supplementary_scenarios": {"race_cancel_and_adjust": supp},
            },
        }
        print(json.dumps(result))


if __name__ == "__main__":
    asyncio.run(probe())