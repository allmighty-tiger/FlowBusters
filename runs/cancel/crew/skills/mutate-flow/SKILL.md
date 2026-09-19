# Skill: Mutate Flow

## Purpose

Read `crew/skills/probe-flow/EXECUTION.md` before authoring any script. Its
backend evidence contract supersedes the legacy output examples below. Every
script must establish and capture its own independent before/actions/after
scenario and print the required structured `verification`; status codes and a
`violation_observed` flag alone are not acceptable evidence.
Invariant paths must start at the HTTP response root and include every envelope
key (for example `["order", "totalReturned"]`). Never use nested-relative paths,
recursive field lookup, or guessed array indexes.
Use `response.extensions["flowbusters_sequence"]` as the capture sequence for
every HTTPX response. Never maintain a parallel counter. Capture setup requests
and every repeated reset in `verification.setup`; their sequences are part of
the same signed transport trace even though they are not attack actions. The
declared setup and scenario captures must account for every HTTP request once.
`supplementary_scenarios` is a mapping keyed by stable scenario name inside
`verification`; a top-level result list is invalid and cannot account for
signed transport events.
`business_rule_must_hold` requires a supported `sum_lte` invariant. If the rule
cannot be represented by a supported predicate, emit
`unsupported_business_rule` with `unsupported_reason` and keep it reviewable.
For `sum_lte`, resolve the declared response-root paths from the actual AFTER
response and print `violation.observed` as the resulting JSON boolean. Never
leave it as `None`/`null` for the backend to fill in; backend recomputation only
checks the emitted boolean against signed evidence.

Generate adversarial Python scripts that probe business logic flaws by manipulating workflow state, replaying requests, swapping roles, and tampering with data extracted from the state map.

## Confidence: medium

## When to Use

- Phase 3 of FlowBusters pipeline
- After `flows/{flow-name}/state_map.json` exists with valid transitions, roles, and critical endpoints
- Need to create targeted attack scripts for business logic testing
- The run may include an optional `--flow-name`; if omitted, use `default`

## Inputs

- Backend-owned `endpoint_catalog.json`: use exact method/origin/path entries;
  never derive a route by shortening a documented path or naming a UI button.
  An unavailable required endpoint leaves a scenario NOT_EXECUTED, with the
  missing prerequisite explained. Do not manufacture a route to get coverage.
  Runtime 404/405 does not establish that a workflow control held.

- Optional `flow-name` parameter
- `flows/{flow-name}/state_map.json`

`flow-name` must be kebab-case: lowercase letters, numbers, and hyphens only.
If `flow-name` is omitted, default to `default`.

## Procedure

### 1. Resolve Flow Paths

Resolve the flow name before mutation generation:
- Use the provided `flow-name` when present
- Otherwise use `default`
- Validate it matches `^[a-z0-9]+(?:-[a-z0-9]+)*$`

Use these directories for the run:
- Read `flows/{flow-name}/state_map.json`
- Write scripts to `mutations/{flow-name}/`

### 2. Load State Map

Read `flows/{flow-name}/state_map.json` and extract:
- All transitions (ordered by dependency)
- All roles with their full cookie/header credentials
- All critical endpoints with their attack surfaces
- All observed UI rules and transition `ui_context` (visible limits, disabled
  actions, role/ownership text, and terminal states)
- `setup_paths`, which may establish test state but must never be attacked or
  reported as a vulnerability
- `conflicting_action_pairs`, where multiple state-changing controls were
  visible in the same UI state

For each observed UI rule, ask whether the server rejects the same action when
the UI is bypassed. If `observed_ui_rules` is non-empty, generate at least one
mutation that explicitly violates one of those rules.
Read `crew/skills/analyze-har/OBSERVED_UI_RULES.md`; copy its structured rule
reference (`source_run`, `artifact`, `rule_id`, and relevant `fact_ids`) into
verification. Never replace it with a prose path or classify an API-only fact
or agent inference as observed UI.
For every conflicting action pair, generate one STATE_INTERLEAVING script that
tests A→B, B→A, and a safe concurrent A/B race from equivalent clean states,
then re-reads the complete resource to verify the invariant.

### 3. Attack Brainstorm (before writing any scripts)

For each critical endpoint, enumerate every state-changing field in its request body and work through this checklist — print your shortlist of chosen vectors to chat first:

| Question | Mutation type |
|----------|---------------|
| Which prerequisite steps could be skipped or reordered? **Including transitions the demo NEVER took** (demo only approved *pending* orders — try approving already-approved, cancelled, or other customers' orders) | SKIP_STEP |
| Which value is MONEY or quantity? If the server trusts a client-supplied price/amount/discount/quantity, it's a direct financial-loss vector — ALWAYS probe it when such fields exist | PRICING_TAMPER |
| Which body fields does the UI never send? (role, is_admin, status, ownership, balance, total, verified) | MASS_ASSIGNMENT |
| Which requests are idempotency-sensitive — create/pay/transfer? Resend the same request twice (same idempotency key/body), and also race N concurrent copies with `asyncio.gather` — no double charge/delivery/duplicate row allowed | DOUBLE_SPEND / REPLAY_ATTACK |
| Which field accepts values the UI would never produce? (negative/zero/huge quantities, floats, unicode) | DATA_TAMPER |
| Which role or ownership boundary exists? (other users' resource IDs, sequential ID enumeration, wrong-role cookies) | ROLE_SWAP / FORCED_BROWSING |
| Which visible UI rule can be violated by calling the API directly? (disabled action, one-use rule, amount limit, locked state) | SKIP_STEP / REPLAY_ATTACK / DATA_TAMPER |
| Which actions were visible at the same time, and can both effects be applied? | STATE_INTERLEAVING |

### 4. Select Mutation Targets

Choose 5-8 mutations (one script per mutation). Prioritize:
1. HIGH criticality endpoints first
2. Endpoints with multiple attack surface types
3. Diverse mutation types (don't generate 5 scripts of one type when other types are applicable)
4. Transitions the recorded demo did NOT take (the happy path is already proven safe — attack the rest of the state machine)
5. High-impact rules observed in the UI but not proven by a negative server response

### 5. Generate Scripts

Each script MUST follow this template structure:

```python
"""
FlowBusters Mutation: {MUTATION_TYPE}
Target: {endpoint_url}
Attack: {brief description of what this tests}
"""
import asyncio
import json
import httpx

TARGET_URL = "{endpoint_url}"
MUTATION_TYPE = "{SKIP_STEP|ROLE_SWAP|DATA_TAMPER|REPLAY_ATTACK|FORCED_BROWSING|MASS_ASSIGNMENT|PRICING_TAMPER|DOUBLE_SPEND|STATE_INTERLEAVING}"

# Captured credentials from recording
COOKIES = {cookies_from_state_map}
HEADERS = {headers_from_state_map}


async def probe():
    async with httpx.AsyncClient(timeout=30.0, cookies=COOKIES, headers=HEADERS) as client:
        # {mutation-specific logic}
        response = await client.post(
            TARGET_URL,
            json={tampered_body},
        )

        result = {
            "url": TARGET_URL,
            "mutation_type": MUTATION_TYPE,
            "status_code": response.status_code,
            "response_body_snippet": response.text[:200],
            "expected_rejection": True,
        }
        print(json.dumps(result))


if __name__ == "__main__":
    asyncio.run(probe())
```

### 6. Mutation Type Patterns

**SKIP_STEP** — Skip prerequisite transitions:
```python
# Instead of: login → submit_application → review → approve
# Do: login → approve (skip submit and review)
# Use late-stage endpoint directly with valid auth but no prior state
```

**ROLE_SWAP** — Use wrong role's credentials:
```python
# Use Role A's cookies/headers to access Role B's endpoint
# Example: user cookies → admin approval endpoint
COOKIES = roles["user"]["cookies"]  # wrong role
TARGET_URL = "admin/approve"  # admin-only endpoint
```

**DATA_TAMPER** — Modify request body values:
```python
# Change IDs to access other users' resources
# Change amounts to negative/zero/extreme values
# Change status fields to skip workflow states
json_body = {"order_id": "OTHER_USERS_ORDER_ID", "amount": -1, "status": "approved"}
```

**REPLAY_ATTACK** — Replay a captured request:
```python
# Replay an approval request that was already processed
# The server should reject duplicate state transitions
# Use exact same request body and headers from the recording
```

**FORCED_BROWSING** — Access endpoints directly:
```python
# Guess or enumerate endpoint URLs
# Try: /api/orders/1, /api/orders/2, /api/orders/3
# Try: /admin/users, /internal/config, /api/export
# Access without going through the normal UI flow
```

**MASS_ASSIGNMENT** — Send fields the UI never sends:
```python
# Take a normal create/update body and add privileged fields:
#   {"product": "X", "quantity": 1, "customer": "Y",
#    "role": "admin", "is_admin": True, "status": "approved",
#    "balance": 999999, "verified": True, "owner_id": <other_user>}
# A correct server ignores (or rejects) these; one that persists them leaks privilege/state.
# Follow up with GET on the resource and verify the fields did NOT stick.
```

**PRICING_TAMPER** — Tamper money/quantity fields:
```python
# If any request body carries price, amount, total, discount, or quantity:
#   {"quantity": -5}            # negative quantity
#   {"price": 0}                # zero price
#   {"discount": 100}           # full discount
#   {"quantity": 0.5, "amount": -999}
# The server must recompute/validate server-side; trusting the client = direct financial loss.
# ALWAYS generate one of these when money/quantity fields exist in the flow.
```

**DOUBLE_SPEND** — Resend an idempotency-sensitive request:
```python
# For create/pay/transfer endpoints, fire the SAME request twice:
#   1) sequential: identical body twice with the same idempotency key (if the flow uses one)
#   2) concurrent: asyncio.gather([client.post(...) for _ in range(5)]) — race identical requests
# A correct server creates/charges exactly once; duplicates in the response or a follow-up
# GET listing two identical rows = bug.
```

### 7. Cookie Embedding

When scripts need Playwright for cookie-based auth:
```python
from playwright.async_api import async_playwright

async def probe():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()

        # Embed captured cookies directly
        await context.add_cookies([
            {"name": "session", "value": "abc123", "domain": ".example.com", "path": "/"},
            {"name": "csrftoken", "value": "xyz789", "domain": ".example.com", "path": "/"},
        ])

        page = await context.new_page()
        response = await page.goto(TARGET_URL)
        # ... rest of probe logic
```

### 8. Syntax Validation

After writing each script, validate:
```bash
python3 -c "import py_compile; py_compile.compile('mutations/{flow-name}/{script_name}.py', doraise=True)"
```

If validation fails, fix the syntax error and re-validate.

### 9. Naming Convention

Name scripts with zero-padded index and descriptive slug inside `mutations/{flow-name}/`:
- `mutations/{flow-name}/01_skip_step_approval.py`
- `mutations/{flow-name}/02_role_swap_user_to_admin.py`
- `mutations/{flow-name}/03_data_tamper_order_amount.py`
- `mutations/{flow-name}/04_replay_approval.py`
- `mutations/{flow-name}/05_forced_browse_admin_panel.py`

### 10. Verify Output

- Count `.py` files in `mutations/{flow-name}/` — must be 5-8
- All pass py_compile
- Each targets a different endpoint or attack vector
- Each prints exactly one JSON line to stdout when run

## Important Notes

- **ONLY these imports:** `playwright.async_api`, `httpx`, `json`, `asyncio` — nothing else
- **NEVER use:** `eval()`, `exec()`, `os.system()`, `subprocess`, `requests`, `urllib`
- **ALWAYS include 30s timeout** on all network operations
- **ALWAYS set `expected_rejection: true`** — we expect the server to reject these
- **One JSON line per script** — stdout must be parseable by Prober
- **Comments are mandatory** — each script must explain what vulnerability it's testing
- **No production URLs** — only test against QA/staging environments
- **Flow isolation:** Read from `flows/{flow-name}/` and write only to `mutations/{flow-name}/`.

### Complete financial scenarios

Include request-body amount overrides for recorded payout actions, and replay
the actual completion endpoint for multi-step payouts. Preserve prerequisites
and obtain a final GET after completion. A pending refund is not a completed
payout. Cross-flow probes compose complete source chains, including terminal
completion, without resetting between the combined actions. Reset only before
each independent scenario, then capture its before GET and its own after GET.
For an approved totalReturned/originalAmount requirement, test exactly those
response-root paths. Report an amount-bound check, not an unrelated claim about
mass-assignment prevention. Compute the boolean from that scenario's AFTER;
never reuse a prior scenario's variables or compare against a different amount.
