"""Authenticated endpoint inventory for generation and transport admission.

UI labels are hypotheses about actions, never evidence of a route name.
"""
import ast
import hashlib
import hmac
import json
from pathlib import Path
from urllib.parse import urlsplit


def issue_setup_authorization(run, root, origin, paths):
    """Persist explicit CLI operator permission separately from copied evidence."""
    from backend.runtime.probe_executor import canonical, key_for
    for path in paths:
        if (not isinstance(path, str) or not path.startswith('/') or path.startswith('//')
                or urlsplit(path).query or urlsplit(path).fragment):
            raise ValueError('Setup authorization requires exact root paths')
    data = {'schema_version': 1, 'run_id': Path(run).name, 'origin': origin,
            'setup_paths': list(dict.fromkeys(paths))}
    data['signature'] = hmac.new(key_for(root), canonical(data), hashlib.sha256).hexdigest()
    with (Path(run) / 'setup_authorization.json').open('x', encoding='utf-8') as stream:
        json.dump(data, stream, indent=2)


def load_setup_authorization(run, root):
    from backend.runtime.probe_executor import canonical, key_for
    path = Path(run) / 'setup_authorization.json'
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding='utf-8'))
    signature = data.pop('signature', None)
    expected = hmac.new(key_for(root), canonical(data), hashlib.sha256).hexdigest()
    if (not isinstance(signature, str) or not hmac.compare_digest(signature, expected)
            or data.get('schema_version') != 1 or data.get('run_id') != Path(run).name):
        raise ValueError('Setup authorization authentication failed')
    return data


def validate_recorded_input(run, flow, root):
    from backend.runtime.reanalyze import validate_recording_source, validate_reanalysis_copy
    manifest = Path(run) / 'recording_source.json'
    if manifest.exists():
        source = json.loads(manifest.read_text(encoding='utf-8'))['source_run']
        inputs = validate_recording_source(root, source)
        validate_reanalysis_copy(run, flow, inputs, root)
    else:
        inputs = validate_recording_source(root, flow)
    return inputs


def prepare_endpoint_catalog(run, flow, root):
    from backend.runtime.probe_executor import canonical, key_for
    run = Path(run)
    entries = []
    if (run / 'cross_flow_inputs.json').exists():
        from backend.runtime.ui_provenance import validate_cross_flow_manifest, _verify_cross_flow_snapshot
        manifest = validate_cross_flow_manifest(run, root=root)
        for source in manifest['sources']:
            folder = run / source['path']
            _verify_cross_flow_snapshot(run, source['run_id'], folder, root=root)
            entries.extend(json.loads((folder / 'recording.har').read_text(encoding='utf-8'))['log']['entries'])
    else:
        validate_recorded_input(run, flow, root)
        entries = json.loads((run / 'flows' / flow / 'recording.har').read_text(encoding='utf-8'))['log']['entries']
    endpoints = {}
    def add(method, url, authority):
        parsed = urlsplit(url)
        if parsed.scheme not in ('http', 'https') or not parsed.netloc:
            raise ValueError('Endpoint catalog needs absolute HTTP URLs')
        key = (method.upper(), f'{parsed.scheme}://{parsed.netloc}', parsed.path or '/')
        endpoints[key] = {'method': key[0], 'origin': key[1], 'path': key[2], 'authority': authority}
    for entry in entries:
        if entry['response'].get('status') not in (404, 405):
            add(entry['request']['method'], entry['request']['url'], 'validated_recording')
    # Setup is operator configuration, never an endpoint inferred by the agent.
    scope = json.loads((run / 'scope.json').read_text(encoding='utf-8'))
    origins = {row['origin'] for row in endpoints.values()}
    authorization = load_setup_authorization(run, root)
    if authorization:
        if authorization['origin'] not in origins:
            raise ValueError('Setup authorization target contradicts recording')
        for path in authorization['setup_paths']:
            add('POST', authorization['origin'] + path, 'configured_setup')
    for path in scope.get('setup_paths', []):
        if (not isinstance(path, str) or not path.startswith('/')
                or path.startswith('//') or urlsplit(path).query or urlsplit(path).fragment):
            raise ValueError('Configured setup_paths must be exact root paths')
        for target in origins:
            add('POST', target + path, 'configured_setup')
    workflows = {}
    catalog = {'schema_version': 1, 'flow_name': flow,
               'endpoints': [endpoints[key] for key in sorted(endpoints)], 'workflows': workflows}
    catalog['signature'] = hmac.new(key_for(root, create=True), canonical(catalog), hashlib.sha256).hexdigest()
    path = run / 'endpoint_catalog.json'
    if path.exists():
        if json.loads(path.read_text(encoding='utf-8')) != catalog:
            raise ValueError('Endpoint catalog changed after recording validation')
    else:
        with path.open('x', encoding='utf-8') as stream:
            json.dump(catalog, stream, indent=2)
    return catalog


def load_endpoint_catalog(run, root):
    from backend.runtime.probe_executor import canonical, key_for
    path = Path(run) / 'endpoint_catalog.json'
    if not path.exists():
        return None  # Existing evidence has no retroactively invented inventory.
    data = json.loads(path.read_text(encoding='utf-8'))
    signature = data.pop('signature', None)
    expected = hmac.new(key_for(root), canonical(data), hashlib.sha256).hexdigest()
    if not isinstance(signature, str) or not hmac.compare_digest(signature, expected):
        raise ValueError('Endpoint catalog authentication failed')
    if data.get('schema_version') != 1 or data.get('flow_name') != Path(run).name:
        raise ValueError('Endpoint catalog scope/version mismatch')
    return data


def endpoint_key(method, url):
    parsed = urlsplit(url)
    return method.upper(), f'{parsed.scheme}://{parsed.netloc}', parsed.path or '/'


def preflight_endpoints(script, catalog):
    """Catch constant/f-string HTTP targets; runtime admission covers dynamic ones."""
    if catalog is None:
        return None
    allowed = {(row['method'], row['origin'], row['path']) for row in catalog['endpoints']}
    from backend.runtime.generated_probes import read_plan
    try:
        plan = read_plan(script)
    except (ValueError, SyntaxError, TypeError, KeyError, OSError) as exc:
        return f'Probe plan validation failed: {exc}'
    if plan is not None:
        setup = plan.get('setup')
        if setup and not any(row['method'] == 'POST' and row['origin'] == plan['origin']
                             and row['path'] == setup and row['authority'] == 'configured_setup'
                             for row in catalog['endpoints']):
            return f'Unauthorized setup: POST {setup}; select an explicitly configured setup endpoint.'
        requests = [('GET', plan['state_read'])]
        requests += [(a['method'], a['path']) for a in plan['actions']]
        for method, path in requests:
            if endpoint_key(method, plan['origin'] + path) not in allowed:
                return f'Endpoint selection error: {method} {path} is absent from the authenticated endpoint catalog.'
        return None
    tree = ast.parse(Path(script).read_text(encoding='utf-8'))
    names = {}
    def value(node):
        if isinstance(node, ast.Constant): return node.value
        if isinstance(node, ast.Name): return names.get(node.id)
        if isinstance(node, ast.JoinedStr):
            pieces = [value(part.value) if isinstance(part, ast.FormattedValue) else value(part) for part in node.values]
            if all(isinstance(part, str) for part in pieces): return ''.join(pieces)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left, right = value(node.left), value(node.right)
            if isinstance(left, str) and isinstance(right, str): return left + right
        return None
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name): names[target.id] = value(node.value)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute): continue
        receiver = node.func.value
        if not isinstance(receiver, ast.Name) or receiver.id not in ('client', 'session', 'httpx', 'requests'): continue
        method = node.func.attr.upper()
        if method not in ('GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS', 'REQUEST'): continue
        args = [value(arg) for arg in node.args]
        if method == 'REQUEST':
            method, url = (args + [None, None])[:2]
        else:
            url = args[0] if args else next((value(k.value) for k in node.keywords if k.arg == 'url'), None)
        if isinstance(method, str) and isinstance(url, str) and urlsplit(url).scheme:
            if endpoint_key(method, url) not in allowed:
                return (f'Endpoint selection error: {method} {url} is absent from the authenticated endpoint catalog. '
                        'Use a recorded or backend-documented method/path; UI labels do not establish routes.')
    return None
