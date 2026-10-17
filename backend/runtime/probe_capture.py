"""Child harness: capture HTTPX/requests transport calls independently of stdout.

Not a sandbox for hostile Python. Unsupported transports cannot supply verified
evidence; their stdout alone never passes the provenance gate.
"""
import json
from pathlib import Path
import sys
import threading
import time
from urllib.parse import urlsplit
import fnmatch


def main():
    script, trace_path, scope_path = sys.argv[1:4]
    original_path = sys.argv[4] if len(sys.argv) > 4 else script
    scope = json.loads(open(scope_path, encoding='utf-8').read())
    lock = threading.Lock()
    counter = 0

    def allowed(url, method):
        parsed = urlsplit(url)
        origins = scope.get('allowed_domains', [])
        authority = f'{parsed.scheme}://{parsed.netloc}'
        catalog = scope.get('_endpoint_catalog')
        if catalog is not None and not any(row['method'] == method.upper()
                and row['origin'] == authority and row['path'] == (parsed.path or '/') for row in catalog):
            raise ValueError('Endpoint selection error: request is absent from authenticated endpoint catalog: '
                             + method + ' ' + str(url))
        if parsed.scheme not in ('http', 'https') or not any(
                fnmatch.fnmatchcase(authority, str(pattern).removesuffix('/*').rstrip('/'))
                or parsed.hostname == pattern for pattern in origins):
            raise ValueError('HTTP request outside configured allowed_domains')
        if not any(prefix == '*' or parsed.path.startswith(prefix)
                   for prefix in scope.get('allowed_paths_prefix', [])):
            raise ValueError('HTTP request outside configured allowed_paths_prefix')
        if scope.get('block_production', True) and parsed.hostname not in ('localhost', '127.0.0.1', '::1') and not (parsed.hostname or '').endswith('.test'):
            raise ValueError('block_production requires a local/test target')

    def body(value):
        if isinstance(value, bytes):
            value = value.decode('utf-8', errors='replace')
        if value in ('', None):
            return None
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return str(value)

    def begin(method, url, payload):
        nonlocal counter
        allowed(str(url), method)
        with lock:
            counter += 1
            return {'sequence': counter, 'started_ns': time.monotonic_ns(),
                    'request': {'method': method, 'url': str(url), 'body': body(payload)}}

    def finish(event, status=None, payload=None, error=None):
        event.update(completed_ns=time.monotonic_ns(), status_code=status,
                     response=body(payload), error=error, complete=error is None)
        with lock, open(trace_path, 'a', encoding='utf-8') as stream:
            stream.write(json.dumps(event) + '\n')

    try:
        import httpx
        sync_send = httpx.Client._send_single_request
        async_send = httpx.AsyncClient._send_single_request

        def send(client, request):
            request.read()
            event = begin(request.method, request.url, request.content)
            try:
                response = sync_send(client, request)
                response.read()
                finish(event, response.status_code, response.content)
                response.extensions['flowbusters_sequence'] = event['sequence']
                return response
            except Exception as exc:
                finish(event, error=str(exc))
                raise

        async def asend(client, request):
            await request.aread()
            event = begin(request.method, request.url, request.content)
            try:
                response = await async_send(client, request)
                await response.aread()
                finish(event, response.status_code, response.content)
                response.extensions['flowbusters_sequence'] = event['sequence']
                return response
            except Exception as exc:
                finish(event, error=str(exc))
                raise

        httpx.Client._send_single_request = send
        httpx.AsyncClient._send_single_request = asend
    except ImportError:
        pass
    try:
        import requests
        original = requests.adapters.HTTPAdapter.send

        def requests_send(adapter, request, **kwargs):
            event = begin(request.method, request.url, request.body)
            try:
                response = original(adapter, request, **kwargs)
                finish(event, response.status_code, response.content)
                response.flowbusters_sequence = event['sequence']
                return response
            except Exception as exc:
                finish(event, error=str(exc))
                raise
        requests.adapters.HTTPAdapter.send = requests_send
    except ImportError:
        pass
    # Standard-library clients (including the existing verified-delete helper).
    import io
    import urllib.request
    original_open = urllib.request.AbstractHTTPHandler.do_open

    def urllib_open(handler, connection, request, **kwargs):
        event = begin(request.get_method(), request.full_url, request.data)
        try:
            response = original_open(handler, connection, request, **kwargs)
            payload = response.read()
            finish(event, response.status, payload)
            response.flowbusters_sequence = event['sequence']
            buffered = io.BytesIO(payload)
            response.read = lambda amount=None: buffered.read(-1 if amount is None else amount)
            response.readinto = buffered.readinto
            response.readline = buffered.readline
            return response
        except Exception as exc:
            finish(event, error=str(exc))
            raise
    urllib.request.AbstractHTTPHandler.do_open = urllib_open
    def audit(event, args):
        if event in ('subprocess.Popen', 'os.system', 'os.exec', 'os.posix_spawn', 'os.fork'):
            raise RuntimeError('Probe subprocesses are unsupported: use an in-process HTTP client for captured execution')
    sys.addaudithook(audit)
    sys.argv = [original_path]
    sys.path.insert(0, str(Path(original_path).parent))
    code = compile(Path(script).read_bytes(), original_path, 'exec')
    exec(code, {'__name__': '__main__', '__file__': original_path, '__package__': None})


if __name__ == '__main__':
    main()
