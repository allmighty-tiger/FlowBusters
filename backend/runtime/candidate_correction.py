"""Bounded candidate proposals; authenticated source facts and execution allowlist."""
import asyncio
import ast
import hashlib
import hmac
import json
import shutil
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from backend.runtime.application_model import validate_cross_flow_candidates, CROSS_FLOW_INSTRUCTIONS
from backend.runtime.ui_provenance import resolve_json_pointer, _verify_cross_flow_snapshot
from backend.runtime.state_map_correction import evidence_hashes
from backend.runtime.probe_executor import canonical, key_for


def _script_endpoint_paths(script: Path) -> set[str]:
    """Deterministically recover the endpoint PATHS a probe sends.

    Two shapes are recognised:
      * a leading ``CONFIG = {...}`` literal (the backend ``render_probe`` and
        coverage template put their actions there and call
        ``client.request(method, CONFIG['origin'] + path)`` — the URLs are not
        statically resolvable otherwise); and
      * direct ``httpx/requests/client`` calls whose URL is a resolvable constant
        (f-strings, concatenation, module-level name bindings) — the same AST
        resolution the endpoint catalog's preflight uses.
    Fully dynamic targets are skipped rather than guessed. This is how a candidate
    is bound to a script by its actual action chain, never by a random basename or
    the order files happen to sort in.
    """
    try:
        tree = ast.parse(script.read_text(encoding='utf-8'))
    except (OSError, ValueError, SyntaxError):
        return set()
    paths: set[str] = set()

    def add(value) -> None:
        if isinstance(value, str) and value:
            path = urlsplit(value).path
            paths.add(path or value)

    # 1) Leading CONFIG literal (coverage / render_probe template).
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name) and node.targets[0].id == 'CONFIG'):
            try:
                config = ast.literal_eval(node.value)
            except (ValueError, TypeError, SyntaxError):
                config = None
            if isinstance(config, dict):
                for key in ('setup', 'state_read'):
                    add(config.get(key))
                for action in config.get('actions', []) if isinstance(config.get('actions'), list) else []:
                    if isinstance(action, dict):
                        add(action.get('endpoint'))

    # 2) Direct client calls with resolvable URLs.
    names: dict[str, str] = {}

    def value(node) -> str | None:
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            return names.get(node.id)
        if isinstance(node, ast.JoinedStr):
            pieces = [value(part.value) if isinstance(part, ast.FormattedValue) else value(part)
                      for part in node.values]
            if all(isinstance(piece, str) for piece in pieces):
                return ''.join(pieces)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left, right = value(node.left), value(node.right)
            if isinstance(left, str) and isinstance(right, str):
                return left + right
        return None

    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names[target.id] = value(node.value)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        receiver = node.func.value
        if not isinstance(receiver, ast.Name) or receiver.id not in ('client', 'session', 'httpx', 'requests'):
            continue
        method = node.func.attr.upper()
        if method not in ('GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS', 'REQUEST'):
            continue
        args = [value(arg) for arg in node.args]
        if method == 'REQUEST':
            _method, url = (args + [None, None])[:2]
        else:
            url = args[0] if args else next((value(k.value) for k in node.keywords if k.arg == 'url'), None)
        if isinstance(url, str) and (urlsplit(url).scheme or url.startswith('/')):
            add(url)
    return paths


def validate_candidate(candidate, run, root, scripts=None):
    """Validate one candidate. If ``scripts`` is given it is the backend's
    deterministic binding (by action chain/invariant/evidence) and is checked for
    full coverage; otherwise the candidate's own ``scripts`` field is used."""
    validate_cross_flow_candidates({'schema_version': 1, 'candidates': [candidate]}, run, root)
    facts = candidate.get('source_facts')
    if not isinstance(facts, list):
        raise ValueError('source_facts must reference actual HAR entries from at least two sources')
    covered = set()
    actions = json.dumps(candidate.get('actions', []))
    action_endpoints = candidate.get('action_endpoints', [])
    if not isinstance(action_endpoints, list):
        raise ValueError('action_endpoints must list exact endpoints for named actions')
    actions += json.dumps(action_endpoints)
    for fact in facts:
        source = fact.get('source_run')
        if source not in candidate['source_runs'] or fact.get('artifact') != 'recording.har':
            raise ValueError('Fact source must belong to source_runs; artifact must be recording.har')
        folder = run / 'cross_flow_sources' / source
        _verify_cross_flow_snapshot(run, source, folder, root=root)
        pointer = fact.get('json_pointer', '')
        import re
        if not re.fullmatch(r'/log/entries/(0|[1-9][0-9]*)', pointer):
            raise ValueError('Fact pointer must identify one exact HAR entry')
        entry = resolve_json_pointer(json.loads((folder / 'recording.har').read_text()), pointer)
        request = entry['request']
        from urllib.parse import urlsplit
        path = urlsplit(request['url']).path
        if (fact.get('method') != request['method'] or fact.get('url') != request['url']
                or fact.get('status') != entry['response']['status']):
            raise ValueError(f'Fact {source}{pointer} contradicts its authenticated HAR entry: '
                             f'expected {request["method"]} {request["url"]} status={entry["response"]["status"]}')
        if request['method'] == 'GET' or path not in actions:
            raise ValueError('Source fact must support a state-changing candidate action, not just a shared GET')
        covered.add(source)
    if covered != set(candidate['source_runs']) or len(covered) < 2:
        raise ValueError('Every declared source needs a resolved action fact; at least two distinct sources required')
    if scripts is None:
        scripts = candidate.get('scripts')
    if not isinstance(scripts, list) or not scripts:
        raise ValueError('Candidate needs explicit scripts filenames linking its executable probes')
    script_paths = []
    for script in scripts:
        if not isinstance(script, str) or Path(script).name != script or not script.endswith('.py'):
            raise ValueError('Invalid candidate script filename')
        path = run / 'mutations' / run.name / script
        if not path.is_file():
            raise ValueError('Candidate script does not exist: ' + script)
        script_paths.append(path)
    # Deterministic action-chain coverage: every state-changing endpoint the
    # candidate declares (and every cited source fact) must be sent by a bound
    # probe, and the bound probes must actually contain the cited facts. This is
    # what binds candidate -> script by verified action chain, not basename.
    declared = set()
    for item in candidate.get('actions', []):
        if isinstance(item, str) and item.startswith('/'):
            declared.add(item)
    for endpoint in candidate.get('action_endpoints', []):
        if isinstance(endpoint, str) and endpoint.startswith('/'):
            declared.add(endpoint)
    for fact in facts:
        declared.add(urlsplit(fact['url']).path)
    code = '\n'.join(path.read_text(encoding='utf-8') for path in script_paths)
    extracted = set()
    for path in script_paths:
        extracted |= _script_endpoint_paths(path)
    for endpoint in sorted(declared):
        if endpoint not in extracted:
            raise ValueError('Candidate action endpoint %s is not exercised by any bound probe script' % endpoint)
    for fact in facts:
        if urlsplit(fact['url']).path not in code:
            raise ValueError('Referenced action endpoint is absent from candidate probe source')


async def propose(config, run, flow, data, errors, env):
    with tempfile.TemporaryDirectory(prefix='fb-candidate-correction-') as temp:
        workspace = Path(temp)
        shutil.copytree(run / 'cross_flow_sources', workspace / 'cross_flow_sources')
        shutil.copytree(run / 'mutations', workspace / 'mutations')
        shutil.copyfile(run / 'cross_flow_inputs.json', workspace / 'cross_flow_inputs.json')
        if (run / 'source_action_facts.json').exists():
            shutil.copyfile(run / 'source_action_facts.json', workspace / 'source_action_facts.json')
        (workspace / 'candidates.json').write_text(json.dumps(data), encoding='utf-8')
        (workspace / 'errors.json').write_text(json.dumps(errors), encoding='utf-8')
        (workspace / 'mcp.json').write_text('{"mcpServers":{}}')
        prompt = CROSS_FLOW_INSTRUCTIONS.replace('{flow}', flow) + '''
CORRECTION ONLY: Read candidates.json and errors.json and immutable cross_flow_sources.
Read source_action_facts.json when available for exact, backend-enumerated HAR indexes.
Return ONLY JSON {"schema_version":1,"candidates":[...]}. Preserve candidate IDs,
claims and action chains. Correct provenance using actual facts; never invent a second
source, drop an invalid hypothesis or edit evidence. Include scripts filenames from
mutations for each candidate. No shell, HTTP, writes, browser or probe execution.
For logical named actions, add action_endpoints with exact paths from those scripts
instead of changing the original actions. Read the actual HAR indexes; never guess.
If a candidate cannot be justified, leave it unchanged for backend coverage reporting.
'''
        (workspace / 'prompt.txt').write_text(prompt, encoding='utf-8')
        proc = await asyncio.create_subprocess_exec(config.claude_bin, '--print', '--bare',
            '--setting-sources', '', '--output-format', 'json', '--tools', 'Read',
            '--json-schema', json.dumps({'type': 'object', 'required': ['schema_version', 'candidates'],
                'properties': {'schema_version': {'const': 1}, 'candidates': {'type': 'array', 'items': {'type': 'object'}}}}),
            '--mcp-config', str(workspace / 'mcp.json'), '--strict-mcp-config',
            '--system-prompt-file', str(workspace / 'prompt.txt'), '--model', config.model,
            '--dangerously-skip-permissions', 'Correct candidate provenance using errors.json.',
            cwd=workspace, env=env, stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), min(300, config.phase_timeout))
        except BaseException:
            if proc.returncode is None:
                proc.kill()
            await proc.communicate()
            raise
        if proc.returncode:
            raise ValueError('Candidate correction agent failed')
        envelope = json.loads(out)
        if envelope.get('is_error'):
            raise ValueError('Candidate correction agent reported an error')
        if isinstance(envelope.get('structured_output'), dict):
            return envelope['structured_output']
        result = envelope.get('result', '').strip()
        # Accept a single JSON fence, never extract a guessed JSON substring.
        if result.startswith('```json\n') and result.endswith('```'):
            result = result[len('```json\n'):-3].strip()
        return json.loads(result)


def load_gate(run, root):
    path = Path(run) / 'candidate_gate.json'
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    signature = data.pop('signature')
    if not hmac.compare_digest(signature, hmac.new(key_for(root), canonical(data), hashlib.sha256).hexdigest()):
        raise ValueError('Candidate gate signature invalid')
    if data['flow'] != Path(run).name:
        raise ValueError('Candidate gate run mismatch')
    return data


def _candidate_action_endpoints(candidate) -> set[str]:
    """Exact endpoint paths the candidate's action chain exercises.

    Covers declared action paths, named action endpoints, and (critically for
    cross-flow) the paths actually demonstrated by the candidate's source facts
    from each distinct source. A probe that reproduces that cross-source chain is
    the deterministic binding target."""
    endpoints: set[str] = set()
    for item in candidate.get('actions', []) or []:
        if isinstance(item, str) and item.startswith('/'):
            endpoints.add(item)
    for endpoint in candidate.get('action_endpoints', []) or []:
        if isinstance(endpoint, str) and endpoint.startswith('/'):
            endpoints.add(endpoint)
    for fact in candidate.get('source_facts', []) or []:
        if isinstance(fact, dict) and isinstance(fact.get('url'), str):
            endpoints.add(urlsplit(fact['url']).path)
    return endpoints


def _bind_candidate_scripts(candidate, script_actions: dict[str, set[str]]):
    """Bind a candidate to probe scripts by its verified action chain, not basename.

    Returns ``(scripts, reason)``. An exact, unique binding (one probe whose sent
    endpoints cover the candidate's cross-source action chain; or the single
    non-setup probe when the chain is not statically resolvable) is authoritative.
    A zero- or multi-way match is ambiguous: the candidate is left unverified with
    the precise reason instead of being guessed, renamed, or dropped."""
    targets = _candidate_action_endpoints(candidate)
    if targets:
        matches = [name for name, actions in script_actions.items() if targets <= actions]
        if len(matches) == 1:
            return [matches[0]], None
        if len(matches) == 0:
            return [], ('no bound probe exercises the full cross-source action chain %s'
                        % sorted(targets))
        return [], ('ambiguous: %d probe scripts each cover the action chain %s; '
                    'exactly one must own it' % (len(matches), sorted(matches)))
    non_setup = [name for name, actions in script_actions.items()
                 if any(a != '/api/demo/reset' for a in actions)]
    if len(non_setup) == 1:
        return non_setup, None
    if not non_setup:
        return [], 'no non-setup probe script available to bind to this candidate'
    return [], ('ambiguous: action chain not statically resolvable and %d non-setup '
                'scripts could own it; exactly one must be named' % len(non_setup))


async def correct_candidates(config, run, flow, env, progress):
    run = Path(run); root = Path(config.run_dir)
    path = run / 'flows' / flow / 'cross_flow_candidates.json'
    data = json.loads(path.read_text())
    if data.get('schema_version') != 1:
        raise ValueError('Candidate schema_version must be 1')
    original = data.get('candidates', [])
    if not isinstance(original, list) or not original:
        raise ValueError('Cross-flow could not be checked: no candidate hypotheses generated')
    # Some older candidate contracts use a unique name rather than an ID.
    # This is only a stable diagnostic label, never a source/provenance upgrade.
    for c in original:
        if not c.get('id') and isinstance(c.get('name'), str):
            c['id'] = c['name']
    ids = [c.get('id') for c in original]
    if any(not isinstance(i, str) or not i for i in ids) or len(set(ids)) != len(ids):
        raise ValueError('Candidate IDs must be non-empty and unique')
    baseline = evidence_hashes(run, flow)
    audit = run / 'candidate_corrections'; audit.mkdir(exist_ok=True)
    (audit / 'original.json').write_text(json.dumps(data, indent=2))
    if list((run / 'reports').glob('*/executions/*.json')):
        raise ValueError('Candidate correction refused after execution')
    # Static action chains of every AI probe in this run, used for deterministic
    # candidate -> script binding. Computed once; it never touches source evidence.
    script_actions: dict[str, set[str]] = {}
    for script in sorted((run / 'mutations' / flow).glob('*.py')):
        script_actions[script.name] = _script_endpoint_paths(script)
    errors: list[dict] = []
    # Deterministic binding pass: run before any retry so candidates whose only
    # problem is a missing/renamed script link are fixed directly, without trusting
    # a correction agent to re-emit a script name it might mangle.
    binding: dict[str, str | None] = {}
    for c in original:
        _scripts, reason = _bind_candidate_scripts(c, script_actions)
        c.pop('scripts', None)
        if reason is None:
            c['scripts'] = _scripts
            binding[c['id']] = None
        else:
            binding[c['id']] = reason
            errors.append({'candidate_id': c['id'], 'code': 'CANDIDATE_SCRIPT_BINDING',
                           'message': reason, 'candidate': c})
    (audit / 'binding.json').write_text(json.dumps(binding, indent=2))
    progress('Deterministically bound cross-flow candidates to probe scripts by '
             'action chain (%d of %d bound)'
             % (sum(1 for v in binding.values() if v is None), len(binding)))
    valid: list[dict] = []
    for attempt in range(3):
        if evidence_hashes(run, flow) != baseline:
            raise ValueError('Immutable source evidence changed during candidate correction')
        valid, errors = [], []
        for c in data['candidates']:
            try:
                validate_candidate(c, run, root, scripts=c.get('scripts'))
                valid.append(c)
            except (ValueError, KeyError, TypeError, OSError, AttributeError) as exc:
                errors.append({'candidate_id': c['id'], 'code': 'INVALID_SOURCE_PROVENANCE',
                               'message': str(exc), 'candidate': c})
        if not errors or attempt == 2:
            break
        feedback = {'attempt': attempt + 1, 'max_attempts': 2,
                    'errors': errors,
                    'note': ('scripts are bound deterministically by action chain; '
                             'do not rename scripts, only correct provenance')}
        (audit / f'feedback-{attempt+1}.json').write_text(json.dumps(feedback, indent=2))
        progress(f'Correcting cross-flow candidate provenance: attempt {attempt+1}/2; {len(errors)} invalid hypotheses')
        try:
            proposal = await propose(config, run, flow, data, feedback, env)
            if proposal.get('schema_version') != 1:
                raise ValueError('Candidate correction schema_version must be 1')
            rows = proposal['candidates']
            if [c.get('id') for c in rows] != ids:
                raise ValueError('Correction must preserve all candidate IDs and order')
            for old, new in zip(original, rows):
                for field in ('title', 'name', 'hypothesis', 'actions', 'expected_behavior'):
                    if old.get(field) != new.get(field):
                        raise ValueError('Correction changed hypothesis/action chain: ' + old['id'])
            # The deterministic binding is authoritative: discard any script names
            # the correction agent emitted or renamed and keep the bound ones.
            for old, new in zip(original, rows):
                bound = old.get('scripts')
                new.pop('scripts', None)
                if bound:
                    new['scripts'] = bound
            data = proposal
            (audit / f'proposal-{attempt+1}.json').write_text(json.dumps(proposal, indent=2))
        except (ValueError, KeyError, TypeError, asyncio.TimeoutError) as exc:
            progress('Candidate correction rejected: ' + (str(exc) or type(exc).__name__))
    allowed = sorted({s for c in valid for s in c.get('scripts', [])})
    if evidence_hashes(run, flow) != baseline:
        raise ValueError('Immutable source evidence changed during candidate correction')
    assigned = {s for c in data['candidates'] for s in c.get('scripts', []) if isinstance(s, str)}
    for script in sorted((run / 'mutations' / flow).glob('*.py')):
        if script.name not in assigned:
            errors.append({'candidate_id': 'unattributed:' + script.name,
                           'message': 'No candidate links this AI probe; excluded from execution'})
    gate = {'flow': flow, 'valid_candidate_ids': [c['id'] for c in valid],
            'allowed_scripts': allowed, 'unverified_coverage': errors}
    gate['signature'] = hmac.new(key_for(root), canonical(gate), hashlib.sha256).hexdigest()
    (run / 'candidate_gate.json').write_text(json.dumps(gate, indent=2))
    # Ambiguous or uncorrectable candidates stay in the report as unverified
    # candidate coverage (gate.unverified_coverage) with their precise reason;
    # backend coverage plans still run independently and are labelled separately.
    # We never fabricate a second source or weaken signatures to force a pass.
    path.write_text(json.dumps({'schema_version': 1, 'candidates': valid}, indent=2))
    return len(valid)
