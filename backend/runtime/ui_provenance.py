"""Strict provenance for business rules derived from sampled semantic UI state."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import hmac
import json
import re
from pathlib import Path


STATE_MAP_SCHEMA_VERSION = 2
OBSERVED_UI_RULES_SCHEMA_VERSION = 1
_RUN_ID = re.compile(r'[A-Za-z0-9_-]+')
_RULE_ID = re.compile(r'UIR-[0-9]{3,}')
_FACT_ID = re.compile(r'UIR-[0-9]{3,}-F[0-9]+')
_UI_FACT_TYPES = {'explicit_ui_text', 'ui_element_transition'}
_FACT_PROVENANCE = {
    'explicit_ui_text': 'explicit_visible_ui_text',
    'ui_element_transition': 'observed_ui_affordance',
    'api_field_transition': 'api_state_fact',
}


class UIProvenanceError(ValueError):
    def __init__(self, message, *, details=None):
        super().__init__(message)
        self.details = dict(details or {})


def _require(condition, message):
    if not condition:
        raise UIProvenanceError(message)


def resolve_json_pointer(document, pointer):
    """Resolve one exact JSON pointer. Never search, coerce, or guess indexes."""
    _require(isinstance(pointer, str) and (pointer == '' or pointer.startswith('/')),
             'JSON pointer must be empty or begin with /')
    value = document
    if pointer == '':
        return value
    for encoded in pointer[1:].split('/'):
        _require(re.search(r'~(?![01])', encoded) is None,
                 f'Invalid JSON pointer escape in {pointer}')
        token = encoded.replace('~1', '/').replace('~0', '~')
        if isinstance(value, dict):
            _require(token in value, f'JSON pointer does not resolve: {pointer}')
            value = value[token]
        elif isinstance(value, list):
            _require(bool(re.fullmatch(r'0|[1-9][0-9]*', token)),
                     f'Array pointer index is invalid: {pointer}')
            index = int(token)
            _require(index < len(value), f'Array pointer index is out of bounds: {pointer}')
            value = value[index]
        else:
            raise UIProvenanceError(f'JSON pointer traverses a scalar: {pointer}')
    return value


def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError) as exc:
        raise UIProvenanceError(f'Cannot read provenance artifact {Path(path).name}: {exc}') from exc


def _tokens(pointer):
    _require(isinstance(pointer, str) and pointer.startswith('/'), 'A non-root JSON pointer is required')
    return [part.replace('~1', '/').replace('~0', '~') for part in pointer[1:].split('/')]


def _semantic_element(value):
    _require(isinstance(value, str), 'Semantic UI pointer must resolve to a string element')
    match = re.match(r'^([A-Za-z][A-Za-z0-9_-]*)\s*(?:"([^"]+)")?', value)
    _require(match is not None, 'Semantic UI element has no role/name representation')
    return match.group(1), match.group(2), value


def _matching_elements(state, role, name):
    matches = []
    for value in state.get('elements', []) if isinstance(state, dict) else []:
        try:
            found_role, found_name, _ = _semantic_element(value)
        except UIProvenanceError:
            continue
        if found_role == role and found_name == name:
            matches.append(value)
    return matches


def _artifact(artifact_dir, name):
    _require(name in ('demo.json', 'recording.har'),
             f'Unsupported provenance artifact: {name}')
    root = Path(artifact_dir).resolve()
    path = (root / name).resolve()
    _require(path.parent == root, 'Provenance artifact escapes its source directory')
    return path


def _state_map_artifact(artifact_dir):
    root = Path(artifact_dir).resolve()
    path = (root / 'state_map.json').resolve()
    _require(path.parent == root, 'State-map artifact escapes its source directory')
    return path


def _validate_source(fact, source_run, artifact):
    _require(fact.get('source_run') == source_run, 'Fact source_run does not match its state map')
    _require(fact.get('artifact') == artifact, f'{fact.get("type")} must reference {artifact}')


def _state_for_pointer(demo, pointer, expected_step, suffix):
    parts = _tokens(pointer)
    prefix = ['workflow_timeline', 'ui_states']
    _require(parts[:2] == prefix and len(parts) >= 4, 'UI pointer must target workflow_timeline/ui_states')
    _require(bool(re.fullmatch(r'0|[1-9][0-9]*', parts[2])), 'UI pointer has an invalid state index')
    state_index = int(parts[2])
    states = demo.get('workflow_timeline', {}).get('ui_states')
    _require(isinstance(states, list) and state_index < len(states), 'UI state pointer is out of bounds')
    state = states[state_index]
    _require(isinstance(state, dict) and state.get('step') == expected_step,
             'UI pointer does not resolve to the declared UI step')
    _require(parts[3:] == suffix or parts[3:-1] == suffix,
             'UI pointer targets an unsupported field')
    return state, resolve_json_pointer(demo, pointer)


def _validate_ui_text(fact, source_run, artifact_dir):
    _validate_source(fact, source_run, 'demo.json')
    _require(fact.get('provenance_type') == 'explicit_visible_ui_text',
             'Explicit UI text has the wrong provenance type')
    _require(type(fact.get('step')) is int and fact['step'] > 0, 'UI text fact needs a positive step')
    _require(isinstance(fact.get('text'), str) and fact['text'].strip(), 'UI text fact needs exact text')
    pointer = fact.get('json_pointer')
    parts = _tokens(pointer)
    _require(len(parts) == 5 and parts[:2] == ['workflow_timeline', 'ui_states']
             and parts[3] == 'elements'
             and bool(re.fullmatch(r'0|[1-9][0-9]*', parts[4])),
             'explicit_ui_text json_pointer must target '
             '/workflow_timeline/ui_states/<state_index>/elements/<element_index>; '
             'changes_from_previous is only for the corresponding UI transition')
    demo = _read_json(_artifact(artifact_dir, 'demo.json'))
    state, value = _state_for_pointer(demo, pointer, fact['step'], ['elements'])
    if value != fact['text']:
        raise UIProvenanceError(
            f'UI text differs from the pointed semantic element: {pointer} resolves to {value!r}; '
            f'claimed {fact["text"]!r}', details={
            'code': 'EXPLICIT_UI_TEXT_MISMATCH', 'artifact': 'demo.json',
            'step': fact['step'], 'json_pointer': pointer,
            'claimed_text': fact['text'], 'pointed_element': value,
        })
    _require(state.get('elements', []).count(value) == 1, 'UI text fact is ambiguous in its state')


def _validate_ui_transition(fact, source_run, artifact_dir):
    _validate_source(fact, source_run, 'demo.json')
    _require(fact.get('provenance_type') == 'observed_ui_affordance',
             'UI transition has the wrong provenance type')
    before_step, after_step = fact.get('before_step'), fact.get('after_step')
    _require(type(before_step) is int and type(after_step) is int and 0 < before_step < after_step,
             'UI transition needs ordered positive before/after steps')
    element = fact.get('element')
    _require(isinstance(element, dict) and isinstance(element.get('role'), str)
             and isinstance(element.get('name'), str) and element['role'] and element['name'],
             'UI transition needs an exact role and accessible name')
    transition = fact.get('transition')
    _require(transition in ('appeared', 'disappeared', 'became_enabled', 'became_disabled'),
             'Unsupported semantic UI transition')
    pointers = fact.get('json_pointers')
    _require(isinstance(pointers, dict) and set(pointers) == {'before', 'after'},
             'UI transition needs exact before and after JSON pointers')
    demo = _read_json(_artifact(artifact_dir, 'demo.json'))
    before_state, before_value = _state_for_pointer(
        demo, pointers['before'], before_step, ['elements'])
    delta_name = transition if transition in ('appeared', 'disappeared') else None
    if delta_name:
        after_state, after_value = _state_for_pointer(
            demo, pointers['after'], after_step, ['changes_from_previous', delta_name])
    else:
        after_state, after_value = _state_for_pointer(demo, pointers['after'], after_step, ['elements'])
    role, name = element['role'], element['name']
    pointed_elements = (after_value,) if transition == 'appeared' else (before_value, after_value)
    for value in pointed_elements:
        found_role, found_name, _ = _semantic_element(value)
        _require((found_role, found_name) == (role, name),
                 'Pointed semantic element does not match the declared role/name')
    if transition != 'appeared':
        _require(len(_matching_elements(before_state, role, name)) == 1,
                 'Before-state semantic element is missing or ambiguous')
    if transition == 'disappeared':
        _require(before_value == after_value, 'Disappeared-element pointers contradict each other')
        _require(len(_matching_elements(after_state, role, name)) == 0,
                 'Element claimed as disappeared remains in the after state')
        _require(after_state.get('changes_from_previous', {}).get('disappeared', []).count(after_value) == 1,
                 'Disappearance evidence is missing or ambiguous')
    elif transition == 'appeared':
        _require(isinstance(before_value, list),
                 'Appeared transition before pointer must resolve to the exact elements array')
        _require(len(_matching_elements(before_state, role, name)) == 0,
                 'Element claimed as appeared already exists in the before state')
        _require(len(_matching_elements(after_state, role, name)) == 1,
                 'Appeared element is missing or ambiguous in the after state')
        _require(after_state.get('changes_from_previous', {}).get('appeared', []).count(after_value) == 1,
                 'Appearance evidence is missing or ambiguous')
    else:
        _require(len(_matching_elements(after_state, role, name)) == 1,
                 'After-state semantic element is missing or ambiguous')
        before_disabled = '[disabled]' in before_value
        after_disabled = '[disabled]' in after_value
        expected = (False, True) if transition == 'became_disabled' else (True, False)
        _require((before_disabled, after_disabled) == expected,
                 'Enabled/disabled transition contradicts the pointed elements')


def _path_value(value, path):
    _require(isinstance(path, list) and path and all(isinstance(part, str) and part for part in path),
             'API fact field_path must be a non-empty array of object keys')
    for part in path:
        _require(isinstance(value, dict) and part in value,
                 'API field path does not resolve exactly')
        value = value[part]
    return value


def _validate_api_transition(fact, source_run, artifact_dir):
    _validate_source(fact, source_run, 'recording.har')
    _require(fact.get('provenance_type') == 'api_state_fact',
             'API field transition has the wrong provenance type')
    indexes = fact.get('entry_indexes')
    _require(isinstance(indexes, list) and len(indexes) == 2
             and all(type(index) is int and index >= 0 for index in indexes)
             and indexes[0] < indexes[1], 'API transition needs two ordered HAR entry indexes')
    pointers = fact.get('json_pointers')
    _require(isinstance(pointers, dict) and set(pointers) == {'before', 'after'},
             'API transition needs exact before and after JSON pointers')
    har = _read_json(_artifact(artifact_dir, 'recording.har'))
    values = []
    for label, index in zip(('before', 'after'), indexes):
        expected = ['log', 'entries', str(index), 'response', 'content', 'text']
        _require(_tokens(pointers[label]) == expected,
                 'HAR response pointer does not match its declared entry index')
        body = resolve_json_pointer(har, pointers[label])
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except ValueError as exc:
                raise UIProvenanceError('HAR response body is not valid JSON') from exc
        values.append(_path_value(body, fact.get('field_path')))
    _require(type(values[0]) is type(fact.get('before')) and values[0] == fact.get('before'),
             'API before value contradicts the raw HAR response')
    _require(type(values[1]) is type(fact.get('after')) and values[1] == fact.get('after'),
             'API after value contradicts the raw HAR response')
    _require(values[0] != values[1], 'API transition before and after values are identical')


def validate_observed_ui_rule(rule, source_run, artifact_dir):
    _require(isinstance(rule, dict), 'Observed UI rule must be an object')
    _require(rule.get('schema_version') == OBSERVED_UI_RULES_SCHEMA_VERSION,
             'Observed UI rule schema version is missing or unsupported')
    _require(bool(_RULE_ID.fullmatch(str(rule.get('id') or ''))), 'Observed UI rule id is invalid')
    _require(isinstance(rule.get('statement'), str) and rule['statement'].strip(),
             'Observed UI rule statement is required')
    facts = rule.get('facts')
    _require(isinstance(facts, list) and facts, 'Observed UI rule needs directly sourced facts')
    ids = []
    for fact in facts:
        fact_id = fact.get('id') if isinstance(fact, dict) else None
        fact_label = fact_id if isinstance(fact_id, str) and fact_id else '<unknown>'
        try:
            _require(isinstance(fact, dict), 'Rule fact must be an object')
            fact_id, fact_type = fact.get('id'), fact.get('type')
            _require(bool(_FACT_ID.fullmatch(str(fact_id or ''))), 'Rule fact id is invalid')
            _require(str(fact_id).startswith(rule['id'] + '-F'),
                     'Rule fact id must be namespaced by its rule id')
            _require(fact_id not in ids, 'Rule fact ids must be unique')
            _require(fact_type in _FACT_PROVENANCE, 'Unsupported rule fact type')
            expected_provenance = _FACT_PROVENANCE[fact_type]
            actual_provenance = fact.get('provenance_type')
            _require(actual_provenance == expected_provenance,
                     f'{fact_type} requires provenance_type {expected_provenance}; '
                     f'got {actual_provenance!r}')
            ids.append(fact_id)
            if fact_type == 'explicit_ui_text':
                _validate_ui_text(fact, source_run, artifact_dir)
            elif fact_type == 'ui_element_transition':
                _validate_ui_transition(fact, source_run, artifact_dir)
            else:
                _validate_api_transition(fact, source_run, artifact_dir)
        except UIProvenanceError as exc:
            raise UIProvenanceError(
                f'Observed UI rule {rule["id"]} fact {fact_label}: {exc}',
                details={**exc.details, 'rule_id': rule['id'], 'fact_id': fact_label,
                         'source_run': source_run},
            ) from exc
    _require(any(fact.get('type') in _UI_FACT_TYPES for fact in facts),
             f'Observed UI rule {rule["id"]}: An observed UI rule must contain at least one raw UI fact '
             '(explicit_ui_text or ui_element_transition from demo.json). API-only facts and '
             'agent inference do not qualify; do not invent or attach unrelated UI evidence.')
    inference = rule.get('inference')
    _require(isinstance(inference, dict)
             and inference.get('provenance_type') == 'agent_inference'
             and isinstance(inference.get('text'), str) and inference['text'].strip(),
             'Rule inference must be explicitly attributed to agent_inference')
    derived = inference.get('derived_from')
    _require(isinstance(derived, list) and derived and len(derived) == len(set(derived))
             and all(item in ids for item in derived),
             'Inference derived_from must reference unique fact ids from this rule')
    return deepcopy(rule)


def validate_state_map(data, artifact_dir, source_run, require_current=True):
    """Validate a new state map and dereference every structured UI-rule fact."""
    _require(isinstance(data, dict), 'state_map.json must contain an object')
    if data.get('schema_version') != STATE_MAP_SCHEMA_VERSION:
        if require_current:
            raise UIProvenanceError('state_map.json schema_version must be 2 for new runs')
        return []
    _require(data.get('observed_ui_rules_schema_version') == OBSERVED_UI_RULES_SCHEMA_VERSION,
             'observed_ui_rules_schema_version must be 1')
    for key in ('target_url', 'flow_name'):
        _require(isinstance(data.get(key), str) and data[key].strip(), f'state map needs {key}')
    _require(data.get('flow_name') == source_run, 'state map flow_name does not match source_run')
    demo = _read_json(_artifact(artifact_dir, 'demo.json'))
    states = demo.get('workflow_timeline', {}).get('ui_states')
    _require(isinstance(states, list), 'demo.json needs workflow_timeline.ui_states')
    available_steps = {state.get('step') for state in states if isinstance(state, dict)}
    cross_flow_aggregate = data.get('mode') == 'cross_flow' and demo.get('mode') == 'cross_flow'
    manifest_ids = []
    if cross_flow_aggregate:
        declared_sources = data.get('source_runs')
        demo_sources = demo.get('sources')
        demo_ids = [item.get('run_id') for item in demo_sources or [] if isinstance(item, dict)]
        run_dir = Path(artifact_dir).resolve().parent.parent
        manifest = validate_cross_flow_manifest(run_dir)
        manifest_sources = manifest.get('sources')
        manifest_ids = [item.get('run_id') for item in manifest_sources or [] if isinstance(item, dict)]
        _require(isinstance(declared_sources, list) and len(declared_sources) >= 2
                 and len(declared_sources) == len(set(declared_sources))
                 and len(manifest_ids) == len(set(manifest_ids))
                 and set(declared_sources) == set(manifest_ids)
                 and demo_ids == manifest_ids,
                 'Cross-flow aggregate source_runs must match its backend-created source manifest')
    transitions = data.get('transitions')
    _require(isinstance(transitions, list) and transitions, 'state map needs at least one transition')
    for transition in transitions:
        _require(isinstance(transition, dict), 'Transition must be an object')
        for key in ('name', 'method', 'url'):
            _require(isinstance(transition.get(key), str) and transition[key].strip(),
                     f'Transition needs {key}')
        inferred = transition.get('inferred', False)
        _require(type(inferred) is bool, 'Transition inferred must be a boolean')
        if inferred:
            _require(transition.get('response_status') is None,
                     'An inferred transition must not claim an observed response_status')
        else:
            _require(type(transition.get('response_status')) is int,
                     'An observed transition needs an integer response_status')
        _require(isinstance(transition.get('depends_on'), list), 'Transition depends_on must be an array')
        context = transition.get('ui_context')
        _require(isinstance(context, dict), 'Transition needs ui_context')
        _require(type(context.get('before_step')) is int and type(context.get('after_step')) is int,
                 'Transition ui_context needs integer before/after steps')
        if inferred:
            _require(0 < context['before_step'] == context['after_step'],
                     'An inferred transition must reference one supporting UI step, not claim a transition')
        else:
            _require(0 < context['before_step'] < context['after_step'],
                     'Observed transition ui_context needs ordered before/after steps')
        if cross_flow_aggregate:
            context_source = context.get('source_run')
            _require(isinstance(context_source, str) and context_source in manifest_ids,
                     'Cross-flow transition ui_context needs an exact source_run')
            source_steps = {
                state.get('step') for state in states
                if isinstance(state, dict) and state.get('_source_run') == context_source
            }
            _require(context['before_step'] in source_steps and context['after_step'] in source_steps,
                     'Cross-flow transition ui_context references a missing source-local semantic UI step')
        else:
            _require(context['before_step'] in available_steps and context['after_step'] in available_steps,
                     'Transition ui_context references a missing semantic UI step')
        for key in ('visible_constraints', 'observed_changes'):
            _require(isinstance(context.get(key), list)
                     and all(isinstance(item, str) for item in context[key]),
                     f'Transition ui_context.{key} must be an array of strings')
    _require(isinstance(data.get('roles'), list), 'state map roles must be an array')
    _require(isinstance(data.get('critical_endpoints'), list) and data['critical_endpoints'],
             'state map needs at least one critical endpoint')
    capture = data.get('semantic_ui_capture')
    _require(isinstance(capture, dict) and capture.get('status') == 'succeeded'
             and capture.get('artifact') == 'demo.json'
             and type(capture.get('ui_state_count')) is int and capture['ui_state_count'] > 0,
             'state map must attest successful semantic UI capture')
    _require(isinstance(states, list) and len(states) == capture['ui_state_count'],
             'semantic_ui_capture.ui_state_count contradicts demo.json')
    rules = data.get('observed_ui_rules')
    _require(isinstance(rules, list), 'observed_ui_rules must be an array')
    if not rules:
        if not cross_flow_aggregate:
            _require(isinstance(capture.get('no_relevant_rules_reason'), str)
                     and capture['no_relevant_rules_reason'].strip(),
                     'An empty observed_ui_rules array needs a no-relevant-rules reason')
    validated = [validate_observed_ui_rule(rule, source_run, artifact_dir) for rule in rules]
    ids = [rule['id'] for rule in validated]
    _require(len(ids) == len(set(ids)), 'Observed UI rule ids must be unique')
    return validated


def normalize_observed_ui_rules(state_map, source_run, artifact_dir):
    """Return verified current rules or clearly marked, never-upgraded legacy rules."""
    if not isinstance(state_map, dict) or state_map.get('schema_version') != STATE_MAP_SCHEMA_VERSION:
        rules = state_map.get('observed_ui_rules', []) if isinstance(state_map, dict) else []
        return [{
            'id': f'LEGACY-UIR-{index + 1:03d}',
            'statement': (str(rule.get('text') or rule.get('statement') or '')
                          if isinstance(rule, dict) else str(rule)),
            'source_run': source_run,
            'provenance_status': 'legacy_unverified',
            'legacy_reference': f'state_map.json#/observed_ui_rules/{index}',
        } for index, rule in enumerate(rules) if isinstance(rule, (dict, str))]
    rules = validate_state_map(state_map, artifact_dir, source_run, require_current=True)
    return [{**rule, 'source_run': source_run, 'provenance_status': 'verified'} for rule in rules]


def _source_directory(run_dir, source_run):
    _require(bool(_RUN_ID.fullmatch(str(source_run or ''))), 'Rule source_run is invalid')
    run_dir = Path(run_dir).resolve()
    if (run_dir / 'cross_flow_inputs.json').exists():
        # Cross-flow provenance is restricted to backend-copied source snapshots.
        # The agent-authored aggregate state map can never stand in for a source.
        candidates = [run_dir / 'cross_flow_sources' / source_run]
    else:
        candidates = [run_dir / 'flows' / source_run]
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved.is_dir() and resolved.is_relative_to(run_dir):
            return resolved
    raise UIProvenanceError('Rule source_run artifacts are unavailable')


def _runtime_root(run_dir):
    run_dir = Path(run_dir).resolve()
    return run_dir.parent.parent if run_dir.parent.name == 'runs' else run_dir.parent


def validate_cross_flow_manifest(run_dir, root=None):
    """Authenticate the backend-created cross-flow source manifest."""
    manifest = _read_json(Path(run_dir) / 'cross_flow_inputs.json')
    signature = manifest.get('signature') if isinstance(manifest, dict) else None
    unsigned = {key: value for key, value in manifest.items() if key != 'signature'} if isinstance(manifest, dict) else {}
    key_path = (Path(root).resolve() if root is not None else _runtime_root(run_dir)) / 'execution_keys' / 'receipt.key'
    try:
        key = key_path.read_bytes()
    except OSError as exc:
        raise UIProvenanceError('Cross-flow source manifest signing key is unavailable') from exc
    expected_signature = hmac.new(
        key, json.dumps(unsigned, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode(),
        hashlib.sha256).hexdigest()
    _require(isinstance(signature, str) and hmac.compare_digest(signature, expected_signature),
             'Cross-flow source manifest signature is invalid')
    return manifest


def _verify_cross_flow_snapshot(run_dir, source_run, artifact_dir, root=None):
    if artifact_dir.parent.name != 'cross_flow_sources':
        return
    manifest = validate_cross_flow_manifest(run_dir, root=root)
    sources = manifest.get('sources') if isinstance(manifest, dict) else None
    matches = [item for item in sources or [] if isinstance(item, dict) and item.get('run_id') == source_run]
    _require(len(matches) == 1, 'Cross-flow source manifest entry is missing or ambiguous')
    state_map = _read_json(_state_map_artifact(artifact_dir))
    har = _read_json(_artifact(artifact_dir, 'recording.har'))
    demo = _read_json(_artifact(artifact_dir, 'demo.json'))
    # The signed manifest stores the exact rule representation that was hashed
    # by collect(). Historically that was the normalized view; current strict
    # collection may store the schema-native validated view. Both must derive
    # exactly from the immutable state map. Never normalize one side and then
    # compare its digest with a hash made from the other representation.
    validated_rules = validate_state_map(
        state_map, artifact_dir, source_run, require_current=True)
    normalized_rules = normalize_observed_ui_rules(
        state_map, source_run, artifact_dir)
    signed_rules = matches[0].get('observed_ui_rules')
    _require(signed_rules == validated_rules or signed_rules == normalized_rules,
             'Cross-flow manifest rule metadata differs from validated source rules')
    payload = {'run_id': source_run, 'state_map': state_map, 'har': har, 'demo': demo,
                 'observed_ui_rules': signed_rules,
                 'observed_ui_provenance': matches[0].get('observed_ui_provenance')}
    if matches[0].get('application_identity'):
        payload['application_identity'] = matches[0]['application_identity']
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    _require(digest == matches[0].get('sha256'),
             'Cross-flow source artifacts differ from the backend snapshot manifest')


def _validate_structured_reference(rule_reference, run_dir, root=None):
    provenance = rule_reference.get('provenance')
    _require(isinstance(provenance, dict)
             and provenance.get('schema_version') == OBSERVED_UI_RULES_SCHEMA_VERSION,
             'Observed UI provenance schema version is missing or unsupported')
    _require(provenance.get('artifact') == 'state_map.json',
             'Observed UI provenance must reference state_map.json')
    source_run = provenance.get('source_run')
    artifact_dir = _source_directory(run_dir, source_run)
    _verify_cross_flow_snapshot(run_dir, source_run, artifact_dir, root=root)
    state_map = _read_json(_state_map_artifact(artifact_dir))
    rules = validate_state_map(state_map, artifact_dir, source_run, require_current=True)
    matches = [rule for rule in rules if rule.get('id') == provenance.get('rule_id')]
    _require(len(matches) == 1, 'Observed UI rule id is missing or ambiguous')
    rule = matches[0]
    fact_ids = provenance.get('fact_ids')
    _require(isinstance(fact_ids, list) and fact_ids and len(fact_ids) == len(set(fact_ids)),
             'Observed UI reference needs unique structured fact ids')
    facts = [fact for fact in rule['facts'] if fact['id'] in fact_ids]
    _require(len(facts) == len(fact_ids), 'Observed UI reference contains an unknown fact id')
    return rule, facts, source_run, fact_ids


def validate_rule_reference(rule_reference, run_dir, root=None):
    """Dereference a verification rule into validated raw observed-UI facts."""
    _require(isinstance(rule_reference, dict) and rule_reference.get('source') == 'observed_ui',
             'Structured observed_ui rule reference is required')
    rule, facts, source_run, fact_ids = _validate_structured_reference(rule_reference, run_dir, root=root)
    _require(any(fact['type'] in _UI_FACT_TYPES for fact in facts),
             'Positive observed_ui provenance must reference a validated raw UI fact')
    return {'schema_version': OBSERVED_UI_RULES_SCHEMA_VERSION,
            'source_run': source_run, 'rule_id': rule['id'],
            'fact_ids': list(fact_ids), 'facts': deepcopy(facts)}


def validate_supporting_rule_reference(rule_reference, run_dir, root=None):
    """Validate structured fact IDs used as API or inference support."""
    _require(isinstance(rule_reference, dict)
             and rule_reference.get('source') in ('api_state', 'agent_inference'),
             'Structured API/inference rule reference is required')
    rule, facts, source_run, fact_ids = _validate_structured_reference(rule_reference, run_dir, root=root)
    if rule_reference['source'] == 'api_state':
        _require(all(fact['type'] == 'api_field_transition' for fact in facts),
                 'api_state provenance may reference only validated API facts')
    else:
        derived = rule['inference']['derived_from']
        _require(all(fact_id in derived for fact_id in fact_ids),
                 'agent_inference provenance must reference facts used by the inference')
    return {'schema_version': OBSERVED_UI_RULES_SCHEMA_VERSION,
            'source_run': source_run, 'rule_id': rule['id'],
            'fact_ids': list(fact_ids), 'facts': deepcopy(facts)}
