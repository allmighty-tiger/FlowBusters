"""Backend-authenticated application identity for requirement scoping.

An origin, run name, report field, or agent claim is never an application
identity.  New runs receive a locally signed manifest only after an explicit
application id matches the backend-owned application registry.
"""
from datetime import datetime, timezone
import hashlib
import hmac
import json
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from backend.runtime.probe_executor import canonical, key_for


REGISTRY_PATH = Path(__file__).with_name('applications.json')
MANIFEST_NAME = 'application_identity.json'


class ApplicationIdentityError(ValueError):
    pass


def _require(condition, message):
    if not condition:
        raise ApplicationIdentityError(message)


def _origin(value):
    parsed = urlsplit(str(value or ''))
    _require(parsed.scheme in ('http', 'https') and parsed.hostname,
             'Application target origin is invalid')
    port = f':{parsed.port}' if parsed.port is not None else ''
    return f'{parsed.scheme.lower()}://{parsed.hostname.lower()}{port}'


def load_applications(path=REGISTRY_PATH):
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError) as exc:
        raise ApplicationIdentityError(f'Cannot load application registry: {exc}') from exc
    _require(isinstance(data, dict) and data.get('schema_version') == 1,
             'Application registry schema_version must be 1')
    records, seen = {}, set()
    for record in data.get('applications', []):
        _require(isinstance(record, dict), 'Application registry entry must be an object')
        app_id = record.get('application_id')
        version = record.get('identity_version')
        _require(isinstance(app_id, str) and app_id and app_id not in seen,
                 'Application ids must be non-empty and unique')
        _require(isinstance(version, str) and version.strip(),
                 f'Application {app_id} needs identity_version')
        _require(isinstance(record.get('product'), str) and record['product'].strip(),
                 f'Application {app_id} needs product')
        _require(isinstance(record.get('requirements_version'), str)
                 and record['requirements_version'].strip(),
                 f'Application {app_id} needs requirements_version')
        endpoint = record.get('identity_endpoint')
        _require(isinstance(endpoint, str) and endpoint.startswith('/')
                 and not endpoint.startswith('//') and '?' not in endpoint
                 and '#' not in endpoint,
                 f'Application {app_id} needs a safe identity_endpoint')
        origins = record.get('allowed_origins')
        _require(isinstance(origins, list) and origins,
                 f'Application {app_id} needs allowed_origins')
        seen.add(app_id)
        records[app_id] = {**record, 'allowed_origins': [_origin(item) for item in origins]}
    return records


def issue_identity(run_dir, root, target_url, trusted_selection, registry_path=REGISTRY_PATH):
    """Issue one backend-signed identity manifest for a newly created run."""
    if trusted_selection is None:
        return None
    apps = load_applications(registry_path)
    application_id = trusted_selection.get('application_id') if isinstance(trusted_selection, dict) else None
    record = apps.get(application_id)
    _require(record is not None, 'Unknown backend-controlled application identity')
    for key in ('application_id', 'identity_version', 'requirements_version', 'product'):
        _require(trusted_selection.get(key) == record.get(key),
                 'Application identity selection does not match the backend registry')
    target_origin = _origin(target_url)
    _require(target_origin in record['allowed_origins'],
             'Application identity is not authorized for this target origin')
    payload = {
        'schema_version': 1,
        'application_id': application_id,
        'identity_version': record['identity_version'],
        'product': record['product'],
        'requirements_version': record['requirements_version'],
        'target_origin': target_origin,
        'issued_at': datetime.now(timezone.utc).isoformat(),
        'issuer': 'flowbusters_backend',
    }
    payload['signature'] = hmac.new(key_for(root, create=True), canonical(payload),
                                    hashlib.sha256).hexdigest()
    path = Path(run_dir) / MANIFEST_NAME
    with path.open('x', encoding='utf-8') as stream:
        json.dump(payload, stream, indent=2)
    return payload


def validate_identity(run_dir, root, registry_path=REGISTRY_PATH):
    """Return a trusted identity or ``None`` for an unbound legacy run."""
    path = Path(run_dir) / MANIFEST_NAME
    if not path.exists():
        return None
    try:
        manifest = json.loads(path.read_text(encoding='utf-8'))
        signature = manifest.pop('signature')
        expected = hmac.new(key_for(root), canonical(manifest), hashlib.sha256).hexdigest()
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise ApplicationIdentityError(f'Application identity manifest is invalid: {exc}') from exc
    _require(hmac.compare_digest(signature, expected),
             'Application identity manifest signature is invalid')
    apps = load_applications(registry_path)
    record = apps.get(manifest.get('application_id'))
    _require(record is not None, 'Application identity is no longer registered')
    _require(manifest.get('schema_version') == 1
             and manifest.get('identity_version') == record['identity_version']
             and manifest.get('requirements_version') == record['requirements_version']
             and manifest.get('product') == record['product']
             and manifest.get('issuer') == 'flowbusters_backend',
             'Application identity manifest does not match the backend registry')
    _require(_origin(manifest.get('target_origin')) in record['allowed_origins'],
             'Application identity origin is not registered')
    return {**manifest, 'signature': signature}


async def discover_application_identity(target_url, registry_path=REGISTRY_PATH,
                                        transport=None):
    """Identify a controlled local demo before a run directory is created.

    The endpoint is a deterministic application handshake, not cryptographic
    process attestation.  Failures and mismatches return ``None`` so the run is
    created unbound and cannot inherit backend-controlled product rules.
    """
    try:
        target_origin = _origin(target_url)
        candidates = [record for record in load_applications(registry_path).values()
                      if target_origin in record['allowed_origins']]
    except ApplicationIdentityError:
        return None
    matches = []
    async with httpx.AsyncClient(transport=transport, timeout=2.0,
                                 follow_redirects=False) as client:
        for record in candidates:
            try:
                response = await client.get(target_origin + record['identity_endpoint'],
                                            headers={'Accept': 'application/json'})
                payload = response.json() if response.status_code == 200 else None
            except (httpx.HTTPError, ValueError, TypeError):
                continue
            if (isinstance(payload, dict)
                    and set(payload) == {'application_id', 'identity_version'}
                    and payload['application_id'] == record['application_id']
                    and payload['identity_version'] == record['identity_version']):
                matches.append(record)
    if len(matches) != 1:
        return None
    return {key: matches[0][key] for key in (
        'application_id', 'identity_version', 'requirements_version', 'product')}
