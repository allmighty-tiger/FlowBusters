import hashlib
import hmac
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from backend.runtime.crew_runner import ArtifactWatcher
from backend.runtime.ui_provenance import (
    UIProvenanceError,
    _validate_ui_text,
    normalize_observed_ui_rules,
    resolve_json_pointer,
    validate_rule_reference,
    validate_state_map,
)


CANCEL = 'button "Cancel order" [cursor=pointer]'


class UIProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.artifacts = self.root / 'flows' / 'price-adjustment'
        self.artifacts.mkdir(parents=True)
        self.demo = {'workflow_timeline': {'ui_states': [
            {'step': 2, 'elements': [CANCEL],
             'changes_from_previous': {'appeared': [], 'disappeared': []}},
            {'step': 4, 'elements': ['button "Request refund" [cursor=pointer]'],
             'changes_from_previous': {'appeared': [], 'disappeared': [CANCEL]}},
        ]}}
        bodies = [
            {'order': {'availableActions': {'cancelOrder': True}}},
            {'message': 'Price adjustment issued.',
             'order': {'availableActions': {'cancelOrder': False}}},
        ]
        self.har = {'log': {'entries': [
            {'response': {'content': {'text': json.dumps(body)}}} for body in bodies
        ]}}
        self.rule = {
            'schema_version': 1,
            'id': 'UIR-001',
            'statement': 'Cancellation is unavailable after price adjustment',
            'facts': [
                {'id': 'UIR-001-F1', 'type': 'ui_element_transition',
                 'provenance_type': 'observed_ui_affordance',
                 'source_run': 'price-adjustment', 'artifact': 'demo.json',
                 'before_step': 2, 'after_step': 4,
                 'element': {'role': 'button', 'name': 'Cancel order'},
                 'transition': 'disappeared',
                 'json_pointers': {
                     'before': '/workflow_timeline/ui_states/0/elements/0',
                     'after': '/workflow_timeline/ui_states/1/changes_from_previous/disappeared/0'}},
                {'id': 'UIR-001-F2', 'type': 'api_field_transition',
                 'provenance_type': 'api_state_fact',
                 'source_run': 'price-adjustment', 'artifact': 'recording.har',
                 'field_path': ['order', 'availableActions', 'cancelOrder'],
                 'before': True, 'after': False, 'entry_indexes': [0, 1],
                 'json_pointers': {
                     'before': '/log/entries/0/response/content/text',
                     'after': '/log/entries/1/response/content/text'}},
            ],
            'inference': {'provenance_type': 'agent_inference',
                          'text': 'The workflow intends cancellation to be unavailable after adjustment',
                          'derived_from': ['UIR-001-F1', 'UIR-001-F2']},
        }
        self.state_map = {
            'schema_version': 2,
            'observed_ui_rules_schema_version': 1,
            'target_url': 'http://localhost:3000',
            'flow_name': 'price-adjustment',
            'transitions': [{
                'name': 'request_price_adjustment', 'method': 'POST',
                'url': 'http://localhost:3000/api/order/price-adjustment',
                'response_status': 200, 'depends_on': [],
                'ui_context': {'before_step': 2, 'after_step': 4,
                               'visible_constraints': [],
                               'observed_changes': ['Cancel order disappeared']},
            }],
            'roles': [],
            'critical_endpoints': [{'method': 'POST', 'url': '/api/order/price-adjustment'}],
            'semantic_ui_capture': {'status': 'succeeded', 'artifact': 'demo.json', 'ui_state_count': 2},
            'observed_ui_rules': [self.rule],
        }
        self._write_artifacts()

    def tearDown(self):
        self.temp.cleanup()

    def _write_artifacts(self):
        (self.artifacts / 'demo.json').write_text(json.dumps(self.demo), encoding='utf-8')
        (self.artifacts / 'recording.har').write_text(json.dumps(self.har), encoding='utf-8')
        (self.artifacts / 'state_map.json').write_text(json.dumps(self.state_map), encoding='utf-8')

    def _write_cross_flow_manifest(self, run, source_runs):
        key = b'test-only-signing-key'
        key_path = self.root / 'execution_keys' / 'receipt.key'
        key_path.parent.mkdir(parents=True, exist_ok=True)
        key_path.write_bytes(key)
        manifest = {
            'schema_version': 1,
            'target_url': 'http://localhost:3000',
            'sources': [{'run_id': source_run} for source_run in source_runs],
        }
        signature = hmac.new(
            key,
            json.dumps(manifest, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode(),
            hashlib.sha256,
        ).hexdigest()
        (run / 'cross_flow_inputs.json').write_text(
            json.dumps({**manifest, 'signature': signature}), encoding='utf-8')

    def test_real_cancel_order_disappearance_resolves_to_demo_states(self):
        rules = validate_state_map(self.state_map, self.artifacts, 'price-adjustment')
        self.assertEqual(rules[0]['facts'][0]['element'], {'role': 'button', 'name': 'Cancel order'})

    def test_api_capability_transition_is_api_fact(self):
        rules = validate_state_map(self.state_map, self.artifacts, 'price-adjustment')
        fact = rules[0]['facts'][1]
        self.assertEqual(fact['provenance_type'], 'api_state_fact')
        self.assertEqual((fact['before'], fact['after']), (True, False))

    def test_cancel_uir_002_f2_text_must_reference_snapshot_not_delta(self):
        # Exact fact/text/indexes from saved cancel UIR-002-F2. Only the
        # relevant raw snapshot fields are reproduced; ignored runs are not
        # test dependencies and the saved recording is never rewritten.
        text = 'paragraph : This order has no actions requiring your attention.'
        fact = {
            'id': 'UIR-002-F2', 'type': 'explicit_ui_text',
            'provenance_type': 'explicit_visible_ui_text',
            'source_run': 'cancel', 'artifact': 'demo.json', 'step': 5,
            'text': text,
            'json_pointer': '/workflow_timeline/ui_states/4/changes_from_previous/appeared/8',
        }
        state = {'step': 5, 'elements': ['generic : filler'] * 65 + [text],
                 'changes_from_previous': {'appeared': ['generic : filler'] * 8 + [text],
                                           'disappeared': [text]}}
        self.demo = {'workflow_timeline': {'ui_states': [
            {'step': step, 'elements': []} for step in range(1, 5)
        ] + [state]}}
        self._write_artifacts()
        original = deepcopy(fact)
        with self.assertRaisesRegex(UIProvenanceError, 'explicit_ui_text json_pointer must target'):
            _validate_ui_text(fact, 'cancel', self.artifacts)
        self.assertEqual(fact, original)  # No automatic pointer repair.
        for suffix in ('changes_from_previous/disappeared/0', 'elements',
                       'elements/-1', 'elements/065', 'elements/65/child'):
            with self.subTest(suffix=suffix):
                fact['json_pointer'] = '/workflow_timeline/ui_states/4/' + suffix
                with self.assertRaisesRegex(UIProvenanceError, 'explicit_ui_text json_pointer must target'):
                    _validate_ui_text(fact, 'cancel', self.artifacts)
        fact['json_pointer'] = '/workflow_timeline/ui_states/4/elements/65'
        _validate_ui_text(fact, 'cancel', self.artifacts)
        fact['step'] = 4
        with self.assertRaisesRegex(UIProvenanceError, 'declared UI step'):
            _validate_ui_text(fact, 'cancel', self.artifacts)
        fact['step'] = 5
        fact['json_pointer'] = '/workflow_timeline/ui_states/4/elements/64'
        with self.assertRaisesRegex(UIProvenanceError, 'differs from the pointed'):
            _validate_ui_text(fact, 'cancel', self.artifacts)
        fact['json_pointer'] = '/workflow_timeline/ui_states/4/elements/65'
        state['elements'].append(text)
        self._write_artifacts()
        with self.assertRaisesRegex(UIProvenanceError, 'ambiguous'):
            _validate_ui_text(fact, 'cancel', self.artifacts)

    def test_root_versions_do_not_replace_rule_schema_or_allow_aliases(self):
        for defect in ('missing_version', 'wrong_version', 'rule_aliases', 'fact_aliases'):
            with self.subTest(defect=defect):
                data = deepcopy(self.state_map)
                rule = data['observed_ui_rules'][0]
                if defect == 'missing_version':
                    rule.pop('schema_version')
                elif defect == 'wrong_version':
                    rule['schema_version'] = 2
                elif defect == 'rule_aliases':
                    rule['rule_id'] = rule.pop('id')
                    rule['rule'] = rule.pop('statement')
                else:
                    fact = rule['facts'][0]
                    fact['fact_id'] = fact.pop('id')
                    fact['provenance'] = fact.pop('provenance_type')
                with self.assertRaises(UIProvenanceError):
                    validate_state_map(data, self.artifacts, 'price-adjustment')

    def _cancel_price_text_fixture(self):
        text = 'generic : Eligible'
        button = 'button "Request price adjustment" [cursor=pointer]'
        before, after = self.demo['workflow_timeline']['ui_states']
        before['elements'] = [text, button]
        after['elements'] = ['generic : Unavailable']
        after['changes_from_previous']['disappeared'] = [text, button]
        return text, button

    def test_colon_text_cannot_be_invented_accessible_name(self):
        self._cancel_price_text_fixture()
        fact = self.rule['facts'][0]
        fact['element'] = {'role': 'generic', 'name': 'Eligible'}
        self._write_artifacts()
        with self.assertRaisesRegex(UIProvenanceError,
                                    'Pointed semantic element does not match the declared role/name'):
            validate_state_map(self.state_map, self.artifacts, 'price-adjustment')

    def test_named_price_adjustment_button_disappearance_validates(self):
        self._cancel_price_text_fixture()
        fact = self.rule['facts'][0]
        fact['element'] = {'role': 'button', 'name': 'Request price adjustment'}
        fact['json_pointers'] = {
            'before': '/workflow_timeline/ui_states/0/elements/1',
            'after': '/workflow_timeline/ui_states/1/changes_from_previous/disappeared/1',
        }
        self._write_artifacts()
        validate_state_map(self.state_map, self.artifacts, 'price-adjustment')
        # A nearby text pointer cannot substitute for the named control.
        fact['json_pointers']['before'] = '/workflow_timeline/ui_states/0/elements/0'
        with self.assertRaisesRegex(UIProvenanceError, 'declared role/name'):
            validate_state_map(self.state_map, self.artifacts, 'price-adjustment')

    def test_colon_text_is_valid_only_as_exact_static_text_fact(self):
        text, _ = self._cancel_price_text_fixture()
        self.rule['facts'][0] = {
            'id': 'UIR-001-F1', 'type': 'explicit_ui_text',
            'provenance_type': 'explicit_visible_ui_text',
            'source_run': 'price-adjustment', 'artifact': 'demo.json',
            'step': 2, 'text': text,
            'json_pointer': '/workflow_timeline/ui_states/0/elements/0',
        }
        self._write_artifacts()
        validated = validate_state_map(self.state_map, self.artifacts, 'price-adjustment')
        self.assertEqual(validated[0]['facts'][0]['type'], 'explicit_ui_text')
        self.rule['facts'][0]['text'] = 'Eligible'
        with self.assertRaisesRegex(UIProvenanceError, 'UI text differs'):
            validate_state_map(self.state_map, self.artifacts, 'price-adjustment')

    def _appearance_fixture(self):
        button = 'button "Complete refund" [cursor=pointer]'
        before, after = self.demo['workflow_timeline']['ui_states']
        after['elements'] = [button]
        after['changes_from_previous']['appeared'] = [button]
        fact = self.rule['facts'][0]
        fact.update(transition='appeared', element={'role': 'button', 'name': 'Complete refund'},
                    json_pointers={'before': '/workflow_timeline/ui_states/0/elements',
                                   'after': '/workflow_timeline/ui_states/1/changes_from_previous/appeared/0'})
        return button, before, after

    def test_appeared_element_absent_before_and_unique_after_validates(self):
        self._appearance_fixture()
        self._write_artifacts()
        validate_state_map(self.state_map, self.artifacts, 'price-adjustment')

    def test_appearance_rejects_existing_duplicate_missing_and_wrong_pointer(self):
        for defect in ('existing_before', 'duplicate_after', 'missing_after', 'duplicate_delta', 'wrong_pointer'):
            with self.subTest(defect=defect):
                button, before, after = self._appearance_fixture()
                before['elements'] = [CANCEL]
                if defect == 'existing_before':
                    before['elements'].append(button)
                elif defect == 'duplicate_after':
                    after['elements'].append(button)
                elif defect == 'missing_after':
                    after['elements'] = []
                elif defect == 'duplicate_delta':
                    after['changes_from_previous']['appeared'].append(button)
                else:
                    self.rule['facts'][0]['json_pointers']['before'] += '/0'
                self._write_artifacts()
                with self.assertRaises(UIProvenanceError):
                    validate_state_map(self.state_map, self.artifacts, 'price-adjustment')

    def test_enabled_disabled_requires_element_pointers_and_state_change(self):
        before, after = self.demo['workflow_timeline']['ui_states']
        for transition, states in [('became_enabled', (CANCEL + ' [disabled]', CANCEL)),
                                   ('became_disabled', (CANCEL, CANCEL + ' [disabled]'))]:
            with self.subTest(transition=transition):
                before['elements'], after['elements'] = [states[0]], [states[1]]
                fact = self.rule['facts'][0]
                fact.update(transition=transition, json_pointers={
                    'before': '/workflow_timeline/ui_states/0/elements/0',
                    'after': '/workflow_timeline/ui_states/1/elements/0'})
                self._write_artifacts()
                validate_state_map(self.state_map, self.artifacts, 'price-adjustment')
                after['elements'] = [states[0]]
                self._write_artifacts()
                with self.assertRaisesRegex(UIProvenanceError, 'Enabled/disabled transition contradicts'):
                    validate_state_map(self.state_map, self.artifacts, 'price-adjustment')
                after['elements'] = [states[1]]
                after['changes_from_previous']['appeared'] = [states[1]]
                fact['json_pointers']['after'] = '/workflow_timeline/ui_states/1/changes_from_previous/appeared/0'
                self._write_artifacts()
                with self.assertRaises(UIProvenanceError):
                    validate_state_map(self.state_map, self.artifacts, 'price-adjustment')

    def test_unchanged_api_field_is_rejected_even_when_refund_changes(self):
        for index, entry in enumerate(self.har['log']['entries']):
            body = json.loads(entry['response']['content']['text'])
            body['order']['priceProtection'] = {'status': 'eligible'}
            body['order']['refund'] = {'status': ['none', 'pending'][index]}
            entry['response']['content']['text'] = json.dumps(body)
        fact = self.rule['facts'][1]
        fact.update(id='UIR-001-F3', field_path=['order', 'priceProtection', 'status'],
                    before='eligible', after='eligible')
        self.rule['inference']['derived_from'] = ['UIR-001-F1', 'UIR-001-F3']
        self._write_artifacts()
        with self.assertRaisesRegex(UIProvenanceError,
                                    'UIR-001-F3: API transition before and after values are identical'):
            validate_state_map(self.state_map, self.artifacts, 'price-adjustment')
        # A real change at the exact referenced path remains valid.
        fact.update(field_path=['order', 'refund', 'status'], before='none', after='pending')
        validated = validate_state_map(self.state_map, self.artifacts, 'price-adjustment')
        self.assertEqual(validated[0]['facts'][1]['after'], 'pending')

    def test_fact_type_requires_its_exact_provenance_type(self):
        explicit_text = deepcopy(self.state_map)
        explicit_text['observed_ui_rules'][0]['facts'][0] = {
            'id': 'UIR-001-F1',
            'type': 'explicit_ui_text',
            'provenance_type': 'explicit_visible_ui_text',
            'source_run': 'price-adjustment',
            'artifact': 'demo.json',
            'step': 2,
            'text': CANCEL,
            'json_pointer': '/workflow_timeline/ui_states/0/elements/0',
        }
        validate_state_map(explicit_text, self.artifacts, 'price-adjustment')

        explicit_text['observed_ui_rules'][0]['facts'][0][
            'provenance_type'] = 'observed_ui_affordance'
        with self.assertRaisesRegex(
                UIProvenanceError,
                r'Observed UI rule UIR-001 fact UIR-001-F1: explicit_ui_text '
                r'requires provenance_type explicit_visible_ui_text; '
                r"got 'observed_ui_affordance'"):
            validate_state_map(explicit_text, self.artifacts, 'price-adjustment')

    def test_fact_artifact_allowlist_and_source_run_traversal(self):
        wrong_artifact = deepcopy(self.state_map)
        wrong_artifact['observed_ui_rules'][0]['facts'][0]['artifact'] = 'state_map.json'
        with self.assertRaisesRegex(UIProvenanceError, 'must reference demo.json'):
            validate_state_map(wrong_artifact, self.artifacts, 'price-adjustment')
        reference = {'source': 'observed_ui', 'provenance': {
            'schema_version': 1, 'source_run': '../price-adjustment',
            'artifact': 'state_map.json', 'rule_id': 'UIR-001',
            'fact_ids': ['UIR-001-F1']}}
        with self.assertRaisesRegex(UIProvenanceError, 'source_run is invalid'):
            validate_rule_reference(reference, self.root)

    def test_missing_fact_artifact_names_the_rule_and_fact(self):
        missing_artifact = deepcopy(self.state_map)
        del missing_artifact['observed_ui_rules'][0]['facts'][0]['artifact']
        with self.assertRaisesRegex(
                UIProvenanceError,
                r'Observed UI rule UIR-001 fact UIR-001-F1: '
                r'ui_element_transition must reference demo\.json'):
            validate_state_map(missing_artifact, self.artifacts, 'price-adjustment')

    def test_cross_flow_cannot_fall_back_to_agent_authored_flow_artifacts(self):
        (self.root / 'cross_flow_inputs.json').write_text(
            json.dumps({'schema_version': 1, 'sources': []}), encoding='utf-8')
        reference = {'source': 'observed_ui', 'provenance': {
            'schema_version': 1, 'source_run': 'price-adjustment',
            'artifact': 'state_map.json', 'rule_id': 'UIR-001',
            'fact_ids': ['UIR-001-F1']}}
        with self.assertRaisesRegex(UIProvenanceError, 'artifacts are unavailable'):
            validate_rule_reference(reference, self.root)

    def test_server_rule_is_only_agent_inference(self):
        rule = validate_state_map(self.state_map, self.artifacts, 'price-adjustment')[0]
        self.assertEqual(rule['inference']['provenance_type'], 'agent_inference')
        self.assertNotIn('agent_inference', {fact['provenance_type'] for fact in rule['facts']})

    def test_duplicate_and_cross_namespaced_fact_ids_are_rejected(self):
        duplicate = deepcopy(self.state_map)
        duplicate['observed_ui_rules'][0]['facts'].append(
            deepcopy(duplicate['observed_ui_rules'][0]['facts'][0]))
        with self.assertRaisesRegex(UIProvenanceError, 'unique'):
            validate_state_map(duplicate, self.artifacts, 'price-adjustment')
        wrong_namespace = deepcopy(self.state_map)
        wrong_namespace['observed_ui_rules'][0]['facts'][0]['id'] = 'UIR-002-F1'
        wrong_namespace['observed_ui_rules'][0]['inference']['derived_from'][0] = 'UIR-002-F1'
        with self.assertRaisesRegex(UIProvenanceError, 'namespaced'):
            validate_state_map(wrong_namespace, self.artifacts, 'price-adjustment')

    def test_broken_and_ambiguous_ui_pointers_are_rejected(self):
        broken = deepcopy(self.state_map)
        broken['observed_ui_rules'][0]['facts'][0]['json_pointers']['before'] += '0'
        with self.assertRaises(UIProvenanceError):
            validate_state_map(broken, self.artifacts, 'price-adjustment')
        ambiguous = deepcopy(self.demo)
        ambiguous['workflow_timeline']['ui_states'][0]['elements'].append(CANCEL)
        (self.artifacts / 'demo.json').write_text(json.dumps(ambiguous), encoding='utf-8')
        with self.assertRaisesRegex(UIProvenanceError, 'ambiguous'):
            validate_state_map(self.state_map, self.artifacts, 'price-adjustment')

    def test_json_pointer_escaping_is_exact_and_invalid_escapes_fail(self):
        document = {'a/b': {'x~y': 7}}
        self.assertEqual(resolve_json_pointer(document, '/a~1b/x~0y'), 7)
        for pointer in ('/a~2b', '/a~0b~2'):
            with self.assertRaisesRegex(UIProvenanceError, 'Invalid JSON pointer escape'):
                resolve_json_pointer(document, pointer)

    def test_missing_steps_and_invented_elements_are_rejected(self):
        for field, value in [('before_step', 3), ('after_step', 5)]:
            missing = deepcopy(self.state_map)
            missing['observed_ui_rules'][0]['facts'][0][field] = value
            with self.assertRaisesRegex(UIProvenanceError, 'declared UI step'):
                validate_state_map(missing, self.artifacts, 'price-adjustment')
        invented = deepcopy(self.state_map)
        invented['observed_ui_rules'][0]['facts'][0]['element']['name'] = 'Issue credit'
        with self.assertRaisesRegex(UIProvenanceError, 'role/name'):
            validate_state_map(invented, self.artifacts, 'price-adjustment')

    def test_rule_reference_requires_raw_ui_fact(self):
        reference = {'source': 'observed_ui', 'provenance': {
            'schema_version': 1, 'source_run': 'price-adjustment',
            'artifact': 'state_map.json', 'rule_id': 'UIR-001',
            'fact_ids': ['UIR-001-F1', 'UIR-001-F2']}}
        resolved = validate_rule_reference(reference, self.root)
        self.assertEqual(resolved['rule_id'], 'UIR-001')
        api_only = deepcopy(reference)
        api_only['provenance']['fact_ids'] = ['UIR-001-F2']
        with self.assertRaisesRegex(UIProvenanceError, 'raw UI fact'):
            validate_rule_reference(api_only, self.root)

    def test_api_only_cap_rule_fails_gate_even_alongside_valid_ui_rule(self):
        # Reproduce cancel-order: the HAR facts are true, but do not constitute
        # a UI-observed cap. A separate valid UI rule must not cover for it.
        for index, entry in enumerate(self.har['log']['entries']):
            body = json.loads(entry['response']['content']['text'])
            body['order'].update(
                totalReturned=[0, 100][index],
                cancellation={'status': ['none', 'completed'][index]},
            )
            entry['response']['content']['text'] = json.dumps(body)
        api_rule = deepcopy(self.rule)
        api_rule.update(id='UIR-002', statement='Total returned must not exceed originalAmount')
        api_rule['facts'] = []
        for number, path, before, after in (
                (1, ['order', 'totalReturned'], 0, 100),
                (2, ['order', 'cancellation', 'status'], 'none', 'completed')):
            fact = deepcopy(self.rule['facts'][1])
            fact.update(id=f'UIR-002-F{number}', field_path=path, before=before, after=after)
            api_rule['facts'].append(fact)
        api_rule['inference']['derived_from'] = ['UIR-002-F1', 'UIR-002-F2']
        api_rule['inference']['text'] = 'One payout of 100 suggests a cap worth testing.'
        self.state_map['observed_ui_rules'].append(api_rule)
        self._write_artifacts()
        raw_before = {name: (self.artifacts / name).read_bytes()
                      for name in ('demo.json', 'recording.har')}
        watcher = ArtifactWatcher(self.root, 'price-adjustment')
        with self.assertRaisesRegex(UIProvenanceError, 'Observed UI rule UIR-002:.*raw UI fact'):
            watcher.check()
        self.assertNotIn('state_map.json', watcher.found)

        # Agent-authored trust claims and inference text cannot bypass the gate.
        api_rule['validated'] = True
        with self.assertRaisesRegex(UIProvenanceError, 'UIR-002:.*raw UI fact'):
            validate_state_map(self.state_map, self.artifacts, 'price-adjustment')

        # Correction preserves the true UI rule and the original recordings.
        self.state_map['observed_ui_rules'].pop()
        self.state_map['critical_endpoints'][0]['why'] = (
            'Agent inference (unverified): one reimbursement was observed; '
            'a total compensation cap is a hypothesis to test, not a UI rule.')
        (self.artifacts / 'state_map.json').write_text(
            json.dumps(self.state_map), encoding='utf-8')
        self.assertIn('state_map.json', {name for name, _ in watcher.check()})
        self.assertEqual([rule['id'] for rule in validate_state_map(
            self.state_map, self.artifacts, 'price-adjustment')], ['UIR-001'])
        for name, content in raw_before.items():
            self.assertEqual((self.artifacts / name).read_bytes(), content)

    def test_schema_incompatible_new_state_map_fails_artifact_gate(self):
        accepted = ArtifactWatcher(self.root, 'price-adjustment').check()
        self.assertIn('state_map.json', {name for name, _ in accepted})
        invalid = deepcopy(self.state_map)
        del invalid['semantic_ui_capture']
        (self.artifacts / 'state_map.json').write_text(json.dumps(invalid), encoding='utf-8')
        watcher = ArtifactWatcher(self.root, 'price-adjustment')
        with self.assertRaisesRegex(UIProvenanceError, 'semantic UI capture'):
            watcher.check()

    def test_cross_flow_aggregate_may_have_no_new_rules(self):
        run = self.root / 'runs' / 'cross-test'
        artifacts = run / 'flows' / 'cross-test'
        artifacts.mkdir(parents=True)
        aggregate = deepcopy(self.state_map)
        aggregate.update({
            'mode': 'cross_flow',
            'flow_name': 'cross-test',
            'source_runs': ['price-adjustment', 'refund'],
            'observed_ui_rules': [],
        })
        aggregate['transitions'][0]['ui_context']['source_run'] = 'price-adjustment'
        aggregate['semantic_ui_capture']['no_relevant_rules_reason'] = None
        for state in self.demo['workflow_timeline']['ui_states']:
            state['_source_run'] = 'price-adjustment'
        self.demo.update({
            'mode': 'cross_flow',
            'sources': [
                {'run_id': 'price-adjustment'},
                {'run_id': 'refund'},
            ],
        })
        (artifacts / 'demo.json').write_text(json.dumps(self.demo), encoding='utf-8')
        (artifacts / 'recording.har').write_text(json.dumps(self.har), encoding='utf-8')
        self._write_cross_flow_manifest(run, aggregate['source_runs'])
        self.assertEqual(
            validate_state_map(aggregate, artifacts, 'cross-test'), [])

    def test_cross_flow_aggregate_sources_must_match_backend_manifest(self):
        run = self.root / 'runs' / 'cross-test'
        artifacts = run / 'flows' / 'cross-test'
        artifacts.mkdir(parents=True)
        aggregate = deepcopy(self.state_map)
        aggregate.update({
            'mode': 'cross_flow',
            'flow_name': 'cross-test',
            'source_runs': ['price-adjustment', 'invented'],
            'observed_ui_rules': [],
        })
        aggregate['transitions'][0]['ui_context']['source_run'] = 'price-adjustment'
        for state in self.demo['workflow_timeline']['ui_states']:
            state['_source_run'] = 'price-adjustment'
        self.demo.update({
            'mode': 'cross_flow',
            'sources': [
                {'run_id': 'price-adjustment'},
                {'run_id': 'refund'},
            ],
        })
        (artifacts / 'demo.json').write_text(json.dumps(self.demo), encoding='utf-8')
        (artifacts / 'recording.har').write_text(json.dumps(self.har), encoding='utf-8')
        self._write_cross_flow_manifest(run, ['price-adjustment', 'refund'])
        with self.assertRaisesRegex(UIProvenanceError, 'source_runs must match'):
            validate_state_map(aggregate, artifacts, 'cross-test')

    def test_ordinary_empty_rules_still_need_an_explicit_reason(self):
        ordinary = deepcopy(self.state_map)
        ordinary['observed_ui_rules'] = []
        ordinary['semantic_ui_capture']['no_relevant_rules_reason'] = None
        with self.assertRaisesRegex(UIProvenanceError, 'no-relevant-rules reason'):
            validate_state_map(ordinary, self.artifacts, 'price-adjustment')

    def test_cross_flow_steps_are_resolved_within_the_named_source(self):
        run = self.root / 'runs' / 'cross-test'
        artifacts = run / 'flows' / 'cross-test'
        artifacts.mkdir(parents=True)
        aggregate = deepcopy(self.state_map)
        aggregate.update({
            'mode': 'cross_flow', 'flow_name': 'cross-test',
            'source_runs': ['price-adjustment', 'refund'],
            'observed_ui_rules': [],
        })
        aggregate['transitions'][0]['ui_context']['source_run'] = 'price-adjustment'
        states = self.demo['workflow_timeline']['ui_states']
        for state in states:
            state['_source_run'] = 'price-adjustment'
        self.demo.update({'mode': 'cross_flow', 'sources': [
            {'run_id': 'price-adjustment'}, {'run_id': 'refund'}]})
        (artifacts / 'demo.json').write_text(json.dumps(self.demo), encoding='utf-8')
        (artifacts / 'recording.har').write_text(json.dumps(self.har), encoding='utf-8')
        self._write_cross_flow_manifest(run, aggregate['source_runs'])
        validate_state_map(aggregate, artifacts, 'cross-test')

        missing_source = deepcopy(aggregate)
        del missing_source['transitions'][0]['ui_context']['source_run']
        with self.assertRaisesRegex(UIProvenanceError, 'exact source_run'):
            validate_state_map(missing_source, artifacts, 'cross-test')

        wrong_local_step = deepcopy(aggregate)
        wrong_local_step['transitions'][0]['ui_context']['after_step'] = 3
        with self.assertRaisesRegex(UIProvenanceError, 'source-local'):
            validate_state_map(wrong_local_step, artifacts, 'cross-test')

    def test_inferred_transition_has_no_invented_http_result(self):
        inferred = deepcopy(self.state_map)
        inferred['transitions'].append({
            'name': 'cancel_order',
            'method': 'POST',
            'url': 'http://localhost:3000/api/order/cancel',
            'response_status': None,
            'inferred': True,
            'depends_on': [],
            'ui_context': {
                'before_step': 2,
                'after_step': 2,
                'visible_constraints': ['Cancel order is visible'],
                'observed_changes': ['Not executed; no state transition observed'],
            },
        })
        self.assertEqual(len(validate_state_map(
            inferred, self.artifacts, 'price-adjustment')), 1)

        fabricated = deepcopy(inferred)
        fabricated['transitions'][-1]['response_status'] = 200
        with self.assertRaisesRegex(UIProvenanceError, 'must not claim'):
            validate_state_map(fabricated, self.artifacts, 'price-adjustment')

        observed_without_status = deepcopy(self.state_map)
        observed_without_status['transitions'][0]['response_status'] = None
        with self.assertRaisesRegex(UIProvenanceError, 'observed transition needs'):
            validate_state_map(observed_without_status, self.artifacts, 'price-adjustment')

    def test_baseline_read_is_evidence_not_an_equal_step_transition(self):
        baseline = deepcopy(self.state_map['transitions'][0])
        baseline.update(name='get_order', method='GET', url='http://localhost:3000/api/order')
        baseline['ui_context'].update(before_step=2, after_step=2)
        invalid = deepcopy(self.state_map)
        invalid['transitions'].insert(0, baseline)
        with self.assertRaisesRegex(UIProvenanceError, 'ordered before/after steps'):
            validate_state_map(invalid, self.artifacts, 'price-adjustment')
        # The observed baseline cannot be passed off as an unexecuted inference.
        baseline['inferred'] = True
        with self.assertRaisesRegex(UIProvenanceError, 'must not claim an observed response_status'):
            validate_state_map(invalid, self.artifacts, 'price-adjustment')
        # Keep the baseline HAR response, but only the real POST in transitions.
        self.assertEqual(len(self.har['log']['entries']), 2)
        validate_state_map(self.state_map, self.artifacts, 'price-adjustment')
        backwards = deepcopy(self.state_map)
        backwards['transitions'][0]['ui_context'].update(before_step=4, after_step=2)
        with self.assertRaisesRegex(UIProvenanceError, 'ordered before/after steps'):
            validate_state_map(backwards, self.artifacts, 'price-adjustment')

    def test_legacy_state_map_remains_readable_but_unverified(self):
        legacy = {'observed_ui_rules': [{'text': 'Cancel order disappears'}]}
        normalized = normalize_observed_ui_rules(legacy, 'price-adjustment', self.artifacts)
        self.assertEqual(normalized[0]['provenance_status'], 'legacy_unverified')
        self.assertIn('#/observed_ui_rules/0', normalized[0]['legacy_reference'])


if __name__ == '__main__':
    unittest.main()
