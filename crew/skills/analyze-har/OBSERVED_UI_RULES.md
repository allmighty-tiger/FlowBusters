# Observed UI rule provenance contract

New `state_map.json` files use `schema_version: 2` and
`observed_ui_rules_schema_version: 1`. The canonical JSON Schema is
`backend/runtime/schemas/observed_ui_rules.v1.json`; the backend also resolves
every reference against the source artifacts before accepting it.

Before saving, re-read the generated JSON and check exact field names:

- Check EACH rule independently for at least one directly relevant raw UI fact:
  `explicit_ui_text` or `ui_element_transition` from `demo.json`, with exact
  references that resolve. API facts may supplement such a rule; an API-only
  rule does not belong in `observed_ui_rules`. UI evidence in a different rule
  does not satisfy this requirement. An `inference` object is never a raw UI fact.
- For example, HAR `totalReturned: 0 -> 100` and
  `cancellation.status: none -> completed` show one reimbursement. They do NOT
  establish a universal rule `totalReturned <= originalAmount`. Do not emit
  that API-only cap hypothesis as a UIR. Never append an unrelated button or
  amount label merely to satisfy the raw-UI requirement; seeing a value or an
  available action does not prove a payout limit.
- Keep genuine UI-grounded rules. Omit API-only rule definitions and references
  to their nonexistent rule/fact IDs. If useful for mutation planning, describe
  the hypothesis in the relevant existing `critical_endpoints[].why`, explicitly
  prefixed `Agent inference (unverified):`, with its raw API observations clearly
  distinguished from the proposed rule. This prose is analysis, not verified
  provenance. Do not invent new provenance schema fields or mark it as user,
  specification, or observed_ui. Saboteur may investigate it as agent_inference;
  it cannot supply authority for CONFIRMED. Keep conflicting_action_pairs only
  where actual co-visible UI actions support the pair.

- `transitions` is not an inventory of every HAR request. Initial GET/HEAD
  reads describe baseline state; keep them in the raw HAR and use their exact
  references as evidence, but do not emit a baseline read as an observed
  transition with equal UI steps. For example, GET /api/order at step 1 is
  baseline evidence; a recorded price-adjustment POST with snapshots 1 and 3
  is a transition with before_step 1 and after_step 3. Require
  `0 < before_step < after_step` for every observed transition. Never invent
  a later snapshot or relabel an observed baseline read as `inferred` to pass
  the gate. Equal steps are reserved for genuinely unexecuted inferred actions
  with `inferred: true` and `response_status: null`. Remove baseline-read names
  from transition `depends_on`; dependencies refer to actual action transitions.

- The root has `schema_version: 2` and `observed_ui_rules_schema_version: 1`.
  These do NOT replace `schema_version: 1` inside EACH observed_ui_rules item.
- Each rule requires `schema_version`, `id`, `statement`, `facts`, `inference`.
  Do not use `rule_id` or `rule` as aliases for `id` or `statement` here.
- Each fact requires `id`, `type`, `provenance_type`, `source_run`, `artifact`.
  Do not rename these to `fact_id` or `provenance`.
- Text facts use `step`, `text`, `json_pointer`, not `raw_text` or `pointer`.
- UI transitions use `element: {"role": "...", "name": "..."}`,
  `before_step`, `after_step`, `transition`, and
  `json_pointers: {"before": "...", "after": "..."}`. Do not emit an element
  string, `before_pointer`, `after_pointer`, or `present -> absent`:
  the supported disappearance value is `disappeared`.
- Inference requires `provenance_type: agent_inference`, `text`, and
  `derived_from` containing existing fact IDs. Keep inference separate from facts.

`rule_id` and `fact_ids` belong to references to an existing rule, not the
definition of that rule. Use the complete canonical example below; do not
invent a shortened schema. Check the complete map before publishing it. A backend
gate failure blocks probe execution. After initial draft generation exits, the
backend can launch at most TWO dedicated Analyst correction attempts with the
structured validator error and immutable evidence copies. This is real feedback,
not an open-ended request to wait for a corrected file.

### Bounded correction contract

In correction mode, read `validator_error.json`, the rejected `state_map.json`,
`demo.json`, and `recording.har`. Error details identify the rule/fact, claimed
text, exact JSON pointer and actual pointed element. Regenerate the COMPLETE
state map, preserving every rule/fact ID, statement, claimed text and inference.
Correct only exact evidenced locators/steps or non-rule schema fields. Do not
drop a rule/fact, weaken its meaning, change raw evidence, or proceed to probes.
Return a JSON object with `state_map` and a nonempty `correction_notes` string
explaining the exact changes. The correction session has Read tools only; the
backend validates and publishes a successful proposal, not the agent.

Example from the saved `UIR-004-F2` shape: the claim is
`paragraph : This order has no actions requiring your attention.` but
`/workflow_timeline/ui_states/5/elements/64` resolves to just `paragraph`.
These strings are NOT equal. If raw state index 5 (step 6) contains the exact
claimed text uniquely at `elements[67]`, explicitly propose that pointer and
explain the old/new locations. Never change the claim to `paragraph` to pass,
reuse the index 67 in another recording, or choose between duplicate matches.
If no unambiguous location supports the claim, report inability to correct it;
do not remove it. After two rejected attempts the assessment fails with the
precise error and no probes execute. Earlier drafts and correction feedback
are preserved for debugging.

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
  `step`, exact semantic element string in `text`, and `json_pointer` ONLY to
  `/workflow_timeline/ui_states/<state_index>/elements/<element_index>`.
  Both indexes are zero-based array indexes; `step` must equal the selected
  state's stored `step`, not its array index. A button or link seen at one
  sampled step belongs to this tuple. Never point explicit text at
  `changes_from_previous.appeared` or `.disappeared`, even if the string matches.
- `ui_element_transition` + `observed_ui_affordance` + `artifact: demo.json`:
  exact before/after steps, role and accessible name, transition (`appeared`, `disappeared`,
  `became_enabled`, or `became_disabled`), and exact before/after pointers. For
  `appeared`, the before pointer resolves to the complete before-state
  `elements` array and the after pointer resolves to the exact `appeared` item.
  The element must be absent before and unique after. For `became_enabled`
  and `became_disabled`, BOTH pointers must reference individual items in
  their respective state's `elements` arrays, never `changes_from_previous`.
  The exact role/name must be unique in both states, and `[disabled]` must
  change from present to absent (enabled), or absent to present (disabled).
  Copy role/name only from a named semantic element such as
  `button "Request price adjustment" [cursor=pointer]`. In `generic : Eligible`,
  `Eligible` is text content, not a quoted accessible name; do not emit
  `element: {"role": "generic", "name": "Eligible"}`. For an action-availability
  claim, use the actual named button's transition and its exact pointers.
  A static text observation may instead use `explicit_ui_text` with the full
  string `generic : Eligible`, its step and exact elements pointer. It proves
  text at that step only, not disappearance or action availability. If no
  supported fact expresses the observation, omit it and its inference references.
- `api_field_transition` + `api_state_fact` + `artifact: recording.har`: exact
  two HAR entry indexes, response-body pointers, object-key `field_path`, and
  typed before/after values that actually differ. Before emitting the fact,
  resolve both response pointers, parse the JSON bodies, and read the exact
  field_path. Both declared values and their types must match the raw values;
  the two raw values must be unequal. Different requests or changed sibling
  fields do not make an unchanged field a transition.

### Exact explicit-text example (cancel recording shape)

If state index 4 has `step: 5` and `elements[65]` contains the following exact
string uniquely, the fact is:

```json
{
  "id": "UIR-002-F2",
  "type": "explicit_ui_text",
  "provenance_type": "explicit_visible_ui_text",
  "source_run": "cancel",
  "artifact": "demo.json",
  "step": 5,
  "text": "paragraph : This order has no actions requiring your attention.",
  "json_pointer": "/workflow_timeline/ui_states/4/elements/65"
}
```

`/workflow_timeline/ui_states/4/changes_from_previous/appeared/8` is INVALID
for that fact, even when it contains the same string. Delta pointers are only
for `ui_element_transition`, with `transition: appeared` or `disappeared`
matching the selected delta and with the required before/after evidence.
The paragraph above has no quoted accessible name; do not invent one to turn
it into an element transition. Use the exact text observation instead.
Recompute indexes and `source_run` from the active run, including reanalysis;
this is an example, not permission to reuse another recording's pointers.
Never repair a pointer by searching for matching text. An absent or duplicate
element is not valid provenance; omit the unsupported fact and dependent
inference instead of guessing.

For example, priceProtection.status = "eligible" in both responses is NOT an
api_field_transition, even if refund.status changed from "none" to "pending"
or a UI button disappeared. Omit that unchanged-field fact from facts and
remove its ID from inference.derived_from. The current schema has no static
API-field fact type: do not invent one or relabel API JSON as visible UI text.
An unchanged API field alone does not prove a client-side-only restriction or
an exploitable endpoint; any such suggestion remains an unverified hypothesis.

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
