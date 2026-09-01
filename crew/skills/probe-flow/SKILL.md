# Skill: Probe Flow

## Purpose

Execute adversarial mutation scripts, parse their JSON output, classify outcomes as BUG_FOUND / REJECTED / ERROR, and produce findings reports with remediation guidance.

## Confidence: medium

## When to Use

- Phase 4 of FlowBusters pipeline
- After mutation scripts exist in `mutations/{flow-name}/` and are syntax-valid
- Need to execute probes and determine if business logic flaws exist
- The run may include an optional `--flow-name`; if omitted, use `default`

## Inputs

- Optional `flow-name` parameter
- Mutation scripts in `mutations/{flow-name}/`

`flow-name` must be kebab-case: lowercase letters, numbers, and hyphens only.
If `flow-name` is omitted, default to `default`.

## Procedure

### 1. Resolve Flow Paths

Resolve the flow name before probing:
- Use the provided `flow-name` when present
- Otherwise use `default`
- Validate it matches `^[a-z0-9]+(?:-[a-z0-9]+)*$`

Use these directories for the run:
- Read scripts from `mutations/{flow-name}/`
- Write reports to `reports/{flow-name}/`

### 2. Enumerate Scripts

List all `.py` files in `mutations/{flow-name}/`:
```bash
ls mutations/{flow-name}/*.py
```

Sort by filename (numeric prefix ensures execution order).

### 3. Execute Each Script

For each script, run with a 30-second timeout:
```bash
timeout 30 python3 mutations/{flow-name}/{script_name}.py 2>&1
```

Capture both stdout and stderr separately when possible.

### 4. Parse Output

Each script should print exactly one JSON line to stdout:
```json
{"url": "...", "mutation_type": "...", "status_code": 200, "response_body_snippet": "...", "expected_rejection": true}
```

**Parse rules:**
- Find the last line of stdout that is valid JSON
- If no valid JSON found, mark as ERROR
- Extract all fields from the JSON

### 5. Classify Outcomes

| Condition | Classification | Meaning |
|-----------|---------------|---------|
| `expected_rejection == true` AND `status_code` is 2xx (200-299) | **BUG_FOUND** 🐛 | Server accepted a request it should have rejected |
| `expected_rejection == true` AND `status_code` is 4xx/5xx | **REJECTED** ✅ | Server properly blocked the attack |
| `expected_rejection == false` AND `status_code` is 2xx | **REJECTED** ✅ | Expected behavior confirmed |
| Script times out (>30s) | **ERROR** ⚠️ | Timeout — possible network issue or infinite loop |
| Script throws exception | **ERROR** ⚠️ | Execution failure |
| No JSON in stdout | **ERROR** ⚠️ | Malformed script output |

### 6. Handle Common Errors

**ImportError / ModuleNotFoundError:**
```
ERROR: Missing module 'httpx'. Fix: pip install httpx
ERROR: Missing module 'playwright'. Fix: pip install playwright && playwright install
```

Report the exact module and install command.

**ConnectionError / TimeoutError:**
```
ERROR: Connection refused to {url}. Is the target application running?
ERROR: Request timed out after 30s. Target may be unreachable.
```

**JSON Parse Error:**
```
ERROR: Script output is not valid JSON. Raw output: {first 200 chars}
```

### 7. Auth Sanity Check (ALWAYS when the flow has a login endpoint)

Check `flows/{flow-name}/state_map.json` for an auth/login endpoint. If one exists:

1. POST the login endpoint with a known username + a WRONG password
2. POST the login endpoint with a brand-new unknown username + any password
3. If EITHER returns 2xx with a usable token/cookie, authentication is not enforced:
   - Record it as a finding with `source: "AUTH_CHECK"`, severity `Critical`, CWE-287, exact test credentials and responses as evidence
   - Make it the FIRST finding in `remediation.md` — full-system access dwarfs per-endpoint bugs

### 8. Compile Findings

Write `reports/{flow-name}/findings.json`:
```json
{
  "run_timestamp": "2024-01-15T11:00:00Z",
  "target_url": "https://example.com/login",
  "flow_name": "{flow-name}",
  "total_scripts": 5,
  "findings": [
    {
      "id": "F-001",
      "title": "Authentication bypass — /api/login accepts ANY username/password",
      "source": "AUTH_CHECK",
      "severity": "Critical",
      "cwe": ["CWE-287"],
      "script": null,
      "mutation_type": null,
      "url_tested": "https://api.example.com/login",
      "evidence": {
        "summary": "Login performs no credential validation: both a known user with a wrong password AND a brand-new unknown user get HTTP 200 + a valid bearer token",
        "requests": [
          {"label": "known_user_wrong_password", "method": "POST", "url": "https://api.example.com/login", "body": {"username": "knownuser", "password": "WRONGPWD"}},
          {"label": "unknown_user_any_password", "method": "POST", "url": "https://api.example.com/login", "body": {"username": "newuser123", "password": "anything"}}
        ],
        "responses": [
          {"label": "known_user_wrong_password", "status_code": 200, "response_body": "{\"ok\": true, \"token\": \"...\"}"},
          {"label": "unknown_user_any_password", "status_code": 200, "response_body": "{\"ok\": true, \"token\": \"...\"}"}
        ]
      }
    },
    {
      "id": "F-002",
      "title": "Order approval skips creation step",
      "source": "MUTATION_SCRIPT",
      "severity": "High",
      "cwe": ["CWE-841"],
      "script": "01_skip_step_approval.py",
      "mutation_type": "SKIP_STEP",
      "url_tested": "https://api.example.com/orders/approve",
      "evidence": "POST /orders/approve without create -> 200 {status: approved}"
    }
  ],
  "results": [
    {
      "script": "01_skip_step_approval.py",
      "mutation_type": "SKIP_STEP",
      "outcome": "BUG_FOUND",
      "status_code": 200,
      "url_tested": "https://api.example.com/orders/approve",
      "response_snippet": "{\"status\": \"approved\", \"order_id\": \"12345\"}",
      "error_message": null
    },
    {
      "script": "02_role_swap_user_to_admin.py",
      "mutation_type": "ROLE_SWAP",
      "outcome": "REJECTED",
      "status_code": 403,
      "url_tested": "https://api.example.com/admin/users",
      "response_snippet": "{\"error\": \"Forbidden\"}",
      "error_message": null
    },
    {
      "script": "03_data_tamper_order_amount.py",
      "mutation_type": "DATA_TAMPER",
      "outcome": "ERROR",
      "status_code": null,
      "url_tested": "https://api.example.com/orders/submit",
      "response_snippet": null,
      "error_message": "ModuleNotFoundError: No module named 'httpx'. Fix: pip install httpx"
    }
  ],
  "summary": {
    "bugs_found": 2,
    "critical_findings": 1,
    "rejected": 1,
    "errors": 1
  }
}
```

Schema rules:
- **`findings[]` is the vulnerability list** — one entry per CONFIRMED vulnerability, regardless of where it came from. `source` is `MUTATION_SCRIPT`, `AUTH_CHECK`, or `ANALYSIS`. Sort by severity (Critical first). This is what drives the report — `summary.bugs_found` counts `findings[]`, NOT `results[]`.
- Every finding needs `title`, `severity` (Critical/High/Medium/Low), `cwe`, `url_tested`, and `evidence`. `script`/`mutation_type` are set only for `MUTATION_SCRIPT` findings, else `null`.
- **`evidence`** — two accepted forms (the report UI renders both):
  - **Object (preferred when a finding is proven by ≥2 probes)**: `{"summary": "what was sent → what came back → why it proves the flaw", "requests": [{"label","method","url","body"}], "responses": [{"label","status_code","response_body"}]}`. Keep `requests`/`responses` **parallel by index** (request[i] pairs with response[i]).
  - **String** (fine for a single-probe finding): one compact line like `POST /api/login knownuser/WRONGPWD -> 200 {ok:true, token:...}`.
- **`results[]` is the raw per-script execution log** — every script, all outcomes (BUG_FOUND/REJECTED/ERROR), unchanged from before. `summary.rejected` and `summary.errors` count from `results[]`.
- Any BUG_FOUND script must also appear in `findings[]` (a script that found a bug is a confirmed vulnerability).
- Severity assignment: auth bypass / full-system access = Critical; unauthorized state-changing action = High; data-integrity issues (e.g. accepting negative quantity) = Medium; information leaks = Low.

### 9. Generate Remediation (If Any Findings)

If `findings[]` is non-empty, generate `reports/{flow-name}/remediation.md` — one `## Finding N:` section per entry in `findings[]`, **Critical first**:

```markdown
# FlowBusters Remediation Report

**Target:** {target_url}
**Flow:** {flow-name}
**Run Date:** {timestamp}
**Findings:** {count} ({critical} critical)

## Summary

{N} vulnerabilities were discovered in {target_url}.
{If a Critical finding exists, lead with it: e.g. 'Most severe: authentication does not verify credentials — anyone can obtain a session token.'}

## Findings

### 1. {Finding title}

- **CWE:** CWE-{id}: {name}
- **Severity:** {Critical|High|Medium|Low}
- **Source:** {AUTH_CHECK|MUTATION_SCRIPT|ANALYSIS}{ (script name if MUTATION_SCRIPT)}
- **Endpoint:** {method} {url}
- **Issue:** {Clear description of what went wrong}
- **Evidence:** {exact request(s) + response(s)}
- **Fix:**
  - {Specific remediation step 1}
  - {Specific remediation step 2}
  - {Specific remediation step 3}

---
```

**CWE Mapping Guide:**
| Source / Mutation Type | Likely CWE | Name |
|------------------------|-----------|------|
| AUTH_CHECK (creds not verified) | CWE-287 | Improper Authentication |
| SKIP_STEP | CWE-841 | Improper Enforcement of Behavioral Workflow |
| ROLE_SWAP | CWE-284 | Improper Access Control |
| DATA_TAMPER | CWE-20 | Improper Input Validation |
| REPLAY_ATTACK | CWE-294 | Authentication Bypass by Capture-replay |
| FORCED_BROWSING | CWE-425 | Direct Request (Forced Browsing) |

**Severity Guide:**
| Impact | Severity |
|--------|----------|
| Financial loss, privilege escalation, auth bypass | Critical |
| Data modification, workflow bypass | High |
| Information disclosure, minor state corruption | Medium |
| Non-sensitive data access, cosmetic issues | Low |

### 10. Print Summary Table

Display to user in chat — the FINDINGS table (vulnerabilities, Critical first) AND the per-script execution log:
```
FINDINGS ({flow-name}):
┌────────┬──────────────┬──────────────┬───────────┐
│ ID     │ Severity     │ Source       │ Title     │
├────────┼──────────────┼──────────────┼───────────┤
│ F-001  │ 🚨 Critical  │ AUTH_CHECK   │ Auth bypa…│
│ F-002  │ ⚠️ High      │ MUTATION     │ Skip-ste… │
└────────┴──────────────┴──────────────┴───────────┘

SCRIPT EXECUTION:
┌─────────────────────────────────┬───────────────┬──────────────┐
│ Script                          │ Mutation      │ Outcome      │
├─────────────────────────────────┼───────────────┼──────────────┤
│ 01_skip_step_approval.py        │ SKIP_STEP     │ 🐛 BUG_FOUND │
│ 02_role_swap_user_to_admin.py   │ ROLE_SWAP     │ ✅ REJECTED   │
└─────────────────────────────────┴───────────────┴──────────────┘

Flow: {flow-name}
Summary: {N} findings ({K} critical) | {R} rejected | {E} errors
```

### 11. Verify Completion

- `reports/{flow-name}/findings.json` exists, is valid JSON, and contains BOTH `findings` and `results` fields
- Every BUG_FOUND script appears as a `findings[]` entry
- `summary.bugs_found` equals the length of `findings[]`
- Auth sanity check (step 7) was run if the flow contained a login endpoint
- If `findings[]` is non-empty: `reports/{flow-name}/remediation.md` exists and lists every finding, Critical first
- Summary tables printed to chat

## Important Notes

- **NEVER modify or regenerate mutation scripts** — that's Saboteur's job
- **ALWAYS use 30s timeout** — never let a script run indefinitely
- **Capture stderr** — import errors and tracebacks are in stderr, not stdout
- **One script at a time** — execute sequentially so results are ordered and debuggable
- **Report ALL outcomes** — don't skip ERRORs, they indicate setup issues the user needs to fix
- **Be actionable** — for every ERROR, tell the user exactly how to fix it (install command, config change, etc.)
- **Flow isolation:** Read only from `mutations/{flow-name}/` and write only to `reports/{flow-name}/`.
