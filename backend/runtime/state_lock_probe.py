"""
state_lock_probe.py — Deterministic business-logic coverage guarantee.

The LLM crew (Analyst → Saboteur → Prober) can miss the single most valuable
class of business-logic flaw: a child resource that can still be mutated while
its parent is in a locked/approved lifecycle state. The UI hides the control
after approval so the app *looks* secure, but a raw API replay exposes the
missing server-side gate. Whether the crew's LLMs actually generate and run
that exact probe is non-deterministic, so for a reliable assessment the backend
guarantees it itself, deterministically, from the captured HAR.

This module:
  1. find_child_mutation_target(har) — locate, from recording.har, a parent
     resource that has BOTH a lifecycle state field (DRAFT→…→APPROVED) and a
     child collection (a list of {id, …} items). Derive the child DELETE path,
     the recorded lifecycle-advancing POSTs, the locked state values, and the
     session cookie. No FlowShop-specific assumptions — it works from what was
     actually captured.
  2. run_probe(target, base_url) — reach the parent's locked state by replaying
     the recorded lifecycle POSTs (idempotent), then attempt the child DELETE
     and confirm whether the child was actually removed. Returns a finding dict
     when the server accepted the delete on a locked parent; None otherwise.
  3. merge_finding(run_dir, flow_name, finding, target_url) — create or merge a
     findings.json (+ remediation.md) under reports/<flow>/ so the standard
     orchestrator/report pipeline picks it up. Idempotent: it replaces any prior
     STATE_LOCK_PROBE finding and recomputes the summary.

Everything here is best-effort and must NEVER raise into the run: the caller
(ensure_business_logic_finding) wraps it so a probe failure degrades to "no
extra finding", it never turns a completed run into a failure.

Stdlib only — this runs in the backend process, not in a browser.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlsplit

logger = logging.getLogger("flowbusters.state_lock_probe")

# Words that hint a lifecycle value is a LOCKED/terminal state vs. a mutable one.
_LOCK_HINT = re.compile(r"approv|final|lock|compl|finish|accept|publish|confirm|paid|sett|active|closed|shipp", re.I)
_UNLOCK_HINT = re.compile(r"draft|creat|new|init|temp|open|start|in[_-]?prog|unsent", re.I)

_TIMEOUT = 10.0  # per HTTP call, seconds


# ── HAR parsing helpers ───────────────────────────────────────────────────────

def _parse_json_body(text: str) -> Any:
    if not text:
        return None
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None


def _har_entries(har_path: Path) -> list[dict]:
    try:
        data = json.loads(Path(har_path).read_text())
    except (OSError, json.JSONDecodeError):
        return []
    entries = (data.get("log") or {}).get("entries") or []
    return [e for e in entries if isinstance(e, dict)]


def _req_view(entry: dict) -> dict:
    r = entry.get("request") or {}
    url = r.get("url") or ""
    return {
        "method": (r.get("method") or "").upper(),
        "url": url,
        "path": urlsplit(url).path,
        "headers": r.get("headers") or [],
        "body": (r.get("postData") or {}).get("text") or "",
    }


def _resp_view(entry: dict) -> dict:
    r = entry.get("response") or {}
    return {
        "status": r.get("status"),
        "headers": r.get("headers") or [],
        "body": (r.get("content") or {}).get("text") or "",
    }


def _header_value(headers: list, name: str) -> Optional[str]:
    name = name.lower()
    for h in headers:
        if isinstance(h, dict) and (h.get("name") or "").lower() == name:
            return h.get("value")
    return None


def _iter_dicts_shallow(body: Any, max_items: int = 3) -> list[dict]:
    """The body plus shallowly-nested dicts / first dicts of shallow lists.
    Enough to find a lifecycle state field without deep recursion."""
    out: list[dict] = []
    if not isinstance(body, dict):
        return out
    out.append(body)
    for v in body.values():
        if isinstance(v, dict):
            out.append(v)
        elif isinstance(v, list):
            for x in v[:max_items]:
                if isinstance(x, dict):
                    out.append(x)
    return out


def _state_value(body: Any, field: str) -> Optional[str]:
    for obj in _iter_dicts_shallow(body):
        v = obj.get(field)
        if isinstance(v, str) and v.isupper() and len(v) >= 3:
            return v
    return None


# ── Target discovery ──────────────────────────────────────────────────────────

def find_child_mutation_target(har_path: Path) -> Optional[dict]:
    """Find a parent resource that has a lifecycle state field AND a child
    collection, and return everything needed to replay the exploit. Returns
    None when the HAR doesn't contain such a structure (probe is a no-op)."""
    entries = _har_entries(Path(har_path))
    if not entries:
        return None

    # 1) Candidate parents: a GET (2xx) whose JSON body holds BOTH a child
    #    collection (a list of dicts each carrying an "id") AND a lifecycle state
    #    field somewhere in the body. Collect all of them — body shape alone can't
    #    disambiguate (a HAR may carry a state-bearing object nested under a
    #    collection, e.g. GET /api/orders whose one row looks like a dashboard),
    #    so step 2 disambiguates on recorded transitions.
    candidates: list[tuple[dict, str]] = []  # (parent, state_field)
    for e in entries:
        r, resp = _req_view(e), _resp_view(e)
        if r["method"] != "GET" or not (200 <= (resp["status"] or 0) < 300):
            continue
        body = _parse_json_body(resp["body"])
        if not isinstance(body, dict):
            continue
        field = None
        for obj in _iter_dicts_shallow(body):
            for k, v in obj.items():
                if isinstance(v, str) and v.isupper() and len(v) >= 3:
                    field = k
                    break
            if field:
                break
        if not field:
            continue  # no lifecycle state anywhere in this body
        for key, val in body.items():
            if isinstance(val, list) and val and all(isinstance(x, dict) for x in val) \
               and any("id" in x for x in val):
                candidates.append(
                    ({"path": r["path"], "url": r["url"], "children_key": key, "req": r}, field)
                )
                break

    # 2) A real lifecycle parent has recorded TRANSITION requests on its own path
    #    (or a direct sub-path) — e.g. POST /api/dashboard/1/submit, /approve. A
    #    mere collection like /api/orders has none. This is what actually picks
    #    the right parent. Prefer the candidate with the most recorded transitions.
    def _transitions_for(path: str, collection_path: str) -> list[str]:
        out = []
        for e in entries:
            r, resp = _req_view(e), _resp_view(e)
            p = r["path"]
            if r["method"] in ("POST", "PUT", "PATCH") \
               and (p == path or (p.startswith(path + "/") and p != collection_path)) \
               and (200 <= (resp["status"] or 0) < 300):
                out.append(p)
        return out

    best = None
    for cand_parent, field in candidates:
        collection_path = cand_parent["path"] + "/" + cand_parent["children_key"]
        transitions = _transitions_for(cand_parent["path"], collection_path)
        if transitions and (best is None or len(transitions) > best[2]):
            best = (cand_parent, field, len(transitions), transitions)
    if best is None:
        return None
    parent, state_field, _n, parent_post_paths = best

    cookie = _header_value(parent["req"]["headers"], "cookie")
    if not cookie:
        return None

    parts = urlsplit(parent["url"])
    base = f"{parts.scheme}://{parts.netloc}"

    # 3) Child DELETE candidates. REST convention from the captured GET: the
    #    collection lives at <parent>/<children_key>, so a child is
    #    <parent>/<children_key>/<id>; fall back to <parent>/<id>.
    child_delete_paths = [
        parent["path"] + "/" + parent["children_key"],
        parent["path"],
    ]

    # 4) Locked state values: every observed value of the state field that looks
    #    terminal (or is not obviously a mutable/draft state).
    locked_states: list[str] = []
    if state_field:
        seen: set[str] = set()
        for e in entries:
            resp = _resp_view(e)
            if 200 <= (resp["status"] or 0) < 300:
                v = _state_value(_parse_json_body(resp["body"]), state_field)
                if v:
                    seen.add(v)
        locked_states = sorted(
            v for v in seen
            if _LOCK_HINT.search(v) or not _UNLOCK_HINT.search(v)
        )

    # 5) Login credentials (best-effort) so a stale session can be re-established.
    login = _find_login(entries)

    return {
        "base": base,
        "parent_path": parent["path"],
        "children_key": parent["children_key"],
        "child_delete_paths": child_delete_paths,
        "lifecycle_field": state_field,
        "lifecycle_posts": parent_post_paths,
        "locked_states": locked_states,
        "cookie": cookie,
        "login": login,
    }


def _find_login(entries: list[dict]) -> Optional[dict]:
    """Locate a recorded login POST so the probe can re-auth if its captured
    session cookie has gone stale (e.g. the server restarted)."""
    for e in entries:
        r = _req_view(e)
        if r["method"] == "POST" and re.search(r"login|signin|auth", r["path"], re.I) \
           and r["body"]:
            ct = _header_value(r["headers"], "content-type") or "application/x-www-form-urlencoded"
            return {"url": r["url"], "body": r["body"], "content_type": ct}
    return None


# ── HTTP execution ────────────────────────────────────────────────────────────

def _cookie_from_response(headers, body: str) -> Optional[str]:
    sc = headers.get("Set-Cookie") if headers else None
    if sc:
        return sc.split(";")[0].strip()
    data = _parse_json_body(body)
    if isinstance(data, dict):
        for k in ("token", "session", "session_id", "sid", "jwt"):
            if data.get(k):
                return f"session={data[k]}"
    return None


def _relogin(cookie_holder: dict, login: dict) -> bool:
    try:
        req = urllib.request.Request(
            login["url"],
            data=login["body"].encode("utf-8"),
            method="POST",
            headers={"Content-Type": login.get("content_type", "application/x-www-form-urlencoded")},
        )
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            body = resp.read().decode("utf-8", "replace")
            new_cookie = _cookie_from_response(resp.headers, body)
            if new_cookie:
                cookie_holder["cookie"] = new_cookie
                return True
    except Exception:
        pass
    return False


def _do_request(method: str, url: str, cookie_holder: dict, login: Optional[dict],
                data: Optional[bytes] = None) -> tuple[Optional[int], Optional[str]]:
    """Perform one request. On a 401, attempt a single re-login and retry once.
    Returns (status, body_text); (None, None) on network error."""
    for attempt in range(2):
        req = urllib.request.Request(
            url, data=data, method=method,
            headers={"Cookie": cookie_holder["cookie"], "User-Agent": "flowbusters-state-lock-probe"},
        )
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
                return resp.status, resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            try:
                body = e.read().decode("utf-8", "replace")
            except Exception:
                body = ""
            if e.code == 401 and attempt == 0 and login and _relogin(cookie_holder, login):
                continue
            return e.code, body
        except Exception:
            return None, None
    return None, None


def _children_of(body: Any, key: str) -> list[dict]:
    if isinstance(body, dict) and isinstance(body.get(key), list):
        return [x for x in body[key] if isinstance(x, dict)]
    return []


def _current_state(target: dict, auth: dict, login: Optional[dict]) -> Optional[str]:
    _, body = _do_request("GET", target["base"] + target["parent_path"], auth, login)
    return _state_value(_parse_json_body(body), target["lifecycle_field"] or "state")


def _ensure_locked(target: dict, auth: dict) -> Optional[str]:
    """Advance the parent to a locked state by replaying the recorded lifecycle
    transitions in order (idempotent: each only takes effect from its expected
    prior state, so a parent already locked is left untouched). Returns the
    parent's state after the attempt (may still be unlocked — caller must check)."""
    if not target["lifecycle_posts"]:
        return _current_state(target, auth, target.get("login"))
    # Replay ALL recorded transitions in order, driving the parent to its furthest
    # (terminal) state — e.g. DRAFT → PENDING_APPROVAL → APPROVED. Transitions are
    # idempotent in practice (an out-of-sequence one is 409'd), so this is safe from
    # any starting state and lands on the fully-locked state rather than the first
    # one. This is a genuine replay of the recorded happy path to its end.
    for path in target["lifecycle_posts"]:
        # Body shape from the recording is unknown; empty JSON object matches the
        # no-payload lifecycle transitions these flows use (submit/approve).
        _do_request("POST", target["base"] + path, auth, target.get("login"), data=b"{}")
    return _current_state(target, auth, target.get("login"))


def run_probe(target: dict, base_url: str = "") -> Optional[dict]:
    """Reach the parent's locked state, attempt the child DELETE, and confirm
    whether the child was actually removed. Returns a finding dict on a
    confirmed violation, else None."""
    auth = {"cookie": target["cookie"]}
    login = target.get("login")
    key = target["children_key"]

    # Baseline read.
    status, body = _do_request("GET", target["base"] + target["parent_path"], auth, login)
    if status is None or not (200 <= status < 300):
        return None
    before = _parse_json_body(body)
    if not _children_of(before, key):
        return None

    # Advance the parent to its locked state (no-op if already locked).
    _ensure_locked(target, auth)

    # Re-read to get a currently-present child (lifecycle may have consumed one)
    # and — critically — confirm the parent IS in a locked state right now. We
    # only fire on a delete accepted on a LOCKED parent: without this guard a
    # child collection that has no lifecycle would let us delete a child in a
    # normal (unlocked) state (a legit 2xx) and false-positive.
    status, body = _do_request("GET", target["base"] + target["parent_path"], auth, login)
    if status is None or not (200 <= status < 300):
        return None
    live = _parse_json_body(body)
    child_ids = [c["id"] for c in _children_of(live, key) if c.get("id") is not None]
    if not child_ids:
        return None
    state_now = _state_value(live, target["lifecycle_field"] or "state")
    if not target["locked_states"] or state_now not in target["locked_states"]:
        return None  # parent not locked → a delete here is not the flaw we test
    child_id = child_ids[0]
    n_before = len(_children_of(live, key))

    # Attempt the child DELETE on (presumably) the locked parent.
    del_status = None
    del_body = None
    del_url = None
    for path in target["child_delete_paths"]:
        url = target["base"] + path + "/" + str(child_id)
        del_status, del_body = _do_request("DELETE", url, auth, login)
        if del_status is None:
            continue
        if del_status == 404:
            continue  # try the next candidate path
        del_url = url
        break
    if del_url is None:
        return None

    # Confirm the effect: is the child gone now?
    status, body = _do_request("GET", target["base"] + target["parent_path"], auth, login)
    final = _parse_json_body(body) or {}
    remaining = [c.get("id") for c in _children_of(final, key)]
    gone = child_id not in remaining
    n_after = len(remaining)

    if not (200 <= (del_status or 0) < 300) or not gone:
        return None  # server rejected, or the delete had no effect → not the flaw

    # Re-read the locked state for the report.
    state_after = _state_value(_parse_json_body(body), target["lifecycle_field"] or "state") \
        or (target["locked_states"][0] if target["locked_states"] else "LOCKED")

    return _build_finding(target, del_url, child_id, n_before, n_after, del_status, del_body, state_after)


def _child_noun(key: str) -> str:
    k = key.strip()
    return k[:-1] if k.endswith("s") and len(k) > 1 else k


def _build_finding(target: dict, delete_url: str, child_id: Any, n_before: int, n_after: int,
                   del_status: int, del_body: Optional[str], state_after: str) -> dict:
    lifecycle = target["lifecycle_posts"] or []
    title = (
        f"{_child_noun(target['children_key'])} removal is accepted on a locked "
        f"({state_after}) parent — server-side lifecycle gate missing on DELETE"
    )
    evidence = {
        "summary": (
            f"Replayed the recorded parent lifecycle ({len(lifecycle)} step(s)) to reach {state_after}, "
            f"then issued DELETE {delete_url} (child id={child_id}). The server returned HTTP {del_status} "
            f"and the child was actually removed — the parent now lists {n_after} of {n_before} children. "
            f"The UI hides this control once the parent is locked, but the backend enforces no lifecycle "
            f"check on DELETE."
        ),
        "requests": [
            {"label": "replay_parent_lifecycle", "method": "POST",
             "url": target["base"] + target["parent_path"] + "/…",
             "body": "(recorded state transitions: " + ", ".join(target["lifecycle_posts"]) + ")"},
            {"label": "delete_child_on_locked_parent", "method": "DELETE", "url": delete_url, "body": None},
            {"label": "confirm_child_removed", "method": "GET",
             "url": target["base"] + target["parent_path"], "body": None},
        ],
        "responses": [
            {"label": "replay_parent_lifecycle", "status_code": 200, "response_body": f"parent state -> {state_after}"},
            {"label": "delete_child_on_locked_parent", "status_code": del_status,
             "response_body": (del_body or "")[:200]},
            {"label": "confirm_child_removed", "status_code": 200,
             "response_body": f"children: {n_before} -> {n_after} (child {child_id} removed)"},
        ],
    }
    return {
        "id": "F-BL",  # final id assigned in merge_finding
        "title": title,
        "source": "STATE_LOCK_PROBE",
        "severity": "High",
        "cwe": ["CWE-841", "CWE-670"],
        "script": "state_lock_probe.py",
        "mutation_type": "REPLAY_ATTACK",
        "url_tested": delete_url,
        "evidence": evidence,
    }


# ── findings.json / remediation.md merge ──────────────────────────────────────

def _recompute_summary(data: dict) -> None:
    findings = data.get("findings") or []
    results = data.get("results") or []
    data["summary"] = {
        "bugs_found": len(findings),
        "critical_findings": sum(1 for f in findings if isinstance(f, dict) and f.get("severity") == "Critical"),
        "rejected": sum(1 for r in results if isinstance(r, dict) and r.get("outcome") == "REJECTED"),
        "errors": sum(1 for r in results if isinstance(r, dict) and r.get("outcome") == "ERROR"),
    }


def _render_remediation_section(f: dict, target_url: str = "") -> str:
    cwe = ", ".join(f.get("cwe", []))
    ev = f.get("evidence", "")
    ev_str = ev if isinstance(ev, str) else json.dumps(ev, indent=2, ensure_ascii=False)
    return (
        f"## Finding {f.get('id', '?')}: {f.get('title', '')}\n\n"
        f"- **CWE:** {cwe}\n"
        f"- **Severity:** {f.get('severity', 'High')}\n"
        f"- **Source:** {f.get('source', 'STATE_LOCK_PROBE')} (script: {f.get('script', 'state_lock_probe.py')})\n"
        f"- **Endpoint:** DELETE {f.get('url_tested', '')}\n"
        f"- **Issue:** The backend permits a child-resource mutation (delete) while the parent is in its "
        f"locked/approved lifecycle state. The UI hides the control, but the server performs no lifecycle "
        f"check, so a raw API replay changes data that should be immutable.\n"
        f"- **Evidence:**\n  {ev_str}\n"
        f"- **Fix:**\n"
        f"  - Enforce the lifecycle gate server-side on DELETE (and every other child mutation): return 4xx "
        f"when the parent is not in a mutable state.\n"
        f"  - Treat the UI's hidden/disabled controls as advisory only; re-validate state on each "
        f"state-changing request.\n"
        f"  - Add a regression test that replays the captured delete against an approved/locked parent.\n"
    )


def merge_finding(run_dir: Path, flow_name: str, finding: dict, target_url: str = "") -> str:
    """Create or merge the probe finding into reports/<flow>/findings.json and
    remediation.md. Idempotent (replaces any prior STATE_LOCK_PROBE finding).
    Returns the assigned finding id."""
    reports = Path(run_dir) / "reports" / flow_name
    reports.mkdir(parents=True, exist_ok=True)
    fp = reports / "findings.json"
    mp = reports / "remediation.md"

    data = None
    if fp.exists():
        try:
            data = json.loads(fp.read_text())
        except (OSError, json.JSONDecodeError):
            data = None
    if not isinstance(data, dict) or "findings" not in data:
        data = {
            "run_timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "target_url": target_url,
            "flow_name": flow_name,
            "total_scripts": 0,
            "findings": [],
            "results": [],
            "summary": {"bugs_found": 0, "critical_findings": 0, "rejected": 0, "errors": 0},
        }

    findings = [f for f in data.get("findings", [])
                if not (isinstance(f, dict) and f.get("source") == "STATE_LOCK_PROBE")]
    finding = dict(finding)
    finding["id"] = "F-%03d" % (len(findings) + 1)
    findings.append(finding)
    data["findings"] = findings
    if not data.get("target_url") and target_url:
        data["target_url"] = target_url
    _recompute_summary(data)
    fp.write_text(json.dumps(data, indent=2, ensure_ascii=False))

    _merge_remediation(mp, finding, target_url)
    return finding["id"]


def _merge_remediation(mp: Path, finding: dict, target_url: str = "") -> None:
    section = _render_remediation_section(finding, target_url)
    if mp.exists():
        try:
            text = mp.read_text()
        except OSError:
            text = ""
        if "state_lock_probe" in text.lower() or "STATE_LOCK_PROBE" in text:
            return  # already documented (idempotent)
        text = (text.rstrip() + "\n\n---\n\n" + section + "\n") if text.strip() else section + "\n"
        mp.write_text(text)
    else:
        header = (
            "# FlowBusters Remediation Report\n\n"
            f"**Target:** {target_url}\n"
            f"**Run Date:** {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n\n"
            "## Summary\n\n"
            "The following business-logic vulnerability was confirmed by the FlowBusters "
            "state-lock probe (a deterministic coverage check for child mutations on a "
            "locked parent).\n\n"
            "## Findings\n\n"
        )
        mp.write_text(header + section + "\n")


# ── Public entry point ────────────────────────────────────────────────────────

async def ensure_business_logic_finding(run_dir: Path, flow_name: str, target_url: str = "") -> Optional[str]:
    """Run the deterministic probe and, on a confirmed violation, guarantee a
    finding in the report. Never raises — any failure degrades to "no extra
    finding" and is logged. Returns the finding id, or None."""
    try:
        har = Path(run_dir) / "flows" / flow_name / "recording.har"
        if not har.exists():
            logger.info("state_lock_probe: no recording.har — skipping")
            return None
        target = find_child_mutation_target(har)
        if not target:
            logger.info("state_lock_probe: no lifecycle parent + child collection in HAR — skipping")
            return None
        finding = await asyncio.to_thread(run_probe, target, target_url)
        if not finding:
            logger.info("state_lock_probe: no violation (endpoint enforces lifecycle, or no child / network error)")
            return None
        fid = merge_finding(Path(run_dir), flow_name, finding, target_url)
        logger.info("state_lock_probe: BUSINESS-LOGIC VIOLATION confirmed → merged (id=%s) url=%s",
                    fid, finding["url_tested"])
        return fid
    except Exception as e:  # noqa: BLE001 — a probe failure must never fail the run
        logger.warning("state_lock_probe: failed (non-fatal): %s", e)
        return None