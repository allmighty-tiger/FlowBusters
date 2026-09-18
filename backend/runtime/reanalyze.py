"""Re-run a single-flow analysis from backend-validated recording evidence."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import json
import os
import re
import shutil
from datetime import datetime
import sys
from pathlib import Path
from urllib.parse import urlsplit

from backend.runtime.recorder import RecordingError, check_scope

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


_RUN_ID = re.compile(r'[A-Za-z0-9_-]+')
_COPIED_ARTIFACTS = ('demo.json', 'recording.har')


class ReanalysisError(ValueError):
    """A source recording or isolated destination violates the contract."""


def _require(condition, message):
    if not condition:
        raise ReanalysisError(message)


def _read_json(path, label):
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReanalysisError(f'{label} is missing or invalid JSON: {exc}') from exc
    _require(isinstance(value, dict), f'{label} must contain an object')
    return value


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _origin(url):
    parsed = urlsplit(url)
    _require(parsed.scheme in ('http', 'https') and bool(parsed.hostname),
             f'Invalid recording target URL: {url!r}')
    hostname = parsed.hostname.lower()
    hostname = f'[{hostname}]' if ':' in hostname else hostname
    port = parsed.port
    suffix = f':{port}' if port and port != (443 if parsed.scheme == 'https' else 80) else ''
    return f'{parsed.scheme}://{hostname}{suffix}'


def _safe_run(root, run_id, *, must_exist):
    _require(isinstance(run_id, str) and bool(_RUN_ID.fullmatch(run_id)),
             f'Invalid run ID: {run_id!r}')
    runs = (Path(root).resolve() / 'runs')
    run = runs / run_id
    _require(run.resolve().parent == runs.resolve(), 'Run path escapes the artifact root')
    _require(not run.is_symlink(), 'Run directory may not be a symlink')
    if must_exist:
        _require(run.is_dir(), f'Source run does not exist: {run_id}')
    else:
        _require(not run.exists(), f'Destination run already exists: {run_id}')
    return run


def _artifact(run, relative):
    path = run.joinpath(*relative.split('/'))
    _require(path.resolve().is_relative_to(run.resolve()),
             f'Source artifact escapes its run: {relative}')
    _require(path.is_file() and not path.is_symlink(),
             f'Source artifact is missing or unsafe: {relative}')
    return path


def validate_recording_source(root, source_run):
    """Read-only validation of one ordinary backend-recorded source run."""
    source = _safe_run(root, source_run, must_exist=True)
    _require(not (source / 'cross_flow_inputs.json').exists(),
             'Reanalysis requires an ordinary recorded-flow run, not a cross-flow run')

    relative_paths = {
        'demo.json': f'flows/{source_run}/demo.json',
        'recording.har': f'flows/{source_run}/recording.har',
        'scope.json': 'scope.json',
        'recording_validated.marker': 'recording_validated.marker',
        'capture_manifest.json': f'flows/{source_run}/har_data/capture_manifest.json',
    }
    paths = {name: _artifact(source, relative) for name, relative in relative_paths.items()}
    demo = _read_json(paths['demo.json'], 'demo.json')
    har = _read_json(paths['recording.har'], 'recording.har')
    scope = _read_json(paths['scope.json'], 'scope.json')
    capture = _read_json(paths['capture_manifest.json'], 'capture_manifest.json')

    _require(demo.get('schema_version') == 2, 'demo.json schema_version must be 2')
    _require(demo.get('source') == 'backend-mcp-recorder',
             'demo.json was not produced by the backend MCP recorder')
    _require(demo.get('flow_name') == source_run,
             'demo.json flow_name does not match the source run ID')
    _require(demo.get('mode') != 'cross_flow', 'Cross-flow evidence cannot be reanalyzed as one flow')
    _require(all(key in demo for key in (
        'timestamp_start', 'timestamp_end', 'initial_snapshot', 'final_snapshot', 'warnings')),
        'demo.json is missing recorder lifecycle fields')
    _require(isinstance(demo.get('warnings'), list), 'demo.json warnings must be an array')
    target_url = demo.get('target_url')
    target_origin = _origin(target_url)
    try:
        check_scope(source, target_url)
    except (RecordingError, OSError, ValueError, json.JSONDecodeError) as exc:
        raise ReanalysisError(f'Source scope does not authorize its recorded target: {exc}') from exc
    _require(isinstance(scope.get('allowed_domains'), list)
             and isinstance(scope.get('allowed_paths_prefix'), list),
             'scope.json needs allowed_domains and allowed_paths_prefix arrays')

    log = har.get('log')
    _require(isinstance(log, dict) and log.get('version') == '1.2',
             'recording.har must be HAR 1.2')
    _require(isinstance(log.get('creator'), dict), 'recording.har needs log.creator')
    entries = log.get('entries')
    _require(isinstance(entries, list) and entries, 'recording.har needs at least one entry')
    for index, entry in enumerate(entries):
        _require(isinstance(entry, dict), f'HAR entry {index} must be an object')
        request = entry.get('request')
        _require(isinstance(request, dict) and isinstance(request.get('url'), str),
                 f'HAR entry {index} needs a request URL')
        _require(_origin(request['url']) == target_origin,
                 f'HAR entry {index} has a different target origin')
        _require(isinstance(entry.get('response'), dict),
                 f'HAR entry {index} needs a response object')

    try:
        marker_count = int(paths['recording_validated.marker'].read_text(encoding='utf-8').strip())
    except (OSError, ValueError) as exc:
        raise ReanalysisError('recording_validated.marker must contain an integer') from exc
    request_count = demo.get('request_count')
    timeline = demo.get('workflow_timeline')
    _require(isinstance(timeline, dict), 'demo.json needs workflow_timeline')
    ui_states = timeline.get('ui_states')
    network_sequence = timeline.get('network_sequence')
    _require(isinstance(ui_states, list) and ui_states,
             'demo.json needs at least one sampled semantic UI state')
    _require(all(isinstance(state, dict) and type(state.get('step')) is int
                 and isinstance(state.get('elements'), list) for state in ui_states),
             'demo.json contains a malformed semantic UI state')
    _require(isinstance(network_sequence, list),
             'demo.json workflow_timeline.network_sequence must be an array')
    _require(type(request_count) is int and request_count > 0,
             'demo.json request_count must be a positive integer')
    _require(marker_count == request_count == len(entries) == len(network_sequence),
             'Validated marker, demo request count, network sequence, and HAR entry count disagree')

    request_ids = capture.get('request_ids')
    missing = capture.get('missing')
    unrecoverable = capture.get('unrecoverable_response_bodies')
    _require(isinstance(request_ids, list) and len(request_ids) == len(set(request_ids))
             and len(request_ids) == marker_count,
             'capture_manifest request IDs do not match the validated request count')
    _require(isinstance(missing, list) and isinstance(unrecoverable, list),
             'capture_manifest missing/unrecoverable fields must be arrays')
    _require(all(isinstance(item, dict) and item.get('part') == 'response-body'
                 for item in missing),
             'capture_manifest contains an incomplete request detail or request body')
    _require(all(type(index) is int and index in request_ids for index in unrecoverable),
             'capture_manifest has invalid unrecoverable response indexes')
    _require(len(unrecoverable) <= len(request_ids) / 2,
             'Too many response bodies were unrecoverable for supported reuse')
    _require({item.get('index') for item in missing} == set(unrecoverable),
             'capture_manifest missing responses and unrecoverable indexes disagree')

    artifacts = {}
    for name, path in paths.items():
        artifacts[name] = {
            'source_relative_path': relative_paths[name],
            'sha256': _sha256(path),
            'size': path.stat().st_size,
        }
    result = {
        'schema_version': 1,
        'source_run': source_run,
        'source_run_path': str(source.resolve()),
        'target_url': target_url,
        'target_origin': target_origin,
        'request_count': request_count,
        'ui_state_count': len(ui_states),
        'artifacts': artifacts,
    }
    # Reanalysis may inherit only a backend-authenticated source identity.
    # Legacy source names, URLs and agent reports cannot create this field.
    from backend.runtime.application_identity import validate_identity
    identity = validate_identity(source, root)
    if identity:
        result['application_identity'] = {
            key: identity[key] for key in ('application_id', 'identity_version', 'requirements_version', 'product')
        }
    return result


def validated_recording_metadata(root, source_run):
    """Read-only display metadata from a validated recorder artifact, not a draft."""
    recording = validate_recording_source(root, source_run)
    demo_path = Path(recording['source_run_path']) / recording['artifacts']['demo.json']['source_relative_path']
    demo = _read_json(demo_path, 'demo.json')
    timestamp = demo.get('timestamp_end')
    _require(isinstance(timestamp, str), 'Recording end timestamp must be an ISO date')
    try:
        parsed = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
    except ValueError as exc:
        raise ReanalysisError('Recording end timestamp must be an ISO date') from exc
    _require(parsed.tzinfo is not None, 'Recording end timestamp must include a timezone')
    return {'flow_name': source_run, 'target_url': recording['target_url'],
            'run_timestamp': timestamp, 'source': 'validated_recording'}


def validate_destination(root, source_run, destination_run):
    _require(source_run != destination_run, 'Source and destination run IDs must differ')
    return _safe_run(root, destination_run, must_exist=False)


def _source_path(inputs, name):
    source = Path(inputs['source_run_path']).resolve()
    path = source.joinpath(*inputs['artifacts'][name]['source_relative_path'].split('/'))
    _require(path.resolve().is_relative_to(source), 'Recorded source path escapes its run')
    return path


def _verify_source_hashes(inputs):
    for name, metadata in inputs.get('artifacts', {}).items():
        path = _source_path(inputs, name)
        _require(path.is_file() and not path.is_symlink(), f'Source artifact disappeared: {name}')
        _require(path.stat().st_size == metadata.get('size')
                 and hmac.compare_digest(_sha256(path), metadata.get('sha256', '')),
                 f'Source artifact changed after validation: {name}')


def _unsigned_manifest(inputs, destination_run):
    copied = {}
    for name in _COPIED_ARTIFACTS:
        copied[name] = {
            'source_relative_path': inputs['artifacts'][name]['source_relative_path'],
            'destination_relative_path': f'flows/{destination_run}/{name}',
            'sha256': inputs['artifacts'][name]['sha256'],
            'size': inputs['artifacts'][name]['size'],
        }
    supporting = {
        name: dict(inputs['artifacts'][name])
        for name in ('scope.json', 'recording_validated.marker', 'capture_manifest.json')
    }
    return {
        'schema_version': 1,
        'mode': 'single_flow_reanalysis',
        'source_run': inputs['source_run'],
        'destination_run': destination_run,
        'target_url': inputs['target_url'],
        'target_origin': inputs['target_origin'],
        'request_count': inputs['request_count'],
        'ui_state_count': inputs['ui_state_count'],
        'copied_artifacts': copied,
        'validated_source_artifacts': supporting,
    }


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def prepare_reanalysis_inputs(destination, destination_run, inputs, root):
    """Copy only validated recording inputs and write an authenticated manifest."""
    destination = Path(destination).resolve()
    _require(destination.name == destination_run, 'Destination directory and run ID disagree')
    _verify_source_hashes(inputs)
    output = destination / 'flows' / destination_run
    output.mkdir(parents=True, exist_ok=True)
    for name in _COPIED_ARTIFACTS:
        shutil.copy2(_source_path(inputs, name), output / name)
    shutil.copy2(_source_path(inputs, 'scope.json'), destination / 'scope.json')
    shutil.copy2(_source_path(inputs, 'recording_validated.marker'),
                 destination / 'recording_validated.marker')

    unsigned = _unsigned_manifest(inputs, destination_run)
    for name, metadata in unsigned['copied_artifacts'].items():
        copied_path = destination.joinpath(*metadata['destination_relative_path'].split('/'))
        _require(not copied_path.samefile(_source_path(inputs, name)),
                 f'Destination artifact is not an independent copy: {name}')
        _require(copied_path.stat().st_size == metadata['size']
                 and hmac.compare_digest(_sha256(copied_path), metadata['sha256']),
                 f'Copied recording artifact differs from its source: {name}')
    from backend.runtime.probe_executor import key_for
    signature = hmac.new(key_for(Path(root).resolve(), create=True),
                         _canonical(unsigned), hashlib.sha256).hexdigest()
    (destination / 'recording_source.json').write_text(
        json.dumps({**unsigned, 'signature': signature}, indent=2), encoding='utf-8')
    validate_reanalysis_copy(destination, destination_run, inputs, root)


def validate_reanalysis_copy(destination, destination_run, inputs, root):
    """Recheck in-memory source hashes, copied bytes, scope, and manifest HMAC."""
    destination = Path(destination).resolve()
    _verify_source_hashes(inputs)
    manifest = _read_json(destination / 'recording_source.json', 'recording_source.json')
    signature = manifest.pop('signature', None)
    expected = _unsigned_manifest(inputs, destination_run)
    _require(manifest == expected, 'recording_source.json contradicts the validated source')
    from backend.runtime.probe_executor import key_for
    expected_signature = hmac.new(key_for(Path(root).resolve()),
                                  _canonical(expected), hashlib.sha256).hexdigest()
    _require(isinstance(signature, str)
             and hmac.compare_digest(signature, expected_signature),
             'recording_source.json signature is invalid')
    for name, metadata in expected['copied_artifacts'].items():
        path = destination.joinpath(*metadata['destination_relative_path'].split('/'))
        _require(path.is_file() and not path.is_symlink(),
                 f'Copied recording artifact is missing or unsafe: {name}')
        _require(path.stat().st_size == metadata['size']
                 and hmac.compare_digest(_sha256(path), metadata['sha256']),
                 f'Copied recording artifact changed: {name}')
    _require(hmac.compare_digest(_sha256(destination / 'scope.json'),
                                 inputs['artifacts']['scope.json']['sha256']),
             'Destination scope differs from the validated source scope')
    try:
        check_scope(destination, inputs['target_url'])
    except (RecordingError, OSError, ValueError, json.JSONDecodeError) as exc:
        raise ReanalysisError(f'Destination scope no longer authorizes the target: {exc}') from exc
    return manifest


def _parser():
    parser = argparse.ArgumentParser(
        description='Reanalyze one validated FlowBusters recording without opening a browser.')
    parser.add_argument('--source', required=True, help='Validated ordinary source run ID')
    parser.add_argument('--destination', required=True, help='New isolated destination run ID')
    parser.add_argument('--artifacts-dir', default='.', help='Artifact root containing runs/ (default: .)')
    return parser


def _configure_console_output():
    """Keep progress Unicode from aborting the Windows CLI code path."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, 'reconfigure', None)
        if callable(reconfigure):
            try:
                reconfigure(errors='backslashreplace')
            except (OSError, ValueError):
                pass


async def _run(args):
    root = Path(args.artifacts_dir).resolve()
    inputs = validate_recording_source(root, args.source)
    validate_destination(root, args.source, args.destination)
    from backend.runtime.orchestrator import run_flowbusters

    def progress(event):
        print(f'[{event.run_mode.value}] {event.message}', flush=True)

    result = await run_flowbusters(
        target_url=inputs['target_url'],
        flow_name=args.destination,
        run_dir=str(root),
        anthropic_api_key=os.environ.get('ANTHROPIC_API_KEY', ''),
        anthropic_model=os.environ.get('ANTHROPIC_MODEL', 'claude-sonnet-5'),
        progress_cb=progress,
        reanalysis_inputs=inputs,
        application_identity=inputs.get('application_identity'),
    )
    if result.get('error'):
        print(result['error'], file=sys.stderr)
        return 1
    print(f'Reanalysis complete: runs/{args.destination}', flush=True)
    return 0


def main(argv=None):
    _configure_console_output()
    try:
        return asyncio.run(_run(_parser().parse_args(argv)))
    except (ReanalysisError, OSError, ValueError) as exc:
        print(f'Reanalysis rejected: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
