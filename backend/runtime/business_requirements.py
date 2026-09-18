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


REGISTRY_PATH = Path(__file__).with_name('business_requirements.json')
RUN_ID_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$')


class BusinessRequirementError(ValueError):
    pass


def _require(condition, message):
    if not condition:
        raise BusinessRequirementError(message)


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
    _require(isinstance(data, dict) and data.get('schema_version') == 2,
             'Backend requirement registry schema_version must be 2')
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
        _require(isinstance(rule.get('application_id'), str) and rule['application_id'],
                 f'Backend requirement {identifier} needs application_id')
        versions = rule.get('identity_versions')
        _require(isinstance(versions, list) and versions
                 and all(isinstance(item, str) and item for item in versions),
                 f'Backend requirement {identifier} needs identity_versions')
        _require(isinstance(rule.get('requirements_version'), str)
                 and rule['requirements_version'].strip(),
                 f'Backend requirement {identifier} needs requirements_version')
        predicate = rule.get('predicate')
        _require(isinstance(predicate, dict) and predicate.get('operator') == 'sum_lte',
                 f'Backend requirement {identifier} needs a supported sum_lte predicate')
        terms = predicate.get('terms')
        _require(isinstance(terms, list) and terms,
                 f'Backend requirement {identifier} needs predicate terms')
        normalized.append({**deepcopy(rule), 'identity_versions': list(versions),
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


def apply_backend_requirements(report, trusted_run_id=None, trusted_identity=None,
                               registry_path=REGISTRY_PATH):
    """Attach an exact product/version/predicate match, failing closed.

    The canonical run id is only a path-integrity check. It is never rule scope
    and cannot substitute for a backend-authenticated application identity.
    """
    if not isinstance(report, dict):
        return report
    try:
        _require(isinstance(trusted_run_id, str) and RUN_ID_RE.fullmatch(trusted_run_id),
                 'A trusted report-path run id is required')
        _require(isinstance(trusted_identity, dict),
                 'A backend-authenticated application identity is required')
        app_id = trusted_identity.get('application_id')
        identity_version = trusted_identity.get('identity_version')
        requirements_version = trusted_identity.get('requirements_version')
        _require(isinstance(app_id, str) and isinstance(identity_version, str)
                 and isinstance(requirements_version, str),
                 'Application identity fields are invalid')
        requirements = load_business_requirements(registry_path)
    except BusinessRequirementError:
        # Missing/invalid controlled configuration must fail closed: no finding
        # gains authority from report content.
        app_id, identity_version, requirements_version, requirements = None, None, None, []
    for collection in ('findings', 'results'):
        for record in report.get(collection, []) if isinstance(report.get(collection), list) else []:
            if not isinstance(record, dict):
                continue
            record.pop('_backend_requirement_valid', None)
            record.pop('backend_requirement', None)
            verification = record.get('verification')
            invariant = verification.get('invariant') if isinstance(verification, dict) else None
            matches = [rule for rule in requirements
                       if app_id == rule['application_id']
                       and identity_version in rule['identity_versions']
                       and requirements_version == rule['requirements_version']
                       and invariant == rule['predicate']]
            if len(matches) == 1:
                rule = deepcopy(matches[0])
                evidence_date = str(report.get('run_timestamp') or '')[:10]
                retrospective = bool(evidence_date and evidence_date < rule['asserted_on'])
                rule['application_mode'] = ('retrospective_evidence_evaluation'
                                            if retrospective else 'prospective_evaluation')
                rule['retrospective'] = retrospective
                rule['application_identity'] = {
                    'application_id': app_id,
                    'identity_version': identity_version,
                    'requirements_version': requirements_version,
                }
                record['_backend_requirement_valid'] = True
                record['backend_requirement'] = rule
    return report
