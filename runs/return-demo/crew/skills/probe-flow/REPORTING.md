# Reporting observed behavior

The primary report reader is a business analyst. Write for the person who demonstrated the product. FlowBusters has no built-in
product rules, approved-rule registry, or product-specific test plans. The user
knows the business rules; the recording supplies the screen and API context.

For each test, explain:

1. Which recorded actions and screen details suggested the test.
2. Which changes to actions or input the test proposes.
3. Which condition will be checked and why it is worth reviewing.
4. What actually happened, using recorded values and request order.
5. What the user still needs to decide about business meaning and impact.

Before execution, label all outcomes as drafts. Do not invent approvals, claim
that a rule was provided by the owner, or claim an operation is forbidden merely
because its button disappeared. Screen observations support hypotheses.

After execution, use these meanings:

- CONFIRMED: a complete authenticated execution violated the explicitly recorded
  AI_INFERRED invariant. This is not a claim of business-owner approval.
- INSUFFICIENT_EVIDENCE: required source observations or workflow actions are missing.
- NOT_REPRODUCED: this test did not show the proposed condition. It does not prove
  that a business control works or that the application is secure.
- NEEDS_REVIEW: the test or available context cannot establish the proposed claim.
- CHECK_ERROR: the test could not be evaluated reliably.

Report queue status as COMPLETED, COMPLETED_WITH_ERRORS, or FAILED. Give planned,
executed, skipped (preflight rejected), failed, and pending probe counts separately.
An unexecuted hypothesis without a planned probe is a coverage note, not a finding.

Keep actual requests, responses, and calculations separate from interpretations.
Do not assume numeric values are currency. Do not invent financial loss, severity,
concurrency, authorization rules, or a root cause. Mark suggested business impact
and fixes as conditional on user review. Generic technical security findings must
still meet their own evidence checks.

Include ordered requests and exact bodies so the behavior can be repeated. Explain
missing steps and unsupported tests plainly. Read setup from the run scope; no
application-specific reset route is assumed. Do not call a recorded result
"signed" in user-facing prose; describe what the backend checked instead.
