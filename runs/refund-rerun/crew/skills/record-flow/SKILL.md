# Skill: Record Flow

## Purpose

Capture a complete user workflow via Playwright MCP, producing structured DOM interaction traces and network traffic (HAR) for downstream analysis.

## Confidence: medium

## When to Use

- Phase 1 of FlowBusters pipeline
- User wants to record a business workflow for security analysis
- Need to capture both UI interactions and API calls from a live application
- The run may include an optional `--flow-name`; if omitted, use `default`

## Inputs

- Target URL
- Optional `flow-name` parameter

`flow-name` must be kebab-case: lowercase letters, numbers, and hyphens only.
If `flow-name` is omitted, default to `default`.

## Procedure

### 1. Resolve Flow Output Directory

Resolve the flow name before recording:
- Use the provided `flow-name` when present
- Otherwise use `default`
- Validate it matches `^[a-z0-9]+(?:-[a-z0-9]+)*$`

Create and use this output directory for the entire phase:
- `flows/{flow-name}/`

### 2. Initialize Browser Session

```
Use Playwright MCP browser_navigate to open the target URL in a headed browser.
The browser must be visible so the user can interact with it.
```

### 3. Inform the User

Tell the user:
> "🎥 Browser is open at {target_url}. Please complete your full workflow:
> 1. Log in with your credentials
> 2. Perform the complete business flow you want to test
> 3. Tell me when you're done
>
> I'm recording all interactions and network traffic for flow `{flow-name}`."

### 4. Capture Semantic UI States

While the user works, use Playwright MCP accessibility snapshots to capture
chronological UI states. Deduplicate unchanged states. Keep URL/title, visible
text, controls and enabled/disabled state, labels/non-sensitive values, alerts,
prices, limits, permissions and terminal-state language. Strip MCP element refs,
redact passwords/tokens, cap the retained context, and never save the full DOM.

Each state must include a compact `changes_from_previous` with appeared and
disappeared semantic elements. These are observed before/after states, not proof
of a particular click; never fabricate interaction events.

### 5. Capture Network Traffic (HAR)

Playwright MCP writes network data **directly to disk**. The full-detail call
contains status and headers, but bodies are separate request parts. Capture
those parts explicitly; never infer a body from `content-length`.

#### 5a. Get Request List (to file)

```
browser_network_requests(static=false, filter="/api/.*|\\.(json)$", filename="flows/{flow-name}/har_data/network_requests.log")
```

The `filter` parameter tells Playwright MCP to only return API/JSON requests.
The `filename=` parameter writes the result directly to disk.

**Fallback:** If filtering returns fewer than 2 entries, call again without
the `filter` parameter (all requests, static=false):
```
browser_network_requests(static=false, filename="flows/{flow-name}/har_data/network_requests.log")
```

Create the directory first: `mkdir -p flows/{flow-name}/har_data`

#### 5b. Collect Request Details (to files)

Read `network_requests.log` to get the original 1-based indices. Filtering can
leave gaps, so preserve the printed number. For each index N, save the full
details and both body parts. Use a zero-padded filename (`001`, `002`, ...):

```
browser_network_request(index=N, filename="flows/{flow-name}/har_data/request_NNN.log")
browser_network_request(index=N, part="request-body", filename="flows/{flow-name}/har_data/request_NNN_request_body.txt")
browser_network_request(index=N, part="response-body", filename="flows/{flow-name}/har_data/request_NNN_response_body.txt")
```

Empty request bodies are normal for GET. A successful JSON response normally
has a body; if its response-body file is absent or empty, the capture gate must
fail. Use `filename=` on every call. Do not rewrite tool output through the model.

#### 5c. Synthesize HAR 1.2

Point the helper at the directory of MCP output files:

```bash
python3 crew/scripts/synthesize_har.py \
    flows/{flow-name}/har_data \
    flows/{flow-name}/recording.har
```

- Verify exit code 0 and that `recording.har` exists.
- If the helper fails, report the error and enter FAILED gate.
- **Do NOT attempt to assemble HAR by hand.**

### 6. Save Artifacts

When the user signals completion:

**Save `flows/{flow-name}/demo.json`:**
```json
{
  "target_url": "https://...",
  "flow_name": "{flow-name}",
  "timestamp_start": "ISO-8601",
  "timestamp_end": "ISO-8601",
  "workflow_timeline": {
    "ui_states": [
      {
        "step": 1,
        "url": "https://...",
        "elements": ["button Apply Coupon enabled", "text: One coupon per order"],
        "changes_from_previous": {"appeared": [], "disappeared": []}
      }
    ],
    "network_sequence": [
      {"sequence": 1, "method": "POST", "url": "https://.../coupon", "status": 200,
       "request_body_keys": ["code"], "response_body_keys": ["discount", "total"]}
    ]
  }
}
```

**`flows/{flow-name}/recording.har`:** Already produced by `synthesize_har.py`
in step 5c. Verify it exists and contains at least 1 entry.

### 7. Verify Output

- Confirm `flows/{flow-name}/demo.json` exists and is valid JSON
- Confirm `flows/{flow-name}/recording.har` exists and is valid HAR
- Confirm at least 1 UI state was captured
- Confirm at least 1 network entry was recorded

### 8. Report Gate Status

```
✅ Phase 1 RECORD complete.
   Flow: {flow-name}
   Interactions captured: {N}
   Network requests recorded: {M}
   Duration: {seconds}s
   Files: flows/{flow-name}/demo.json, flows/{flow-name}/recording.har
```

## Important Notes

- **Sensitive data:** Redact passwords in demo.json `value` fields. Keep them in HAR (needed for replay).
- **Session cookies:** These are critical — Analyst needs them. Do NOT strip auth headers from HAR.
- **Timing:** Wait patiently for the user. Do not rush or timeout the recording.
- **Playwright MCP only:** Use the Playwright MCP tools (browser_navigate, browser_snapshot, browser_click, etc.). Never shell out to puppeteer or selenium.
- **Flow isolation:** Only write artifacts inside `flows/{flow-name}/` for the active flow.
