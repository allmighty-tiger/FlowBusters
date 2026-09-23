# Backend execution and evidence contract

## New AI probes: declarative atomic plans

Write new drafts as `mutations/{flow}/NN_scenario.plan.json`, schema_version 2.
Do not create Python files. The backend validates JSON and supporting evidence
before creating `NN_scenario.py`; findings reference that eventual script name.
No custom HTTP, capture counters, supplementary scenarios or precomputed violation.

| claim_type enum | predicate enum | invariant |
| --- | --- | --- |
| amount_bound | business_rule_must_hold | sum_lte with exact numeric AFTER paths |
| unsupported | unsupported_business_rule | null; concrete unsupported_reason |

These are the only new-generation combinations (probe_contract.PLAN_SCHEMA).
For example, a repeated-payout upper-bound hypothesis uses the first pair; a
uniqueness, input-trust or workflow-order hypothesis uses the second unless a
supported predicate actually expresses it. Do not invent predicate names.
Correction receives the original hypothesis, rejected pair, allowed pairs and
exact observed fields/paths. An unexpressible predicate stays reviewable, never
silently changes into a monetary claim.

The following Python fragment describes backend-compiled fields, NOT a complete
plan to copy. New files must be JSON and must include all inference metadata in
the Declarative inference gate section below.

`claim_type` is a schema category, not the attack name. Use exactly `amount_bound`
only when the claim is that the measured sum exceeds the recorded comparison
value. Put replay/interleaving descriptions in the title, not a made-up supported
claim_type. Request uniqueness, field acceptance and workflow authorization are
different claims: a sum limit cannot establish them. Use unsupported_business_rule
for those claims. Supporting observation values must be the exact pointed raw
values, never summaries such as "recorded completion returned 100".

```python
PROBE_PLAN = {
    'schema_version': 2, 'title': 'Completed operations exceed the inferred amount bound',
    'origin': 'http://localhost:3000', 'setup': '/api/demo/reset',
    'state_read': '/api/records', 'claim_type': 'amount_bound',
    'actions': [
        {'method': 'POST', 'path': '/api/records/update', 'body': {}},
        {'method': 'POST', 'path': '/api/records/request', 'body': {}},
        {'method': 'POST', 'path': '/api/records/complete', 'body': {}}],
    'predicate': 'business_rule_must_hold',
    'invariant': {'operator': 'sum_lte', 'terms': [['order', 'measuredValue']],
                  'limit': ['order', 'comparisonValue']}
}
```

Select actual authorized endpoints from `endpoint_catalog.json`; the example is
not permission to probe another target. The backend supplies setup, before GET,
ordered actions, final GET, transport sequence IDs and the boolean computed from
that final response. Repeated requests must appear explicitly in `actions`.
One file is one atomic chain. Do not describe sequential requests as concurrent.
An unsupported claim uses `predicate='unsupported_business_rule'`,
`claim_type='unsupported'`, `invariant=null` and a specific `unsupported_reason`; never use a
money bound to claim mass-assignment or workflow authorization was disproved.

Cross-flow candidates must include an `execution_contract` equal to the plan's
`origin`, `setup`, `state_read`, ordered `actions`, `predicate`, `invariant`,
`unsupported_reason` (null when absent), and `claim_type`. Binding is exact and
unique, not an endpoint-subset or filename match. Each candidate still needs
authenticated state-changing facts from at least two different source runs.

The remaining output contract describes compiled output and legacy scripts.

The backend executes Python scripts AFTER the agent exits. Do not run scripts
or HTTP verification manually. Author drafts, then stop. Use HTTPX, requests, or urllib
in-process: other transports and subprocess HTTP calls cannot support CONFIRMED.

Each script must establish its own fresh state and credentials using permitted
setup. Never assume a previous script left usable state. Do not edit scripts
after execution. A failed prerequisite must be recorded as NOT_EXECUTED.

Print one JSON object with title, mutation_type, url, status_code, outcome,
and verification. Build verification directly from actual response objects:
never write literal invented response bodies. Every capture includes:

    {"sequence": 1, "status_code": 200, "complete": true,
     "request": {"method": "GET", "url": "FULL_URL", "body": null},
     "response": {"actual": "parsed JSON response body"}}

A bodyless POST (fixture reset, empty mutation call) is the SAME null:
    {"sequence": 1, "status_code": 200, "complete": true,
     "request": {"method": "POST", "url": "FULL_URL", "body": null},
     "response": {"actual": "parsed JSON response body"}}

Do not maintain an independent sequence counter. Under backend execution, the
capture harness writes the authoritative transport sequence onto each HTTPX
response as `response.extensions["flowbusters_sequence"]`. Copy that integer
into the capture. Missing metadata is a contract failure; never guess or
renumber it. Every request consumes a sequence, including fixture resets,
credential setup, repeated resets, failed requests, and concurrent requests.
Represent setup calls in `verification.setup` as capture objects. If a probe
contains supplementary scenarios, represent their setup/before/actions/after
captures under that scenario. The union of setup and scenario captures must
account for every event in the signed transport trace exactly once. Setup
captures remain fixture evidence and are never attack actions.

Supplementary evidence has one canonical location and shape:

    "verification": {
      ...primary scenario fields...,
      "supplementary_scenarios": {
        "stable_scenario_id": {
          "setup": [...],
          "before": {...},
          "actions": [...],
          "after": {...}
        }
      }
    }

It MUST be a mapping of unique names inside `verification`. Never emit
`supplementary_scenarios` beside `verification` at the result root, and never
use a list there. Root-level supplementary objects are not evidence and fail
preflight/output validation. Every HTTP response created by every scenario,
including resets, reads, rejected actions, repeats, and concurrent requests,
must appear exactly once in the canonical capture union.

Request body must be the exact parsed JSON of the bytes you actually sent,
literal text, or null. Decide `body` from whether the request carried a body,
NOT from the HTTP method: a GET or a bodyless POST (e.g. a fixture reset
sent with no `json`/`data`) both declare `body: null`, while a POST that sent
`json={...}` declares that object. Never declare `{}` for a request you sent
with no body — the backend's signed transport trace records such requests as
`null`, and a capture that claims an empty object it never sent is a contract
failure, not a notation detail.
Response must be the FULL parsed JSON body, or literal text. Include status_code
on EVERY action. Include all state-changing requests between before and after.
Sequence numbers identify the corresponding signed event; they are not local
step numbers. They count ALL HTTP calls made by the script, including setup.
Use one independently bracketed scenario per script. `before` and `after` are
their OWN standalone GET state-read captures — the read just before and the read
just after the actions. They are the scenario brackets, NOT members of `actions`
and NOT aliases of an action capture: `actions` holds only the intervening
action calls, and the final after-read must not also be listed as the last
action. Do not set `before`/`after` to `actions[-1]`/`setup[-1]` when that
would reuse a sequence the brackets already own. Before and after may have
identical bodies: their distinct request sequence numbers disambiguate them.
The executor builds the primary chain as `[before, *actions, after]`, so if a
sequence appears in two places (e.g. the after-read duplicated in `actions`),
the scenario fails as "sequences must be unique and increasing".

Keep the existing verification predicate/rule contract. Generic business-rule
checks additionally supply a machine-evaluable invariant:

    "invariant": {"operator": "sum_lte",
                  "terms": [["priceAdjustment"], ["completedRefund"]],
                  "limit": ["comparisonValue"]}

Paths are arrays of exact keys into the captured AFTER response. Above is only
a schema example; use fields actually present. Paths MUST start at the HTTP
response root, including envelope keys such as `order`; never write paths
relative to a nested resource and never guess array indexes. A single term such as
[["measuredValue"]] is also supported. The script MUST resolve those exact paths
from its captured AFTER response and emit `violation.observed` as the JSON boolean
result of `sum(terms) > limit` (`true` or `false`, never `null`, a number, or a
string). The backend independently recomputes the same comparison from the signed
trace and rejects disagreement; backend recomputation does not fill in a missing
boolean. Cite the UI/user/specification business
rule independently; a numerical comparison alone does not establish a rule.
For multiple scenarios separated by reset, compute every declared invariant
from that scenario's own AFTER capture, never from another scenario or an
accumulated total across resets. Use the exact declared limit path: for
`terms=[["order", "measuredValue"]]` and `limit=["order", "comparisonValue"]`,
the calculation is `after_order["measuredValue"] > after_order["comparisonValue"]`.
Never substitute the record-update amount (30) for comparisonValue (100).
100 > 100 is false even if cancellation returned more than a price adjustment.
Derive the description from the same operands. Missing numeric values are an
error, never a default false. Supplementary captures without their own invariant
and violation remain partial coverage; their race label creates no verdict.
The backend also rejects contradictory supplementary invariant claims and names
the scenario, AFTER sequence, operand paths/values, limit, and recomputed result.
Other generic invariant types remain NEEDS_REVIEW until implemented. When the
intended rule cannot be expressed by `sum_lte`, do not emit
`business_rule_must_hold` and do not invent paths or arithmetic. Use
`predicate: "unsupported_business_rule"` with a concrete
`unsupported_reason`, retain the complete captured transport evidence, and let
the backend keep the result in NEEDS_REVIEW.

The backend statically rejects a literal non-boolean `violation.observed` before
starting probes when that defect is visible in generated source. It validates the
versioned output again immediately after each terminal receipt. Missing invariants,
dynamic or otherwise malformed non-boolean violation values, malformed or guessed sequences, unaccounted setup
requests, and captures that do not identify their signed event are explicit
evidence-contract errors. The receipt is retained and independent probes continue
from their own authorized setup. A zero process exit is not a successful
check when this validation fails.

For `rule.source: "observed_ui"`, use the structured rule reference from
`crew/skills/analyze-har/OBSERVED_UI_RULES.md`: schema version, exact source run,
`state_map.json`, rule ID, and fact IDs. At least one selected fact must resolve
to raw semantic UI evidence in `demo.json`. Free-form state-map references,
API-state facts alone, and agent inference are unverified and cannot confirm a
positive violation. User and specification rules retain their explicit textual
reference forms. Missing rule provenance never overrides a complete negative
execution: when the backend-evaluated invariant is not violated, the result is
NOT_REPRODUCED.

Preserve before/actions/after, rule, predicate and violation.description.
Backend also retains the pre-existing predicate verifier: provenance alone
cannot confirm an unsupported or invalid business rule.

Place follow-up verification scripts in verification_probes/{flow-name}/.
Their draft finding has source VERIFICATION_PROBE, its own script filename,
and triggered_by set to the originating mutation script. Each follow-up must
capture its own COMPLETE chain, including setup and state reads. Never combine
the parent execution with follow-up execution into one claimed script trace.

Backend receipts record a unique execution ID, source snapshot and SHA-256,
command, timestamps, exit code, exact stdout/stderr bytes (base64 plus text),
parsed output and transport events. Each rerun creates a new receipt. Existing
receipts are not overwritten; signatures detect changed content on report load.

Read endpoint_catalog.json before authoring requests. The backend authenticates
its method/origin/path inventory, catches statically resolvable unlisted targets
before execution, and enforces it again at the transport boundary. Recorded
URLs and backend application contracts establish endpoints; UI names do not.
A signed 404/405 on a required action remains incomplete coverage, never a held
control. Do not silently replace an endpoint in an executed script.

Backend scripts live in backend_coverage/{flow-name}/. Agents write only their
own mutations/verification_probes. New receipts sign the component source as
BACKEND_COVERAGE, MUTATION_SCRIPT or VERIFICATION_PROBE; a filename or printed
origin field cannot establish generation authority.

No product registry or application-specific coverage plan supplies new probes.
AI-selected recorded operations pass the state-map and plan gates and use this
same transport and invariant contract. Each is one atomic reset -> before GET
-> full action chain -> final GET scenario. Contract errors are per probe;
independent admitted plans continue and retain their own terminal receipts.
Payout replay must repeat completion, not just refund request. A combined
adjustment/refund check must reach refund complete before reading its final state.

Declare a numeric comparison only when its fields exist in the recording.
Calculate it from the final response. Treat it as an AI-proposed condition;
its business meaning requires user review. Use unsupported_business_rule when
the available check cannot express the test.

Semantic coverage: specify claim, required actions, supported predicate and final
workflow state before constructing verification. Pending payout is partial
coverage, not a held control. Request-field acceptance cannot be tested by
measuredValue alone. Use unsupported_business_rule with the exact missing
request-to-expected-state predicate rather than a fabricated negative result.

Example: price adjustment -> refund request -> refund complete -> final GET
tests completed combined payouts. Price adjustment -> refund request -> GET
with refund.status=pending does not. For requestedAmount tampering, a final
measuredValue=0 cannot reject the hypothesis: retain it as NEEDS_REVIEW unless
an independently supported predicate tests the requested amount itself.
# Setup admission

Numeric-plan provenance failures are admission errors, not unsupported predicates.
The generation gate may relocate an unchanged observation value to one unique
exact pointer in the same authenticated raw artifact. Abbreviated HAR bodies and
state-map analysis require regeneration; they cannot be accepted as raw evidence.
After one unsuccessful regeneration the JSON plan is rejected without creating
Python or making HTTP requests. Independent admitted plans may still execute.
Preflight rejections are separate from process errors and have no execution receipt.

A PROBE_PLAN setup is an explicitly configured POST reset, not a state-read GET.
Use only a `configured_setup` entry from the authenticated endpoint catalog.
All planned methods and paths are checked before the first probe. Multiple
independent plans require authorized state restoration. Missing setup blocks
execution; removing it does not make tests independent. Setup correction is
bounded to one proposal before execution. The agent receives exact validator
errors and the unchanged recording. Actions, claim and numeric operand names
are preserved; only a recorded nesting correction may repair an operand path.
The corrected plan passes the entire gate again. A still-invalid plan is a
backend-authenticated preflight CHECK_ERROR, not an HTTP execution receipt.
Other independent plans continue. Existing receipts and executed scripts are never rewritten.
# Declarative inference and isolation gate

Every supported `business_rule_must_hold` PROBE_PLAN requires `rule_origin:
AI_INFERRED`, `inferred_invariant` (statement, exact invariant object in `predicate`,
ordered `required_actions`, response-root `terminal_state` conditions), and
`supporting_observations` (source_run, demo.json/recording.har, json_pointer, exact
raw value). Comments are not metadata. The backend validates the complete queue,
resolves observation pointers against validated recordings, and rejects missing
metadata, unobserved actions or incomplete chains before the first probe.
HAR `content.text` is a string: reference its complete raw value, not invented
JSON children. Unsupported predicates remain reviewable coverage, never confirmed.
Parse that string to inspect the response schema before choosing invariant operands.
Each numeric operand must exist at its exact response-root path in recorded HAR.
For example, a field inside `record.refund` is not a field directly inside `record`.
Preflight rejects wrong nesting without guessing a replacement; runtime independently
requires numeric operands in the actual final GET and recomputes the boolean.

Each plan must select an authorized setup endpoint from endpoint_catalog.json's
`configured_setup` entries, followed by before GET, ordered actions and final GET.
A constant limit does not establish an independent baseline. Reanalysis can receive
an operator-authorized fixture path through `--setup-path`; this is test isolation,
not a business rule. The compiler computes the boolean from this plan's final GET.
