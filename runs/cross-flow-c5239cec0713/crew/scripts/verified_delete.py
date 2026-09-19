"""Execute a configured, authorized deletion check and persist raw evidence.

Usage: python3 crew/scripts/verified_delete.py CONFIG.json OUTPUT.json
CONFIG supplies actual recorded URLs, headers, JSON paths (arrays of keys),
item_id, finding_id, rule, and complete_collection / isolated_resource booleans.
No endpoint guessing, lifecycle replay, redirects, or automatic re-login.
"""
import hashlib
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class PreconditionsMissing(Exception):
    pass


def extract(body, path):
    for key in path:
        body = body[key]
    return body


def execute(config):
    urls = [config['read_url'], config['delete_url']]
    origin = lambda u: (urlsplit(u).scheme, urlsplit(u).netloc)
    if origin(urls[0]) != origin(urls[1]) or origin(urls[0])[0] not in ('http', 'https'):
        raise ValueError('Read and action must use the same authorized HTTP origin')
    headers = dict(config.get('headers', {}))
    principal = hashlib.sha256(json.dumps(headers, sort_keys=True).encode()).hexdigest()
    opener = build_opener(NoRedirect())
    v = {'predicate': 'approved_item_must_remain', 'rule': config['rule']}

    def request(method, url):
        req = {'method': method, 'url': url}
        try:
            response = opener.open(Request(url, method=method, headers=headers), timeout=10)
        except HTTPError as exc:
            response = exc
        with response:
            return req, {'status_code': response.code,
                         'body': response.read().decode('utf-8', 'replace')}

    def read(sequence):
        req, res = request('GET', urls[0])
        snapshot = dict(resource_id=urls[0], principal_id=principal,
                        sequence=sequence, status_code=res['status_code'],
                        request=req, response=res, complete=False)
        v['before' if sequence == 1 else 'after'] = snapshot
        if res['status_code'] != 200:
            raise ValueError('State read did not return 200')
        body = json.loads(res['body'])
        items = extract(body, config['items_path'])
        if not isinstance(items, list):
            raise ValueError('Expected a collection')
        snapshot.update(state=extract(body, config['state_path']),
                        item_ids=[x[config.get('id_key', 'id')] for x in items],
                        complete=config.get('complete_collection') is True)
        return snapshot

    try:
        before = read(1)
        if (not before['complete'] or config.get('isolated_resource') is not True
                or before['state'] != 'APPROVED' or config['item_id'] not in before['item_ids']):
            gaps = []
            if not before['complete']: gaps.append('A complete collection is required')
            if config.get('isolated_resource') is not True: gaps.append('An isolated test resource is required')
            if before['state'] != 'APPROVED': gaps.append('The resource must be APPROVED')
            if config['item_id'] not in before['item_ids']: gaps.append('The target item must exist in the collection')
            raise PreconditionsMissing('; '.join(gaps))
        try:
            req, res = request('DELETE', urls[1])
            v['action'] = dict(resource_id=urls[0], principal_id=principal, sequence=2,
                               method='DELETE', item_id=config['item_id'], request=req, response=res)
        except Exception as exc:
            v['error'] = 'Action capture failed: ' + type(exc).__name__
        read(3)
    except PreconditionsMissing as exc:
        v['execution'] = {'status': 'NOT_EXECUTED', 'missing_precondition': str(exc),
            'next_step': 'Prepare an authorized isolated approved resource with a complete collection containing the target item, then rerun this check.'}
    except Exception as exc:
        v['error'] = 'State check failed: ' + str(exc)
    return dict(id=config['finding_id'], title=config.get('title', 'Approved item deletion check'),
                source='VERIFIED_DELETE', severity=config.get('severity', 'Not assessed'),
                cwe=['CWE-841'], script='verified_delete.py', url_tested=urls[1],
                remediation=config.get('remediation', 'Enforce the supplied lifecycle rule server-side on child mutations.'),
                expected_behavior=config['rule'].get('reference', ''), verification=v)


if __name__ == '__main__':
    result = execute(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")))
    output = Path(sys.argv[2])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({'finding_id': result['id'], 'evidence_file': str(output)}))
