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
[["totalReturned"]] is also supported. The backend computes the sum and limit,
then checks violation.observed agrees. Cite the UI/user/specification business
rule independently; a numerical comparison alone does not establish a rule.
Other generic invariant types remain NEEDS_REVIEW until implemented. When the
intended rule cannot be expressed by `sum_lte`, do not emit
`business_rule_must_hold` and do not invent paths or arithmetic. Use
`predicate: "unsupported_business_rule"` with a concrete
`unsupported_reason`, retain the complete captured transport evidence, and let
the backend keep the result in NEEDS_REVIEW.

The backend validates the versioned output immediately after each terminal
receipt. Missing invariants, malformed or guessed sequences, unaccounted setup
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
