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

### 3. Identify State-Changing Requests

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

### 4. Extract Authentication Credentials

Scan all request headers for auth patterns:
- **Cookies:** `session`, `sessionid`, `auth`, `token`, `jwt`, `.AspNetCore.*`
- **Headers:** `Authorization: Bearer ...`, `X-CSRF-Token`, `X-Auth-Token`
- **Form fields:** `_token`, `__RequestVerificationToken`, `csrf`

Group by distinct credential sets (each unique set = one role).

### 5. Identify Roles

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

### 6. Classify Criticality

Rate each endpoint:
- **HIGH** — Financial transactions, approvals/rejections, role changes, authentication, data deletion
- **MED** — Data creation/modification, status updates, file uploads
- **LOW** — Preferences, non-sensitive updates, logging

### 7. Determine Attack Surface

For each critical endpoint, assess which mutation types apply:
- **SKIP_STEP** — Endpoint has `depends_on` prerequisites that could be bypassed
- **ROLE_SWAP** — Endpoint is accessed by one role; another role's creds might work
- **DATA_TAMPER** — Body contains IDs or values that could be manipulated
- **REPLAY_ATTACK** — Request could be replayed after state change
- **FORCED_BROWSING** — Endpoint URL is guessable/sequential

### 8. Write State Map

Output `flows/{flow-name}/state_map.json`:
```json
{
  "target_url": "https://example.com/login",
  "flow_name": "{flow-name}",
  "recorded_at": "2024-01-15T10:30:00Z",
  "transitions": [...],
  "roles": [...],
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

### 9. Verify Output

- Validate JSON structure
- Confirm `flows/{flow-name}/state_map.json` exists
- Confirm at least 1 transition exists
- Confirm at least 1 role with credentials exists
- Confirm at least 1 critical endpoint identified

## Important Notes

- **NEVER include response bodies** in state_map.json — only metadata
- **NEVER send raw response bodies to the LLM** — strip before reasoning
- **Preserve exact cookie/token values** — Saboteur needs them for replay
- **Body keys only** — extract field names from request bodies, not values (except IDs needed for targeting)
- **Cross-reference with demo.json** — use interaction timestamps to establish ordering/dependencies between transitions
- **Flow isolation:** Read and write only inside `flows/{flow-name}/` for the active flow.
