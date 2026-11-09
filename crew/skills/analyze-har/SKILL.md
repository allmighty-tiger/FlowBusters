# Skill: Analyze HAR

## Purpose

Parse captured HAR files and DOM interaction traces to extract state-changing endpoints, authentication credentials, role contexts, and produce a structured state map for adversarial mutation generation.

## Confidence: medium

## When to Use

- Phase 2 of FlowBusters pipeline
- After a flow recording is complete and `flows/{flow-name}/demo.json` + `flows/{flow-name}/recording.har` exist
- Need to identify attack surfaces in a business workflow
- The run may include an optional `--flow-name`; if omitted, use `default`

## Inputs

- Optional `flow-name` parameter
- `flows/{flow-name}/demo.json`
- `flows/{flow-name}/recording.har`

`flow-name` must be kebab-case: lowercase letters, numbers, and hyphens only.
If `flow-name` is omitted, default to `default`.

## Procedure

### 1. Resolve Flow Paths

Resolve the flow name before analysis:
- Use the provided `flow-name` when present
- Otherwise use `default`
- Validate it matches `^[a-z0-9]+(?:-[a-z0-9]+)*$`

Read both files from the flow-specific directory:
- `flows/{flow-name}/demo.json` — DOM interaction trace
- `flows/{flow-name}/recording.har` — Full network traffic capture

### 2. Filter Static Assets

Remove entries that match ANY of these patterns (by URL extension or content-type):
- `.css`, `.js`, `.map`, `.woff`, `.woff2`, `.ttf`, `.eot`
- `.png`, `.jpg`, `.jpeg`, `.gif`, `.svg`, `.ico`, `.webp`
- `text/css`, `application/javascript`, `image/*`, `font/*`
- CDN requests (e.g., `cdn.`, `static.`, `assets.`)

**Keep only:** Requests with content-type `application/json`, `text/html`, `application/x-www-form-urlencoded`, or API-like paths.

### 3. Read the UI Workflow Timeline

Read `demo.json.workflow_timeline.ui_states` before classifying requests. Each
state is a compact Playwright accessibility snapshot; `changes_from_previous`
is the observed UI delta. Extract security-relevant visible rules and states:

- disabled/enabled actions and terminal/locked/completed states
- prices, totals, discounts, quantities, maximum/minimum limits
- role, ownership, approval and authorization language
- one-time-use, ordering, prerequisite and replay constraints

Treat these as hypotheses about rules the backend must enforce. Do not infer an
exact click event: the timeline contains observed states, not fabricated clicks.
Correlate UI states to HAR entries by chronological order, URL and semantics.

### 4. Identify State-Changing Requests

Do not copy every HAR request into `transitions`. An initial GET /api/order
with only UI step 1 is baseline evidence, not a `1 -> 1` transition. Keep the
request in the source HAR and reference its captured state where relevant.
For an observed action use actual snapshots with `before_step < after_step`;
never invent a later step or label the baseline request as inferred. Do not
leave baseline-read names in action-transition `depends_on` arrays.

Focus on requests that modify server state:
- **Method filter:** POST, PUT, DELETE, PATCH (ignore GET, HEAD, OPTIONS)
- **Body analysis:** Must have a request body (JSON or form-encoded)
- **Response analysis:** Note status codes — 2xx means state was changed

For each state-changing request, extract:
```json
{
  "name": "descriptive_action_name",
  "method": "POST",
  "url": "https://api.example.com/orders/approve",
  "headers": {
    "Cookie": "session=abc123; csrftoken=xyz",
    "Authorization": "Bearer eyJ...",
    "X-CSRF-Token": "xyz"
  },
  "body_keys": ["order_id", "status", "approver_id"],
  "response_status": 200,
  "criticality": "HIGH",
  "depends_on": ["submit_application"]
}
```

This shape describes an observed request. An unexecuted inferred action may be
included only with `"inferred": true`, `"response_status": null`, and equal
`ui_context.before_step`/`after_step` values pointing to the one sampled UI
state that supports the inference. Never invent a response status or an
after-state for an inferred action.

### 5. Extract Authentication Credentials

Scan all request headers for auth patterns:
- **Cookies:** `session`, `sessionid`, `auth`, `token`, `jwt`, `.AspNetCore.*`
- **Headers:** `Authorization: Bearer ...`, `X-CSRF-Token`, `X-Auth-Token`
- **Form fields:** `_token`, `__RequestVerificationToken`, `csrf`

Group by distinct credential sets (each unique set = one role).

### 6. Identify Roles

Determine role contexts by observing:
- Different cookie values across requests (suggests role switch)
- Different authorization headers
- Requests to admin/management endpoints vs. user endpoints
- URL patterns like `/admin/`, `/api/v1/internal/`, `/manage/`

Each role gets:
```json
{
  "name": "admin|user|approver|reviewer",
  "cookies": [
    { "name": "session", "value": "full_value", "domain": ".example.com", "path": "/" }
  ],
  "headers": { "Authorization": "Bearer eyJ..." }
}
```

### 7. Classify Criticality

Rate each endpoint:
- **HIGH** — Financial transactions, approvals/rejections, role changes, authentication, data deletion
- **MED** — Data creation/modification, status updates, file uploads
- **LOW** — Preferences, non-sensitive updates, logging

### 8. Determine Attack Surface

For each critical endpoint, assess which mutation types apply:
- **SKIP_STEP** — Endpoint has `depends_on` prerequisites that could be bypassed
- **ROLE_SWAP** — Endpoint is accessed by one role; another role's creds might work
- **DATA_TAMPER** — Body contains IDs or values that could be manipulated
- **REPLAY_ATTACK** — Request could be replayed after state change
- **FORCED_BROWSING** — Endpoint URL is guessable/sequential

### 9. Write State Map

Output `flows/{flow-name}/state_map.json`:
```json
{
  "schema_version": 2,
  "observed_ui_rules_schema_version": 1,
  "target_url": "https://example.com/login",
  "flow_name": "{flow-name}",
  "recorded_at": "2024-01-15T10:30:00Z",
  "transitions": [...],
  "roles": [...],
    "semantic_ui_capture": {
      "status": "succeeded",
      "artifact": "demo.json",
      "ui_state_count": 4,
      "no_relevant_rules_reason": "No security-relevant rule was visible in the sampled semantic states"
    },
    "observed_ui_rules": [],
    "critical_endpoints": [
    {
      "url": "https://api.example.com/orders/approve",
      "method": "POST",
      "why": "Order approval - financial impact, requires specific role",
      "attack_surface": ["SKIP_STEP", "ROLE_SWAP", "DATA_TAMPER"]
    }
  ]
}
```

### 10. Verify Output

- Read and satisfy `crew/skills/analyze-har/OBSERVED_UI_RULES.md`. The backend
  rejects new state maps whose raw pointers, steps, elements, HAR indexes,
  values, provenance types, or schema versions do not validate.
- Validate JSON structure
- Confirm `flows/{flow-name}/state_map.json` exists
- Confirm at least 1 transition exists
- Confirm at least 1 role with credentials exists
- Confirm at least 1 critical endpoint identified
- Confirm `observed_ui_rules` follows version 1. It may be empty only when
  semantic UI capture succeeded, its state count matches `demo.json`, and a
  `no_relevant_rules_reason` is recorded.
- For EACH rule, require directly relevant raw UI evidence from `demo.json`
  (`explicit_ui_text` or `ui_element_transition`). API facts may supplement it
  but cannot be the only facts, and inference is not observed evidence. Omit
  API-only rules and their IDs rather than padding them with unrelated UI text.
  Keep useful API-based hypotheses in the relevant `critical_endpoints[].why`
  labelled `Agent inference (unverified):`; this is not verified provenance.
- Inspect every rule fact before writing the file. The only valid tuples are:
  `explicit_ui_text` + `explicit_visible_ui_text` + `demo.json`;
  `ui_element_transition` + `observed_ui_affordance` + `demo.json`;
  `api_field_transition` + `api_state_fact` + `recording.har`. Never mix tuple
  values. A button observed at one step is explicit UI text; it becomes a UI
  affordance transition only when exact before/after evidence supports one.
  Never invent an accessible name from colon text: `generic : Eligible` has
  no quoted name. Use the actual named button for an action transition, or
  record the full text as `explicit_ui_text` at one step; text presence alone
  does not prove disappearance or availability. Resolve pointers before emission.
  For API transitions, resolve both HAR pointers and the exact field_path
  first: identical values (for example "eligible" -> "eligible") are not a
  transition. Omit that fact and remove its ID from inference.derived_from.
  See OBSERVED_UI_RULES.md; the schema has no static API-field fact type.
  A JSON pointer does not replace the mandatory `artifact` field.

## Important Notes

- **NEVER include response bodies** in state_map.json — only metadata
- **NEVER send raw response bodies to the LLM** — strip before reasoning
- **Preserve exact cookie/token values** — Saboteur needs them for replay
- **Body keys only** — extract field names from request bodies, not values (except IDs needed for targeting)
- **Cross-reference with demo.json** — use UI state order and semantic deltas to
  establish dependencies, and preserve visible rules for mutation design
- **Flow isolation:** Read and write only inside `flows/{flow-name}/` for the active flow.
- **Keep provenance types distinct:** visible text, UI affordance transitions,
  API response fields, and agent inference are separate claims. Never promote
  API JSON or inferred server policy to an observed UI fact.
