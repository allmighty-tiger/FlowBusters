"""
STALE DUPLICATE — do not use.
The DOUBLE_SPEND concurrent-refund probe is implemented in
08_double_spend_concurrent_refund.py. This file is kept only as an inert,
syntactically valid no-op so it cannot break the mutation set.
"""


async def probe():
    import json
    print(json.dumps({
        "title": "Stale duplicate; see 08_double_spend_concurrent_refund.py",
        "mutation_type": "DOUBLE_SPEND",
        "url": "http://localhost:3000/api/order/refund/request",
        "status_code": None,
        "outcome": "NOT_EXECUTED",
        "expected_rejection": True,
    }))


if __name__ == "__main__":
    import asyncio
    asyncio.run(probe())