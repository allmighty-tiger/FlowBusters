"""Backend-owned stdio MCP recording; independent of model turn lifetime."""
import asyncio
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit


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

    async def call(self, name, arguments):
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
            if not modal or name not in {
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
            initial = await optional_snapshot(client, warnings, 'Initial', notify)
            elapsed = 0
            while not (run_dir / 'recording_done.marker').exists():
                if proc.returncode is not None:
                    raise RecordingError('Recording MCP process exited before Finish Recording')
                await asyncio.sleep(1)
                elapsed += 1
                if config.auto_complete and elapsed >= 15:
                    (run_dir / 'recording_done.marker').write_text('auto-complete')
            notify('Saving and validating recorded requests — keep the browser open')
            count = await dump_capture(client, run_dir, config.flow_name, notify)
            final = await optional_snapshot(client, warnings, 'Final', notify)
            (run_dir / 'flows' / config.flow_name / 'demo.json').write_text(json.dumps({
                'target_url': config.target_url, 'source': 'backend-mcp-recorder',
                'initial_snapshot': initial, 'final_snapshot': final, 'warnings': warnings,
                'note': 'User actions are represented by captured HTTP traffic; no fabricated click trace.',
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
