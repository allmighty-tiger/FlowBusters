# Observed UI rule provenance contract

New `state_map.json` files use `schema_version: 2` and
`observed_ui_rules_schema_version: 1`. The canonical JSON Schema is
`backend/runtime/schemas/observed_ui_rules.v1.json`; the backend also resolves
every reference against the source artifacts before accepting it.

The state map must attest the sampled semantic timeline:

```json
"semantic_ui_capture": {
  "status": "succeeded",
  "artifact": "demo.json",
  "ui_state_count": 4
}
```

If no relevant rule was observed, `observed_ui_rules` may be empty only when
capture succeeded and `semantic_ui_capture.no_relevant_rules_reason` is a
non-empty explanation. Do not invent a rule to avoid an empty array.

A cross-flow aggregate is the one exception: it performs no new browser
observation and must not copy or upgrade source rules. When both the state map
and backend-created `demo.json` declare `mode: cross_flow`, the aggregate keeps
`observed_ui_rules: []` and its exact `source_runs` must match the source
manifest. Verified source rules remain referenced by ID from candidates;
legacy rules may be referenced only as `agent_inference` with their exact
`LEGACY-UIR-*` ID and `fact_ids: []`. They remain unverified and cannot confirm
a positive finding.

Combined cross-flow `demo.json` states retain their source-local `step` values,
so the same step number can occur under multiple `_source_run` values. Every
aggregate transition `ui_context` must name one exact `source_run`; resolve its
`before_step` and `after_step` only among states with that `_source_run`. Never
treat the array position as a global step number and never guess across sources.

Each rule separates raw facts from inference:

```json
{
  "schema_version": 1,
  "id": "UIR-001",
  "statement": "Cancellation is unavailable after price adjustment",
  "facts": [
    {
      "id": "UIR-001-F1",
      "type": "ui_element_transition",
      "provenance_type": "observed_ui_affordance",
      "source_run": "price-adjustment",
      "artifact": "demo.json",
      "before_step": 2,
      "after_step": 4,
      "element": {"role": "button", "name": "Cancel order"},
      "transition": "disappeared",
      "json_pointers": {
        "before": "/workflow_timeline/ui_states/1/elements/64",
        "after": "/workflow_timeline/ui_states/3/changes_from_previous/disappeared/4"
      }
    },
    {
      "id": "UIR-001-F2",
      "type": "api_field_transition",
      "provenance_type": "api_state_fact",
      "source_run": "price-adjustment",
      "artifact": "recording.har",
      "field_path": ["order", "availableActions", "cancelOrder"],
      "before": true,
      "after": false,
      "entry_indexes": [0, 1],
      "json_pointers": {
        "before": "/log/entries/0/response/content/text",
        "after": "/log/entries/1/response/content/text"
      }
    }
  ],
  "inference": {
    "provenance_type": "agent_inference",
    "text": "The workflow intends cancellation to be unavailable after adjustment",
    "derived_from": ["UIR-001-F1", "UIR-001-F2"]
  }
}
```

The indexes above are illustrative. Calculate exact zero-based array indexes
from the active immutable artifacts. A pointer must resolve exactly; never
search recursively, guess an index, or cite a whole object when an element is
required.

Allowed facts use exact, inseparable tuples. Never choose `provenance_type`
from the element's meaning; choose it from the fact `type`:

- `explicit_ui_text` + `explicit_visible_ui_text` + `artifact: demo.json`: exact
  `step`, exact semantic element string in `text`, and `json_pointer` to it. A
  button or link seen at one sampled step belongs to this tuple.
- `ui_element_transition` + `observed_ui_affordance` + `artifact: demo.json`:
  exact before/after steps, role and accessible name, transition (`appeared`, `disappeared`,
  `became_enabled`, or `became_disabled`), and exact before/after pointers. For
  `appeared`, the before pointer resolves to the complete before-state
  `elements` array and the after pointer resolves to the exact `appeared` item.
- `api_field_transition` + `api_state_fact` + `artifact: recording.har`: exact
  two HAR entry indexes, response-body pointers, object-key `field_path`, and
  typed before/after values.

`availableActions` and other response JSON are API facts, never UI facts. A
server-side policy derived from facts is `agent_inference`, never a directly
observed fact. User-provided and specification rules are separate provenance
types supplied outside this observed-UI schema.

Every transition in a v2 state map must contain `ui_context` with ordered
positive `before_step` and `after_step`, plus string arrays
`visible_constraints` and `observed_changes`.

Mutation and cross-flow verification cite a rule structurally:

```json
"rule": {
  "source": "observed_ui",
  "provenance": {
    "schema_version": 1,
    "source_run": "price-adjustment",
    "artifact": "state_map.json",
    "rule_id": "UIR-001",
    "fact_ids": ["UIR-001-F1"]
  }
}
```

At least one cited fact must be raw UI evidence. A prose path such as
`price-adjustment/state_map.json observed_ui_rules[2]`, an API fact alone, or an
agent inference cannot confirm a positive finding.

Cross-flow candidates use the same `provenance` object for `source: api_state`
or `source: agent_inference`, so their rule and fact IDs are still
dereferenceable. An `api_state` reference may select only API facts; an
`agent_inference` reference may select only facts named by that rule's
`inference.derived_from`. These types can support prioritization and analysis
but cannot promote a positive execution to CONFIRMED.
