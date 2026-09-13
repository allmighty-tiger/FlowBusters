"""Offline stdio MCP fixture: no browser or AI service."""
import json
import sys
from pathlib import Path

mode = sys.argv[1]
root = Path.cwd()
attempts = {}
snapshots = 0
detached = {5} if mode == 'navdetached' else set()
for line in sys.stdin:
    message = json.loads(line)
    if 'id' not in message:
        continue
    method = message['method']
    result = {}
    if method == 'tools/list':
        result = {'tools': [{'name': name} for name in ['browser_navigate', 'browser_snapshot',
            'browser_network_requests', 'browser_network_request', 'browser_close']]}
    if method == 'tools/call':
        name = message['params']['name']
        args = message['params']['arguments']
        with (root / 'calls.jsonl').open('a') as log:
            log.write(json.dumps({'name': name, 'args': args}) + '\n')
        if name == 'browser_snapshot':
            snapshots += 1
            if mode == 'snapshot_error' or (mode == 'final_snapshot_error' and snapshots == 2):
                result = {'isError': True, 'content': [{'type': 'text', 'text': 'Target page has been closed'}]}
            elif mode == 'timeline':
                total = '$100' if snapshots == 1 else '$80'
                applied = '' if snapshots == 1 else '\n- status: Coupon applied\n- button "Apply Coupon" [disabled] [ref=e9]'
                result = {'content': [{'type': 'text', 'text':
                    f'- Page URL: http://fixture.test/cart\n- text: Total {total}{applied}'}]}
        if name == 'browser_handle_dialog':
            if mode == 'modal':
                (root / 'dialog_resolved.marker').write_text('auto-dismiss')
        if name == 'browser_network_requests':
            if mode == 'modal' and not (root / 'dialog_resolved.marker').exists():
                result = {'isError': True, 'content': [{'type': 'text', 'text':
                    'Tool does not handle the modal state. Modal state: confirm. Can be handled by browser_handle_dialog'}]}
            else:
                m3 = 'POST' if mode in ('bodyless', 'postbody') else 'GET'
                Path(args['filename']).write_text(f'3. [{m3}] http://fixture.test/api/data => [200] OK\n5. [GET] http://fixture.test/api/other => [200] OK\n')
        if name == 'browser_network_request':
            index = args['index']
            part = args.get('part')
            method = 'POST' if (index == 3 and mode in ('bodyless', 'postbody')) else 'GET'
            key = (index, part)
            attempts[key] = attempts.get(key, 0) + 1
            if index in detached and part == 'response-body':
                result = {'isError': True}  # navigation detached the body; never recoverable
            elif mode == 'missing' and part == 'response-body':
                result = {'isError': True}  # broken capture: no response bodies at all
            elif index == 3 and part == 'response-body' and (mode == 'retry' and attempts[key] == 1):
                result = {'isError': True}
            elif part == 'request-body':
                # Mirror playwright: a bodyless request has no postData, so the
                # read succeeds but writes no file. A GET must never yield a body.
                if method != 'GET':
                    Path(args['filename']).write_text('username=x&password=y')
            elif part == 'response-body':
                Path(args['filename']).write_text('{"ok": true}')
            else:
                request_hint = ('Call browser_network_request with part="request-body" to read the request body.\n'
                                if mode == 'postbody' and index == 3 else '')
                Path(args['filename']).write_text(f'#'+str(index)+' ['+method+'] http://fixture.test/api/data\n  General\n    status: [200] OK\n    mimeType: application/json\n  Response headers\n    content-type: application/json\n\n'+request_hint+'Call browser_network_request with part="response-body" to read the response body.\n')
        if name == 'browser_close':
            if not (root / 'flows/test/recording.har').exists():
                result = {'isError': True}
    print(json.dumps({'jsonrpc': '2.0', 'id': message['id'], 'result': result}), flush=True)
