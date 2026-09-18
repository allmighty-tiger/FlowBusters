# Backend execution and evidence contract

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

Request body must be parsed JSON, literal text, or null for an empty body.
Response must be the FULL parsed JSON body, or literal text. Include status_code
on EVERY action. Include all state-changing requests between before and after.
Sequence numbers identify the corresponding signed event; they are not local
step numbers. They count ALL HTTP calls made by the script, including setup.
Use one independently bracketed scenario per script. Before and after may have
identical bodies: their distinct request sequence numbers disambiguate them.

Keep the existing verification predicate/rule contract. Generic business-rule
checks additionally supply a machine-evaluable invariant:

    "invariant": {"operator": "sum_lte",
                  "terms": [["priceAdjustment"], ["completedRefund"]],
                  "limit": ["originalAmount"]}

Paths are arrays of exact keys into the captured AFTER response. Above is only
a schema example; use fields actually present. Paths MUST start at the HTTP
response root, including envelope keys such as `order`; never write paths
relative to a nested resource and never guess array indexes. A single term such as
[["totalReturned"]] is also supported. The script MUST resolve those exact paths
from its captured AFTER response and emit `violation.observed` as the JSON boolean
result of `sum(terms) > limit` (`true` or `false`, never `null`, a number, or a
string). The backend independently recomputes the same comparison from the signed
trace and rejects disagreement; backend recomputation does not fill in a missing
boolean. Cite the UI/user/specification business
rule independently; a numerical comparison alone does not establish a rule.
For multiple scenarios separated by reset, compute every declared invariant
from that scenario's own AFTER capture, never from another scenario or an
accumulated total across resets. Use the exact declared limit path: for
`terms=[["order", "totalReturned"]]` and `limit=["order", "originalAmount"]`,
the calculation is `after_order["totalReturned"] > after_order["originalAmount"]`.
Never substitute the price-adjustment amount (30) for originalAmount (100).
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
evidence-contract errors. The receipt is retained, but the backend stops before
running the remaining generated probes. A zero process exit is not a successful
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

Backend-owned coverage inputs can supplement generated probes for an explicitly
registered application/version. They are selected only from validated recorded
operations, run after the state-map gate and use this same signed transport and
invariant contract. They contain no expected verdict. Each is one atomic
reset -> before GET -> full action chain -> final GET scenario. Agent scripts
remain independent; contract errors still stop the remaining execution queue.
Payout replay must repeat completion, not just refund request. A combined
adjustment/refund check must reach refund complete before reading its final state.

Declare verification explicitly. For the approved Northstar monetary cap use
`{"operator":"sum_lte","terms":[["order","totalReturned"]],"limit":["order","originalAmount"]}`.
Compute observed as `after_body["order"]["totalReturned"] > after_body["order"]["originalAmount"]`
from that scenario's final GET, after checking finite numeric values. Missing or
invalid numeric fields are contract errors, never False. Prefer explicit invariant
and violation keys in the primary verification dictionary. Do not OR primary and
supplementary results. Helper dictionary unpacking is supported only when its
shape can be statically resolved; no script code is executed during preflight.
If the hypothesis is not a monetary bound, do not insert this invariant: use
unsupported_business_rule and a concrete unsupported_reason instead.

Semantic coverage: specify claim, required actions, supported predicate and final
workflow state before constructing verification. Pending payout is partial
coverage, not a held control. Request-field acceptance cannot be tested by
totalReturned alone. Use unsupported_business_rule with the exact missing
request-to-expected-state predicate rather than a fabricated negative result.

Example: price adjustment -> refund request -> refund complete -> final GET
tests completed combined payouts. Price adjustment -> refund request -> GET
with refund.status=pending does not. For requestedAmount tampering, a final
totalReturned=0 cannot reject the hypothesis: retain it as NEEDS_REVIEW unless
an independently supported predicate tests the requested amount itself.
