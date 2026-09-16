"""Backend-controlled business requirements used during report loading.

This registry is repository-owned configuration, not report or agent output.
Rules may be applied retrospectively to immutable signed evidence, but their
assertion metadata must never imply that they existed before ``asserted_on``.
"""
from copy import deepcopy
from datetime import date
import json
from pathlib import Path
import re
from urllib.parse import urlsplit


REGISTRY_PATH = Path(__file__).with_name('business_requirements.json')
RUN_ID_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$')


class BusinessRequirementError(ValueError):
    pass


def _require(condition, message):
    if not condition:
        raise BusinessRequirementError(message)


def _origin(value):
    parsed = urlsplit(str(value or ''))
    _require(parsed.scheme in ('http', 'https') and parsed.hostname,
             'Requirement target origin is invalid')
    port = f':{parsed.port}' if parsed.port is not None else ''
    return f'{parsed.scheme.lower()}://{parsed.hostname.lower()}{port}'


def _path(value, label):
    _require(isinstance(value, list) and value
             and all(isinstance(part, str) and part for part in value),
             f'Requirement {label} must be a non-empty object-key path')
    return list(value)


def load_business_requirements(path=REGISTRY_PATH):
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError) as exc:
        raise BusinessRequirementError(f'Cannot load backend requirement registry: {exc}') from exc
    _require(isinstance(data, dict) and data.get('schema_version') == 1,
             'Backend requirement registry schema_version must be 1')
    rules = data.get('requirements')
    _require(isinstance(rules, list), 'Backend requirement registry needs requirements')
    normalized, identifiers = [], set()
    for rule in rules:
        _require(isinstance(rule, dict), 'Backend requirement must be an object')
        identifier = rule.get('id')
        _require(isinstance(identifier, str) and identifier and identifier not in identifiers,
                 'Backend requirement ids must be non-empty and unique')
        identifiers.add(identifier)
        _require(rule.get('authority') == 'user_asserted_requirement'
                 and rule.get('controlled_by') == 'backend_registry',
                 f'Backend requirement {identifier} has an invalid authority')
        for field in ('product', 'statement', 'assertion_context'):
            _require(isinstance(rule.get(field), str) and rule[field].strip(),
                     f'Backend requirement {identifier} needs {field}')
        try:
            asserted = date.fromisoformat(rule['asserted_on'])
            effective = date.fromisoformat(rule['effective_from'])
        except (KeyError, TypeError, ValueError) as exc:
            raise BusinessRequirementError(
                f'Backend requirement {identifier} needs ISO assertion/effective dates') from exc
        _require(effective >= asserted,
                 f'Backend requirement {identifier} cannot predate its assertion')
        _require(rule.get('application_mode') == 'retrospective_evidence_evaluation',
                 f'Backend requirement {identifier} needs explicit retrospective application mode')
        origins = rule.get('target_origins')
        _require(isinstance(origins, list) and origins,
                 f'Backend requirement {identifier} needs target_origins')
        origins = [_origin(origin) for origin in origins]
        _require(len(origins) == len(set(origins)),
                 f'Backend requirement {identifier} target_origins must be unique')
        run_ids = rule.get('allowed_run_ids')
        _require(isinstance(run_ids, list) and run_ids,
                 f'Backend requirement {identifier} needs allowed_run_ids')
        _require(all(isinstance(run_id, str) and RUN_ID_RE.fullmatch(run_id)
                     for run_id in run_ids),
                 f'Backend requirement {identifier} has an invalid allowed run id')
        _require(len(run_ids) == len(set(run_ids)),
                 f'Backend requirement {identifier} allowed_run_ids must be unique')
        predicate = rule.get('predicate')
        _require(isinstance(predicate, dict) and predicate.get('operator') == 'sum_lte',
                 f'Backend requirement {identifier} needs a supported sum_lte predicate')
        terms = predicate.get('terms')
        _require(isinstance(terms, list) and terms,
                 f'Backend requirement {identifier} needs predicate terms')
        normalized.append({**deepcopy(rule), 'target_origins': origins,
                           'allowed_run_ids': list(run_ids),
                           'predicate': {'operator': 'sum_lte',
                                         'terms': [_path(term, 'term') for term in terms],
                                         'limit': _path(predicate.get('limit'), 'limit')}})
    return normalized


def trusted_report_run_id(report_path):
    """Derive a run id only from the canonical backend report location."""
    path = Path(report_path).resolve(strict=False)
    report_dir = path.parent
    reports_dir = report_dir.parent
    run_dir = reports_dir.parent
    if (path.name != 'findings.json' or reports_dir.name != 'reports'
            or run_dir.parent.name != 'runs' or report_dir.name != run_dir.name
            or not RUN_ID_RE.fullmatch(run_dir.name)):
        return None
    return run_dir.name


def apply_backend_requirements(report, trusted_run_id=None, registry_path=REGISTRY_PATH):
    """Attach exact scoped matches; discard any agent-supplied trust flags."""
    if not isinstance(report, dict):
        return report
    try:
        _require(isinstance(trusted_run_id, str) and RUN_ID_RE.fullmatch(trusted_run_id),
                 'A trusted report-path run id is required')
        target_origin = _origin(report.get('target_url'))
        requirements = load_business_requirements(registry_path)
    except BusinessRequirementError:
        # Missing/invalid controlled configuration must fail closed: no finding
        # gains authority from report content.
        target_origin, requirements = None, []
    for collection in ('findings', 'results'):
        for record in report.get(collection, []) if isinstance(report.get(collection), list) else []:
            if not isinstance(record, dict):
                continue
            record.pop('_backend_requirement_valid', None)
            record.pop('backend_requirement', None)
            verification = record.get('verification')
            invariant = verification.get('invariant') if isinstance(verification, dict) else None
            matches = [rule for rule in requirements
                       if trusted_run_id in rule['allowed_run_ids']
                       and target_origin in rule['target_origins']
                       and invariant == rule['predicate']]
            if len(matches) == 1:
                record['_backend_requirement_valid'] = True
                record['backend_requirement'] = deepcopy(matches[0])
    return report
