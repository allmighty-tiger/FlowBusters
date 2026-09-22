# Backend test-contract audit — 2026-09-22

Baseline: 277 discovered tests, 19 assertion failures and 8 errors. Two failed
module imports hid their individual tests. Restoring discovery increases the
test count; no test is deleted to obtain a passing suite.

Final full discovery: **306 tests, OK**. Count reconciliation: 277 minus two
failed-import placeholders plus 14 integration tests and 9 grouping tests = 298;
one numeric-regeneration test, three queue-status tests and one process-failure
continuation test = 303; three live-run regression tests (endpoint-preflight
exception, duplicate analytical/executor row, complete unsupported downgrade)
= 306. Focused suite: 56 tests, OK.

## All 19 baseline assertion failures

| Test module / method | Classification and correction |
| --- | --- |
| test_application_identity / test_unsigned_manifest_fails_closed | Obsolete: identity is disabled, not a source of confirmation. Assert no identity is assigned. |
| test_application_identity_smoke / test_different_predicate_cannot_inherit_rule | Obsolete verdict name: incomplete inference is INSUFFICIENT_EVIDENCE; still cannot confirm. |
| test_application_identity_smoke / test_signed_violation_confirms_finding_and_execution_result | Obsolete product-rule authority: a signed amount alone lacks the new inference metadata. Assert insufficient evidence in both views. |
| test_business_requirements / test_free_form_user_or_specification_reference_is_not_authority | Obsolete reason wording; still reject free-form authority. |
| test_business_requirements / test_multiple_runs_of_same_authenticated_application_match | Obsolete registry contract: no product rule is loaded. |
| test_business_requirements / test_other_application_on_same_origin_does_not_match | Obsolete registry reason; no application receives authority from origin. |
| test_cross_flow_requirement / test_mass_assignment_amount_bound_does_not_prove_input_control | Obsolete diagnostic substring; retain rejection of unrelated predicates. |
| test_cross_flow_requirement / test_same_rule_is_applied_to_every_matching_finding_and_result | Obsolete rule authority; both incomplete-inference results stay insufficient. |
| test_not_executed / test_missing_fixture_prevents_action | Obsolete security-verdict name; execution remains NOT_EXECUTED, evidence insufficient. |
| test_not_executed / test_preserved_linked_and_counted_once | Same contract change; retain linkage and one-count assertions. |
| test_not_executed / test_result_without_finding_is_visible_in_count | Count unlinked execution outcomes separately from security findings. |
| test_probe_executor / test_actual_complete_chain_confirmed_and_raw_preserved | Obsolete free-form rule authority; keep raw-preservation and trace assertions. |
| test_probe_executor / test_authenticated_receipt_replaces_stale_not_executed_draft | Obsolete positive verdict without inference metadata; keep receipt precedence. |
| test_probe_executor / test_cross_flow_origin_binds_to_mutation_receipt | Obsolete positive verdict without inference metadata; keep attribution. |
| test_probe_executor / test_deduplication_preserves_execution_attempt_counts | Obsolete 'controls held' policy count; retain all receipt/attempt counts. |
| test_probe_executor / test_legacy_cross_flow_report_links_only_unique_signed_receipt | Obsolete verdict name; retain unique-link-only assertion. |
| test_probe_executor / test_pending_null_draft_uses_receipt_without_changing_raw_report | Obsolete rule authority; keep stale-draft precedence and raw preservation. |
| test_probe_executor / test_report_cannot_spoof_observed_ui_provenance | Obsolete verdict name; spoofed provenance still cannot confirm. |
| test_startup_prompt / test_actual_rule_contract_is_embedded_in_analysis_instructions | Obsolete business-policy wording; assert the AI_INFERRED contract and business-readable reporting instructions. |

## All 8 baseline errors

| Test module / method | Classification and correction |
| --- | --- |
| test_application_identity / test_backend_issues_and_validates_identity | Obsolete setup: issuance now intentionally returns None. Assert disabled issuance and no file creation. |
| test_application_identity / test_tampered_manifest_fails_closed | Obsolete setup: no new manifest is issued. Existing forged bytes remain untrusted and unchanged. |
| test_application_identity_smoke / test_real_http_handshake_accepts_match_and_rejects_same_origin_mismatch | Obsolete setup: HTTP self-identification is not a confirmation authority. |
| test_application_identity_smoke / test_wrong_or_missing_version_cannot_inherit_rule | Obsolete setup: no identity tuple to unpack; no registry rule applies. |
| test_business_requirements / test_earlier_evidence_is_explicitly_retrospective | Obsolete registry fixture: disabled registry is empty, not indexable. |
| test_business_requirements / test_registry_records_product_versioned_requirement | Same obsolete registry fixture; explicitly assert empty registry. |
| test_northstar_coverage_integration module | Broken import of removed production render_probe. Use an explicit test-only plan fixture with the real generic compiler and transport. No product templates restored to production. |
| test_report_issue_groups module | Same broken import; test generic grouping and attribution without removed templates. |

## Real regressions and additional infrastructure defects

- A per-probe error stopped independent tests; preflight rejected the whole queue;
  completion text could claim success while probes were pending. Fixed with signed
  individual rejections, continued execution and derived terminal status.
- Unscripted draft F-007 inflated six planned probes to seven findings. Preserve
  it as unexecuted coverage, without inventing a receipt or a security verdict.
- Pending-state detection missed a single response envelope. Test the actual
  captured child state; do not call a pending payout a completed negative check.
- Unique-issue grouping depended on a removed product registry. Group only within
  a report using validated inference, origin, resource, chain and predicate;
  retain every finding variant and receipt.
- Restored integration tests exposed Windows default-encoding failures when
  writing generated source. Use explicit UTF-8 test artifacts.
- Sandbox Node EPERM prevented temporary fixture startup. Run integration tests
  with the required filesystem permission; do not skip them or weaken assertions.

New regression tests additionally check one numeric-path regeneration attempt,
failed regeneration without HTTP, independent-probe continuation, authenticated
preflight decisions, tamper rejection, idempotent report reload, and truthful
terminal counts. Existing strict signature, trace and invariant tests remain.

Frontend checks: 31 tests, OK, and production build passes. Legacy UI assertions
for product approval, currency-specific prose and 'control held' were updated to
the generic inference contract; exact HTTP request, state, count and CWE checks
remain. A real UI grouping defect was also fixed: ReportView now consumes backend
vulnerability groups instead of rebuilding one group for every reproduction.
