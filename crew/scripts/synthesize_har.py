#!/usr/bin/env python3
"""
synthesize_har.py — Deterministic HAR 1.2 synthesizer.

Two input modes:

  1. Directory mode (recommended — Playwright MCP writes files directly):
     python3 synthesize_har.py <network_data_dir> <output_recording.har>

     Reads files produced by browser_network_requests(filename=) and
     browser_network_request(index=N, filename=):
       - network_requests.json  (list from browser_network_requests)
       - request_001.json …     (details from browser_network_request)

  2. Legacy mode (single raw_network.json file):
     python3 synthesize_har.py <input_raw_network.json> <output_recording.har>

Exit codes:
    0 — success
    1 — failure (prints error to stderr)
"""
import json
import os
import sys
import glob as globmod
from urllib.parse import urlparse, parse_qs


def load_directory_mode(directory: str) -> list:
    """Load entries from Playwright MCP file output."""
    requests_file = os.path.join(directory, "network_requests.json")
    if not os.path.exists(requests_file):
        raise FileNotFoundError(
            f"Directory mode: {requests_file} not found. "
            "Did browser_network_requests(filename=...) succeed?"
        )

    with open(requests_file, "r", encoding="utf-8") as f:
        requests_data = json.load(f)

    entries = []
    # Process request detail files (request_001.json, request_002.json, ...)
    detail_files = sorted(globmod.glob(os.path.join(directory, "request_*.json")))
    for detail_file in detail_files:
        with open(detail_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Playwright MCP may return a dict with an "entries" key or a flat entry
        entry = data
        if "url" not in entry and "method" not in entry:
            # Try to extract from nested structure
            if "entries" in data and isinstance(data["entries"], list):
                entry = data["entries"][0] if data["entries"] else {}
            elif isinstance(data, dict):
                # Flatten common MCP response shapes
                pass

        entries.append(entry)

    if not entries:
        raise ValueError(
            f"Directory mode: No request detail files found in {directory}. "
            "Expected request_*.json files from browser_network_request(index=N, filename=...)"
        )
    return entries


def load_legacy_mode(path: str) -> list:
    """Load raw network JSON (legacy single-file format)."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        raise FileNotFoundError(f"Input file not found: {path}")
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in {path}: {e}")

    entries = data.get("entries", [])
    if not isinstance(entries, list):
        raise ValueError("'entries' must be a list")
    return entries


def load_raw(path: str) -> list:
    """Auto-detect input mode: directory vs single file."""
    if os.path.isdir(path):
        return load_directory_mode(path)
    else:
        return load_legacy_mode(path)


def validate_entries(entries: list) -> None:
    """Validate that every entry has the required fields."""
    required = {"url", "method", "status"}
    for i, entry in enumerate(entries):
        missing = required - set(entry.keys())
        if missing:
            # Allow entries where the MCP tool saved a slightly different shape
            # — if it has 'request' and 'response' keys, it's already HAR-ish
            if "request" in entry and "response" in entry:
                continue
            raise ValueError(
                f"Entry {i} missing required fields: {sorted(missing)}. "
                f"Has: {sorted(entry.keys())}"
            )


def headers_to_list(headers_dict) -> list:
    """Convert dict of headers to HAR [{name, value}] format."""
    if not headers_dict or not isinstance(headers_dict, dict):
        return []
    return [{"name": k, "value": str(v)} for k, v in headers_dict.items()]


def parse_query_string(url: str) -> list:
    """Extract query string params as HAR [{name, value}] format."""
    parsed = urlparse(url)
    if not parsed.query:
        return []
    params = parse_qs(parsed.query, keep_blank_values=True)
    result = []
    for name, values in params.items():
        for value in values:
            result.append({"name": name, "value": value})
    return result


def to_timestamp(iso_string: str) -> float:
    """Convert ISO 8601 timestamp to epoch milliseconds."""
    if not iso_string:
        return 0.0
    try:
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(iso_string.replace("Z", "+00:00"))
        return dt.timestamp() * 1000
    except Exception:
        return 0.0


def entry_to_har(raw: dict) -> dict:
    """Convert a single raw network entry to HAR 1.2 entry format."""
    url = raw.get("url", "")
    method = raw.get("method", "GET")
    request_headers = raw.get("request_headers", {}) or {}
    request_body = raw.get("request_body", "") or ""
    status = raw.get("status", 0)
    status_text = raw.get("status_text", "")
    response_headers = raw.get("response_headers", {}) or {}
    response_body = raw.get("response_body", "") or ""
    response_mime = raw.get("response_mime", "application/octet-stream") or "application/octet-stream"
    started_at = raw.get("started_at", "")
    duration_ms = raw.get("duration_ms", 0) or 0

    # Ensure types
    if isinstance(status, str):
        try:
            status = int(status)
        except ValueError:
            status = 0
    if isinstance(duration_ms, str):
        try:
            duration_ms = float(duration_ms)
        except ValueError:
            duration_ms = 0

    request_body_bytes = len(request_body.encode("utf-8")) if request_body else 0
    response_body_bytes = len(response_body.encode("utf-8")) if response_body else 0

    started_dt = to_timestamp(started_at)

    # Build request
    req = {
        "method": method,
        "url": url,
        "httpVersion": "HTTP/1.1",
        "cookies": [],
        "headers": headers_to_list(request_headers),
        "queryString": parse_query_string(url),
        "headersSize": -1,
        "bodySize": request_body_bytes,
    }

    # Add postData only if there is a body
    if request_body:
        ct = ""
        for h in (request_headers or {}):
            if h.lower() == "content-type":
                ct = request_headers[h]
                break
        req["postData"] = {
            "mimeType": ct or "application/octet-stream",
            "text": request_body,
        }

    # Build response
    resp = {
        "status": status,
        "statusText": status_text or "",
        "httpVersion": "HTTP/1.1",
        "cookies": [],
        "headers": headers_to_list(response_headers),
        "content": {
            "size": response_body_bytes,
            "mimeType": response_mime,
            "text": response_body,
        },
        "redirectURL": "",
        "headersSize": -1,
        "bodySize": response_body_bytes,
    }

    # Check for redirect
    if status in (301, 302, 303, 307, 308):
        for h in (response_headers or {}):
            if h.lower() == "location":
                resp["redirectURL"] = response_headers[h]
                break

    return {
        "startedDateTime": started_at if started_at else "",
        "time": duration_ms,
        "request": req,
        "response": resp,
        "cache": {},
        "timings": {
            "send": 0,
            "wait": duration_ms,
            "receive": 0,
        },
    }


def main():
    if len(sys.argv) != 3:
        print(
            f"Usage: {sys.argv[0]} <input_raw_network.json> <output_recording.har>",
            file=sys.stderr,
        )
        sys.exit(1)

    input_path = sys.argv[1]
    output_path = sys.argv[2]

    try:
        entries = load_raw(input_path)
        validate_entries(entries)
    except (FileNotFoundError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    har_entries = []
    for raw_entry in entries:
        har_entries.append(entry_to_har(raw_entry))

    har = {
        "log": {
            "version": "1.2",
            "creator": {"name": "flowbusters-portal", "version": "v2"},
            "entries": har_entries,
        }
    }

    # Final validation before writing
    assert har["log"]["version"] == "1.2"
    assert "name" in har["log"]["creator"]
    assert "version" in har["log"]["creator"]
    assert isinstance(har["log"]["entries"], list)
    for entry in har["log"]["entries"]:
        assert entry["request"].get("method"), "request.method missing"
        assert entry["request"].get("url"), "request.url missing"
        assert entry["response"].get("status") is not None, "response.status missing"

    try:
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(har, f, indent=2, ensure_ascii=False)
    except OSError as e:
        print(f"ERROR: Could not write {output_path}: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"HAR written: {output_path} ({len(har_entries)} entries)")
    sys.exit(0)


if __name__ == "__main__":
    main()
