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

### 4. Capture DOM Interactions

While the user works, use Playwright MCP snapshot capabilities to track:
- **Clicks** — element selectors and text content
- **Form fills** — field names and input types (NOT values for sensitive fields)
- **Navigation** — URL changes and page transitions
- **Submissions** — form submissions and their targets

Structure each interaction as:
```json
{
  "type": "click|fill|navigate|submit|select",
  "selector": "CSS or accessibility selector",
  "value": "input value or null",
  "timestamp": "ISO-8601",
  "url": "current page URL"
}
```

### 5. Capture Network Traffic (HAR)

Playwright MCP writes network data **directly to disk** — the model never
regenerates JSON. Use the `filename=` parameter on every network tool call
so data flows: MCP → file (milliseconds, not token-by-token).

#### 5a. Get Request List (to file)

```
browser_network_requests(static=false, filter="/api/.*|\\.(json)$", filename="flows/{flow-name}/har_data/network_requests.json")
```

The `filter` parameter tells Playwright MCP to only return API/JSON requests.
The `filename=` parameter writes the result directly to disk.

**Fallback:** If filtering returns fewer than 2 entries, call again without
the `filter` parameter (all requests, static=false):
```
browser_network_requests(static=false, filename="flows/{flow-name}/har_data/network_requests.json")
```

Create the directory first: `mkdir -p flows/{flow-name}/har_data`

#### 5b. Collect Request Details (to files)

Read `network_requests.json` to get the list of indices. For each index N,
call `browser_network_request` with `filename=`:

```
browser_network_request(index=N, filename="flows/{flow-name}/har_data/request_N.json")
```

**CRITICAL:** Use `filename=` on every call. Playwright MCP writes the file
directly. **Do NOT regenerate the data as text output.**

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
  "interactions": [
    { "type": "navigate", "selector": null, "value": "https://...", "timestamp": "..." },
    { "type": "fill", "selector": "#username", "value": "[REDACTED]", "timestamp": "..." },
    { "type": "click", "selector": "button[type=submit]", "value": null, "timestamp": "..." }
  ]
}
```

**`flows/{flow-name}/recording.har`:** Already produced by `synthesize_har.py`
in step 5c. Verify it exists and contains at least 1 entry.

### 7. Verify Output

- Confirm `flows/{flow-name}/demo.json` exists and is valid JSON
- Confirm `flows/{flow-name}/recording.har` exists and is valid HAR
- Confirm at least 1 interaction was captured
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
