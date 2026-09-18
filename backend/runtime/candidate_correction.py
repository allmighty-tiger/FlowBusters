"""Bounded candidate proposals; authenticated source facts and execution allowlist."""
import asyncio
import hashlib
import hmac
import json
import shutil
import tempfile
from pathlib import Path

from backend.runtime.application_model import validate_cross_flow_candidates, CROSS_FLOW_INSTRUCTIONS
from backend.runtime.ui_provenance import resolve_json_pointer, _verify_cross_flow_snapshot
from backend.runtime.state_map_correction import evidence_hashes
from backend.runtime.probe_executor import canonical, key_for


def validate_candidate(candidate, run, root):
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
    scripts = candidate.get('scripts')
    if not isinstance(scripts, list) or not scripts:
        raise ValueError('Candidate needs explicit scripts filenames linking its executable probes')
    for script in scripts:
        if not isinstance(script, str) or Path(script).name != script or not script.endswith('.py'):
            raise ValueError('Invalid candidate script filename')
        if not (run / 'mutations' / run.name / script).is_file():
            raise ValueError('Candidate script does not exist: ' + script)
    code = '\n'.join((run / 'mutations' / run.name / s).read_text(encoding='utf-8') for s in scripts)
    for fact in facts:
        from urllib.parse import urlsplit
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
    valid, errors = [], []
    for attempt in range(3):
        if evidence_hashes(run, flow) != baseline:
            raise ValueError('Immutable source evidence changed during candidate correction')
        valid, errors = [], []
        for c in data['candidates']:
            try:
                validate_candidate(c, run, root)
                valid.append(c)
            except (ValueError, KeyError, TypeError, OSError, AttributeError) as exc:
                errors.append({'candidate_id': c['id'], 'code': 'INVALID_SOURCE_PROVENANCE',
                               'message': str(exc), 'candidate': c})
        if not errors or attempt == 2:
            break
        feedback = {'attempt': attempt + 1, 'max_attempts': 2, 'errors': errors}
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
            data = proposal
            (audit / f'proposal-{attempt+1}.json').write_text(json.dumps(proposal, indent=2))
        except (ValueError, KeyError, TypeError, asyncio.TimeoutError) as exc:
            progress('Candidate correction rejected: ' + (str(exc) or type(exc).__name__))
    allowed = sorted({s for c in valid for s in c['scripts']})
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
    if not valid:
        raise ValueError('Cross-flow could not be checked: ' + '; '.join(e['candidate_id'] + ': ' + e['message'] for e in errors))
    path.write_text(json.dumps({'schema_version': 1, 'candidates': valid}, indent=2))
    return len(valid)
