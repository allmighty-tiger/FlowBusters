# State verification, version 1

## Checks that cannot execute

Retain checks whose prerequisites are missing in findings and results. Use the
same finding_id for linkage. Supply this structured field on the finding/result
or inside verification:

```json
{"execution": {"status": "NOT_EXECUTED", "missing_precondition": "A resource owned by a different user is unavailable for this scenario.", "next_step": "Prepare the required users and resource ownership, then attempt access using the other user's session."}, "related_findings": ["F-005"]}
```

State the actual missing prerequisite and a concrete manual test or rerun step.
Do not assume all authorization tests require two objects: a single object and
two appropriately different principals can suffice for some scenarios.
NOT_EXECUTED means the target test action did not run; setup reads may have run.
Keep it visible as a coverage gap, never call it safe or count it as confirmed.
Timeouts, failed reads and malformed responses are CHECK_ERROR. An executed
check with insufficient evidence remains NEEDS_REVIEW. NOT_REPRODUCED means
this attempt did not demonstrate the violation, not that the app is secure.
Never infer status from a title or turn an execution error into a missing fixture.
Related evidence is a reference only; it cannot promote another check's verdict.

HTTP codes describe responses, not business-rule enforcement. Capture an
independent post-action read even when the action returns 500. Never infer
removal from 404. Unsupported checks remain NEEDS_REVIEW; do not invent evidence.

First supported predicate: approved_item_must_remain.
Record `verification` on the finding or on exactly one result with a matching
`finding_id`. Stable finding IDs also identify remediation sections.

Verification object:
- predicate: approved_item_must_remain
- rule: {source: user | specification, reference: actual supplied rule or spec location}
- before and after: {resource_id, principal_id, sequence: integer,
  status_code: 200, complete: true, state: APPROVED, item_ids: [IDs],
  request: captured request, response: captured response}
- action: {resource_id, principal_id, sequence: integer, method: DELETE,
  item_id: ID, request: captured request, response: captured response}
- error: optional description of a failed state check

Write a probe that reads the same complete collection before and after the
DELETE, using the same authenticated principal. Parse item IDs and approval
state from the captured response, not from agent assumptions. Use increasing
sequence numbers. Mark complete only when pagination is exhausted. Use an
isolated test resource without concurrent writers; otherwise the result is
inconclusive. If the application exposes no reliable state reader, emit
NEEDS_REVIEW. Do not create generic guessed URLs.

The backend compares the structured snapshots deterministically. It trusts
probe-provided captures and their normalized fields; this is not independent
re-execution or cryptographic evidence verification. App-specific extraction
must be tested against the target fixture. Additional predicates require code
and tests before they can return CONFIRMED.

An inferred rule needs user/specification confirmation before CONFIRMED.
Use explicit finding.remediation or headings with exact finding IDs.

For this predicate, execute `python3 crew/scripts/verified_delete.py CONFIG.json
reports/<flow>/evidence/<finding_id>.json`. Do NOT substitute a prose summary or
hand-written verification snapshots. The report API loads these artifacts
directly. Reuse that exact finding ID in findings.json, results and remediation.
Run this INSTEAD OF a separate destructive delete probe (not after it).

CONFIG contains finding_id, title, severity, read_url, delete_url, headers,
state_path (e.g. ["dashboard", "state"]), items_path (e.g. ["parts"]), id_key,
item_id, rule {source, reference}, complete_collection, isolated_resource.
Derive URLs and extraction paths from the actual recorded app, never guess.
Only set complete_collection and isolated_resource to true if established:
this executor supports a single complete response, not paginated collections.
Use only an authorized test resource and the configured authenticated session.
If preconditions cannot be established, do not run deletion; report the gap.
Rules inferred by the agent must retain source "agent", not "specification".
Keep credential-bearing CONFIG local with restrictive permissions; never add
it to git or attach it to reports. Response bodies can also contain sensitive
data: use the authorized test fixture and review artifacts before sharing.
