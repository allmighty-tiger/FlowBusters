"""Backend-owned stdio MCP recording; independent of model turn lifetime."""
import asyncio
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit


class RecordingError(RuntimeError):
    pass


class DialogWaitTimeout(RecordingError):
    pass


def error_detail(value):
    """Keep useful MCP diagnostics, omitting commonly embedded credentials."""
    text = json.dumps(value, ensure_ascii=False)
    for key, secret in os.environ.items():
        if any(word in key.upper() for word in ('KEY', 'TOKEN', 'SECRET', 'PASSWORD')) and len(secret) >= 8:
            text = text.replace(secret, '[REDACTED]')
    text = re.sub(r'(?i)(bearer\s+)[^\s"\\]+', r'\1[REDACTED]', text)
    text = re.sub(r'(?i)((?:password|token|api[_-]?key|secret)=)[^&\s"\\]+',
                  r'\1[REDACTED]', text)
    return text[:2000] or 'No error detail returned'


async def optional_snapshot(client, warnings, label, notify):
    try:
        return await asyncio.wait_for(client.call('browser_snapshot', {}), timeout=10)
    except (RecordingError, TimeoutError, OSError) as exc:
        detail = str(exc) or type(exc).__name__
        warning = f'{label} snapshot unavailable: {detail}'
        warnings.append(warning)
        notify(warning)
        return None


_SENSITIVE_NAMES = re.compile(
    r'(?i)(password|passcode|passwd|secret|token|api[_ -]?key|authorization|bearer|csrf)'
)
_SNAPSHOT_META = re.compile(r'^\s*(?:#+\s*)?(?:page state|page snapshot)\s*:?[\s`]*$', re.I)
_EMPTY_CONTAINER = re.compile(
    r'^(?:generic|group|list|main|navigation|region|form|document)(?:\s+\[[^]]*\])?\s*:?$',
    re.I,
)


def _utc_now():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def _result_texts(value):
    """Yield text blocks from an MCP result without retaining images/metadata."""
    if isinstance(value, str):
        yield value
        return
    if isinstance(value, list):
        for item in value:
            yield from _result_texts(item)
        return
    if not isinstance(value, dict):
        return
    if value.get('type') == 'text' and isinstance(value.get('text'), str):
        yield value['text']
        return
    for key in ('content', 'structuredContent'):
        if key in value:
            yield from _result_texts(value[key])


def _safe_url(value):
    """Keep routing context while removing credentials from query parameters."""
    try:
        parsed = urlsplit(value)
    except (TypeError, ValueError):
        return value
    if not parsed.scheme or not parsed.netloc:
        return value
    query = parse_qs(parsed.query, keep_blank_values=True)
    for key in list(query):
        if _SENSITIVE_NAMES.search(key):
            query[key] = ['[REDACTED]']
    fragment = parsed.fragment
    fragment_query = parse_qs(fragment, keep_blank_values=True)
    if fragment_query:
        for key in list(fragment_query):
            if _SENSITIVE_NAMES.search(key):
                fragment_query[key] = ['[REDACTED]']
        fragment = urlencode(fragment_query, doseq=True)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path,
                       urlencode(query, doseq=True), fragment))


def _redact_snapshot_line(line):
    line = re.sub(r'(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+', r'\1[REDACTED]', line)
    line = re.sub(
        r'(?i)((?:password|passcode|passwd|secret|token|api[_ -]?key|csrf)\s*[=:]\s*)\S+',
        r'\1[REDACTED]',
        line,
    )
    # Accessibility snapshots may render an input as: textbox "Password": value.
    if _SENSITIVE_NAMES.search(line) and ':' in line:
        prefix, _ = line.split(':', 1)
        line = prefix + ': [REDACTED]'
    return line


def compact_snapshot(snapshot, max_elements=80, max_chars=6000):
    """Convert Playwright's accessibility snapshot into bounded semantic UI context.

    The model needs labels, visible rules and control state, not MCP envelopes,
    element refs or a full DOM dump. The output is deliberately lossy and bounded.
    """
    if not snapshot:
        return None
    texts = list(_result_texts(snapshot))
    if not texts:
        return None
    page_url = None
    page_title = None
    elements = []
    seen = set()
    used = 0
    for raw in texts:
        for original in raw.splitlines():
            line = original.strip().strip('`').strip()
            if not line or _SNAPSHOT_META.match(line):
                continue
            url_match = re.match(r'^-?\s*Page URL:\s*(.+)$', line, re.I)
            if url_match:
                page_url = _safe_url(url_match.group(1).strip())
                continue
            title_match = re.match(r'^-?\s*Page Title:\s*(.+)$', line, re.I)
            if title_match:
                page_title = title_match.group(1).strip()[:300]
                continue
            line = re.sub(r'^[-*]\s*', '', line)
            line = re.sub(r'\s*\[ref=[^]]+]\s*', ' ', line, flags=re.I)
            line = re.sub(r'\s+', ' ', line).strip()
            if not line or _EMPTY_CONTAINER.match(line):
                continue
            line = _redact_snapshot_line(line)[:300]
            if line in seen:
                continue
            if used + len(line) > max_chars or len(elements) >= max_elements:
                break
            seen.add(line)
            elements.append(line)
            used += len(line)
        if used >= max_chars or len(elements) >= max_elements:
            break
    if not page_url and not page_title and not elements:
        return None
    return {'url': page_url, 'title': page_title, 'elements': elements}


def add_ui_state(timeline, snapshot, phase, captured_at=None):
    """Append only changed UI states and include a compact before/after delta."""
    compact = compact_snapshot(snapshot)
    if compact is None:
        return None
    captured_at = captured_at or _utc_now()
    signature = json.dumps(compact, sort_keys=True, ensure_ascii=False)
    if timeline and timeline[-1].get('_signature') == signature:
        timeline[-1]['last_observed_at'] = captured_at
        timeline[-1]['observations'] += 1
        if phase == 'final':
            timeline[-1]['is_final'] = True
        return timeline[-1]
    previous = timeline[-1] if timeline else None
    old = set(previous.get('elements', [])) if previous else set()
    new = set(compact['elements'])
    state = {
        'step': len(timeline) + 1,
        'phase': phase,
        'captured_at': captured_at,
        'last_observed_at': captured_at,
        'observations': 1,
        'is_final': phase == 'final',
        **compact,
        'changes_from_previous': {
            'appeared': [item for item in compact['elements'] if item not in old][:30],
            'disappeared': [item for item in (previous or {}).get('elements', []) if item not in new][:30],
        },
        '_signature': signature,
    }
    timeline.append(state)
    return state


def _json_keys(text):
    if not text:
        return []
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        try:
            return sorted(parse_qs(text, keep_blank_values=True).keys())[:50]
        except (TypeError, ValueError):
            return []
    if isinstance(value, dict):
        return sorted(str(key) for key in value.keys())[:50]
    if isinstance(value, list) and value and isinstance(value[0], dict):
        return sorted(str(key) for key in value[0].keys())[:50]
    return []


def network_sequence(entries):
    """Return a value-free network outline that can be read beside UI states."""
    sequence = []
    for index, entry in enumerate(entries, 1):
        request = entry.get('request', {})
        response = entry.get('response', {})
        post_data = request.get('postData', {}) if isinstance(request.get('postData'), dict) else {}
        content = response.get('content', {}) if isinstance(response.get('content'), dict) else {}
        sequence.append({
            'sequence': index,
            'method': request.get('method'),
            'url': _safe_url(request.get('url', '')),
            'status': response.get('status'),
            'request_body_keys': _json_keys(post_data.get('text', '')),
            'response_body_keys': _json_keys(content.get('text', '')),
        })
    return sequence


class McpClient:
    def __init__(self, process, notify=lambda message: None, dialog_timeout=120, poll_interval=2, max_dismissals=10):
        self.process = process
        self.serial = 0
        self.notify = notify
        self.dialog_timeout = dialog_timeout
        self.poll_interval = poll_interval
        self.max_dismissals = max_dismissals

    async def rpc(self, method, params=None):
        self.serial += 1
        ident = self.serial
        self.process.stdin.write((json.dumps({'jsonrpc': '2.0', 'id': ident,
            'method': method, 'params': params or {}}) + '\n').encode())
        await self.process.stdin.drain()
        async with asyncio.timeout(90):
            while True:
                line = await self.process.stdout.readline()
                if not line:
                    raise RecordingError('Playwright MCP exited during recording')
                message = json.loads(line)
                if message.get('id') != ident:
                    continue
                if 'error' in message:
                    raise RecordingError('MCP request failed: ' + method + ': ' + error_detail(message['error']))
                return message.get('result', {})

    async def call(self, name, arguments, recover_modal=True):
        # A native confirm()/alert() left open in the recording window blocks MCP's
        # capture reads behind its modal gate. We do not ACCEPT the dialog — that
        # would run the action (e.g. the Reset) mid-save. We dismiss it as Cancel,
        # which clears the gate without firing the action, then re-read. Bounded so
        # a dialog we cannot clear still times out rather than hanging.
        deadline = None
        dismissals = 0
        while True:
            result = await self.rpc('tools/call', {'name': name, 'arguments': arguments})
            if not result.get('isError'):
                if deadline is not None:
                    # internal recovery detail -> backend.log, not the user's UI
                    print('Dialog cleared — continuing to save the recording', file=sys.stderr)
                return result
            contents = result.get('content', [])
            modal = any(
                item.get('type') == 'text'
                and 'does not handle the modal state' in item.get('text', '')
                and 'browser_handle_dialog' in item.get('text', '')
                for item in contents if isinstance(item, dict))
            if not recover_modal or not modal or name not in {
                'browser_network_requests', 'browser_network_request', 'browser_snapshot'
            }:
                raise RecordingError('MCP tool failed: ' + name + ': ' + error_detail(contents))
            now = asyncio.get_running_loop().time()
            if deadline is None:
                deadline = now + self.dialog_timeout
            if now >= deadline:
                raise DialogWaitTimeout('Recording could not be saved: a browser dialog '
                    'remained open until the waiting time expired.')
            if dismissals >= self.max_dismissals:
                raise DialogWaitTimeout('Recording could not be saved: a browser dialog was '
                    f'still present after {dismissals} dismiss attempts. Close it in the '
                    'recording window and retry.')
            dismissals += 1
            try:
                await self.rpc('tools/call', {'name': 'browser_handle_dialog',
                    'arguments': {'accept': False}})
            except RecordingError:
                pass  # 'No dialog visible' / already handled — the next read decides
            # internal recovery detail -> backend.log, not the user's UI
            print('confirmation dialog dismissed as Cancel during capture (auto-recovery)',
                  file=sys.stderr)
            await asyncio.sleep(min(self.poll_interval, deadline - now))


def manifest_ids(text):
    ids = [int(x) for x in re.findall(r'^\s*#?(\+?\d+)[.)]?\s+\[(?:GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\]', text, re.M)]
    if not ids or len(ids) != len(set(ids)):
        raise RecordingError('Empty or unrecognized network manifest; capture cannot be validated')
    return ids


def check_scope(run_dir, target):
    scope_file = run_dir / 'scope.json'
    if not scope_file.exists():
        raise RecordingError('scope.json is required for backend recording')
    scope = json.loads(scope_file.read_text())
    url = urlsplit(target)
    if url.scheme not in ('http', 'https') or not url.hostname:
        raise RecordingError('Invalid target URL')
    allowed = scope.get('allowed_domains', [])
    origin = f'{url.scheme}://{url.netloc}'
    if not any(entry in (origin, url.hostname, url.netloc) for entry in allowed):
        raise RecordingError('Target is outside allowed_domains')
    if not any(prefix == '*' or url.path.startswith(prefix) for prefix in scope.get('allowed_paths_prefix', [])):
        raise RecordingError('Target is outside allowed_paths_prefix')
    if scope.get('block_production'):
        raise RecordingError('block_production is enabled; explicitly configure an approved non-production scope before recording')


# HTTP methods that never carry a request body. playwright's network detail
# omits the request-body hint for these, so a request-body read returns success
# with no file written; treating that as a missing capture would abort a valid
# recording. Skip them rather than read-and-fail.
_NO_REQUEST_BODY = frozenset({'GET', 'HEAD', 'OPTIONS', 'DELETE'})


def _request_methods(manifest_text):
    """Map each request index to its HTTP method, parsed from manifest lines."""
    methods = {}
    for line in manifest_text.splitlines():
        match = re.match(r'^\s*#?(\d+)[.)]?\s+\[([A-Z]+)\]', line)
        if match:
            methods[int(match.group(1))] = match.group(2)
    return methods


async def dump_capture(client, run_dir, flow, notify=lambda message: None):
    folder = run_dir / 'flows' / flow / 'har_data'
    folder.mkdir(parents=True, exist_ok=True)
    manifest = folder / 'network_requests.log'
    await client.call('browser_network_requests', {'static': False, 'filename': str(manifest)})
    if not manifest.exists():
        raise RecordingError('MCP did not save the network manifest')
    manifest_text = manifest.read_text()
    ids = manifest_ids(manifest_text)
    methods = _request_methods(manifest_text)
    failed = {}
    for index in ids:
        parts = [(None, '.log')]
        if methods.get(index, 'GET') not in _NO_REQUEST_BODY:
            parts.append(('request-body', '_request_body.txt'))
        parts.append(('response-body', '_response_body.txt'))
        for part, suffix in parts:
            destination = folder / f'request_{index:03d}{suffix}'
            args = {'index': index, 'filename': str(destination)}
            if part:
                args['part'] = part
            for attempt in range(2):
                try:
                    await client.call('browser_network_request', args)
                    if not destination.exists():
                        raise RecordingError('Requested capture file was not saved')
                    break
                except DialogWaitTimeout:
                    raise
                except RecordingError:
                    if attempt == 1:
                        failed[(index, part or 'details')] = {'index': index, 'part': part or 'details'}
    # A response body can detach from the network entry when the page navigates
    # away during capture (an SPA redirect right after login is the common case).
    # Give the entry a moment and re-read any still-missing response body before
    # giving up, so a one-off race does not abort an otherwise complete recording.
    for _ in range(3):
        missing = [i for (i, p) in failed if p == 'response-body'
                   and not (folder / f'request_{i:03d}_response_body.txt').exists()]
        if not missing:
            break
        await asyncio.sleep(1)
        for index in missing:
            destination = folder / f'request_{index:03d}_response_body.txt'
            try:
                await client.call('browser_network_request', {'index': index, 'part': 'response-body', 'filename': str(destination)})
            except DialogWaitTimeout:
                raise
            except RecordingError:
                continue
            if destination.exists():
                failed.pop((index, 'response-body'), None)
    fatal = [f for f in failed.values() if f['part'] != 'response-body']
    unrecoverable = sorted(i for (i, p) in failed if p == 'response-body')
    failures = [{'index': f['index'], 'part': f['part']} for f in sorted(failed.values(), key=lambda f: (f['index'], f['part']))]
    (folder / 'capture_manifest.json').write_text(json.dumps({'request_ids': ids, 'missing': failures, 'unrecoverable_response_bodies': unrecoverable}, indent=2))
    # A request body or the request details cannot be re-derived: missing either
    # breaks replay of the mutation, so it is always fatal.
    if fatal:
        raise RecordingError('Capture incomplete: missing request body or details; '
                             'see har_data/capture_manifest.json')
    if len(unrecoverable) > len(ids) / 2:
        raise RecordingError(f'Capture incomplete: {len(unrecoverable)}/{len(ids)} response bodies '
                             'are unrecoverable; the recording is not usable. See '
                             'har_data/capture_manifest.json')
    if unrecoverable:
        (folder / 'unrecoverable_response_bodies.marker').write_text(json.dumps({'indexes': unrecoverable}))
        # internal detail -> backend.log, not the user's UI
        print('Some response bodies could not be captured (detached by a page navigation, '
              'e.g. after login); the recording still includes the full request sequence '
              'and is usable.', file=sys.stderr)
    har = folder.parent / 'recording.har'
    for validation_attempt in range(2):
        builder = await asyncio.create_subprocess_exec(sys.executable,
            str(run_dir / 'crew/scripts/synthesize_har.py'), str(folder), str(har),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        _, stderr = await builder.communicate()
        if not builder.returncode:
            break
        if validation_attempt:
            (folder / 'validation_error.txt').write_bytes(stderr)
            raise RecordingError('HAR validation failed; see har_data/validation_error.txt')
        # A response may still have been pending when the first dump ran.
        # Retry only empty response captures, using the same live MCP session.
        for index in ids:
            path = folder / f'request_{index:03d}_response_body.txt'
            if path.stat().st_size == 0:
                await client.call('browser_network_request', {'index': index,
                    'part': 'response-body', 'filename': str(path)})
    entries = json.loads(har.read_text())['log']['entries']
    if len(entries) != len(ids):
        raise RecordingError('HAR entry count differs from manifest')
    return len(entries)


async def record(config, run_dir, mcp_path, env, notify):
    run_dir = Path(run_dir).resolve()
    check_scope(run_dir, config.target_url)
    if (run_dir / 'recording_done.marker').exists() or (run_dir / 'flows' / config.flow_name / 'recording.har').exists():
        raise RecordingError('Run directory already contains a finish marker; use a new flow name')
    settings = json.loads(Path(mcp_path).read_text()).get('mcpServers', {}).get('playwright', {})
    if not settings.get('command') or settings.get('url'):
        raise RecordingError('Backend recorder requires the configured playwright stdio MCP server')
    child_env = dict(env)
    child_env.pop('ANTHROPIC_API_KEY', None)
    child_env.update(settings.get('env', {}))
    proc = await asyncio.create_subprocess_exec(settings['command'], *settings.get('args', []),
        cwd=str(run_dir), env=child_env, stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        limit=64 * 1024 * 1024)
    client = McpClient(proc, notify)
    try:
        async with asyncio.timeout(min(config.overall_timeout, 3600)):
            await client.rpc('initialize', {'protocolVersion': '2024-11-05',
                'capabilities': {}, 'clientInfo': {'name': 'flowbusters-recorder', 'version': '1'}})
            proc.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
            await proc.stdin.drain()
            inventory = await client.rpc('tools/list')
            names = {tool['name'] for tool in inventory.get('tools', [])}
            if not {'browser_navigate', 'browser_snapshot', 'browser_network_requests', 'browser_network_request', 'browser_close'} <= names:
                raise RecordingError('Configured MCP server lacks required capture tools')
            await client.call('browser_navigate', {'url': config.target_url})
            notify('Browser ready — perform your workflow, then click Finish Recording')
            warnings = []
            timeline = []
            started_at = _utc_now()
            initial = await optional_snapshot(client, warnings, 'Initial', notify)
            initial_compact = compact_snapshot(initial)
            add_ui_state(timeline, initial, 'initial', started_at)
            elapsed = 0.0
            interval_raw = child_env.get('FLOWBUSTERS_UI_SNAPSHOT_INTERVAL', '1.0')
            try:
                snapshot_interval = min(10.0, max(0.25, float(interval_raw)))
            except (TypeError, ValueError):
                snapshot_interval = 1.0
            loop = asyncio.get_running_loop()
            next_snapshot = loop.time() + snapshot_interval
            timeline_warning_written = False
            while not (run_dir / 'recording_done.marker').exists():
                if proc.returncode is not None:
                    raise RecordingError('Recording MCP process exited before Finish Recording')
                await asyncio.sleep(0.25)
                elapsed += 0.25
                if config.auto_complete and elapsed >= 15:
                    (run_dir / 'recording_done.marker').write_text('auto-complete')
                if loop.time() >= next_snapshot and not (run_dir / 'recording_done.marker').exists():
                    try:
                        observed = await asyncio.wait_for(
                            client.call('browser_snapshot', {}, recover_modal=False), timeout=10)
                        add_ui_state(timeline, observed, 'change')
                    except (RecordingError, TimeoutError, OSError) as exc:
                        # Navigations can briefly make a snapshot unavailable. Do not fail
                        # the recording or spam the UI; initial/final capture still report
                        # their own actionable warning.
                        if not timeline_warning_written:
                            warnings.append('Some intermediate UI snapshots were unavailable: '
                                            + (str(exc) or type(exc).__name__))
                            timeline_warning_written = True
                    next_snapshot = loop.time() + snapshot_interval
            notify('Saving and validating recorded requests — keep the browser open')
            count = await dump_capture(client, run_dir, config.flow_name, notify)
            final = await optional_snapshot(client, warnings, 'Final', notify)
            final_compact = compact_snapshot(final)
            add_ui_state(timeline, final, 'final')
            for state in timeline:
                state.pop('_signature', None)
            entries = json.loads((run_dir / 'flows' / config.flow_name / 'recording.har').read_text())['log']['entries']
            finished_at = _utc_now()
            (run_dir / 'flows' / config.flow_name / 'demo.json').write_text(json.dumps({
                'schema_version': 2,
                'target_url': config.target_url,
                'flow_name': config.flow_name,
                'source': 'backend-mcp-recorder',
                'timestamp_start': started_at,
                'timestamp_end': finished_at,
                'initial_snapshot': initial_compact,
                'final_snapshot': final_compact,
                'workflow_timeline': {
                    'ui_states': timeline,
                    'network_sequence': network_sequence(entries),
                    'correlation_note': 'UI states are chronological and deduplicated. Network '
                        'requests are chronological. Correlate them by order, URL and visible '
                        'semantics; do not invent a click-to-request link when the recording '
                        'does not prove one.',
                },
                'warnings': warnings,
                'note': 'UI state changes are observed snapshots, not fabricated click events.',
                'request_count': count}, indent=2))
            await client.call('browser_close', {})
            (run_dir / 'recording_validated.marker').write_text(str(count))
            notify(f'Recording validated: {count} requests; browser closed')
    finally:
        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), 5)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
