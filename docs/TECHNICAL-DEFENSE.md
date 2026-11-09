# FlowBusters technical overview and Q&A

Code review date: September 19, 2026, branch `fixes`, including the reviewed
work in progress. This is the short guide for answering technical questions. The
longer [Lunch & Learn Q&A](LUNCH-AND-LEARN-QA.md) has more practice answers.

This guide is for a technical review, not the live demo. Start with the short
answer in each section. Use the longer answer only when someone asks for more
detail. AI helped build this product, but AI output is not accepted as proof. The
backend checks the test steps, records what happened, and decides the result by
using approved business rules.

## Thirty-second overview

> FlowBusters records an approved business process and asks AI to suggest small,
> focused tests. The backend then checks each test, runs it, records the real web
> traffic, signs the final record, checks the final response, applies an approved
> business rule, and prepares the report. Anything written by AI is only a
> suggestion until these checks are complete.

The two paths are:

```text
Recorded process:
browser recording -> checked record -> AI test ideas -> backend test run
-> signed run records -> business rule checks -> final report

Combined-process review:
signed copies of recorded processes -> AI test ideas -> checks against the recordings
-> clearly linked tests -> signed run records -> final report
```

Code: [`run_flowbusters`](../backend/runtime/orchestrator.py),
[`run_crew`](../backend/runtime/crew_runner.py),
[`execute_run`](../backend/runtime/probe_executor.py), and
[`normalize_report`](../backend/runtime/verification.py).

## Three things to keep separate

| Item | Example | Trusted record |
|---|---|---|
| What happened | The final GET response contains `totalReturned=130` and `originalAmount=100`. | The signed run record and matching web traffic |
| Business rule | For Northstar, `totalReturned <= originalAmount`. | The approved, versioned backend rule list |
| Final result | The amount returned was higher than the original amount, so the issue is `CONFIRMED`. | Backend checks made after the run record and business rule are verified |

Buttons, recorded web traffic, and AI-written notes can suggest a possible issue,
but they cannot create a financial rule. The backend finds the approved rule in
[`apply_backend_requirements`](../backend/runtime/business_requirements.py), and
the rule is stored in
[`business_requirements.json`](../backend/runtime/business_requirements.json).

## Questions about AI-assisted development

### “Was this application vibe coded?”

**Short answer:** Yes. AI was used heavily to build it faster. That makes strong
checks and careful review more important; it does not prove the product is
correct.

**More detail:** AI is used in two ways:

- AI helped write code and tests during development.
- AI reviews recordings and suggests possible issues and test steps while the
  product is running.

AI is not the final decision-maker in either case. The backend checks the format
and meaning of every test plan, runs only supported steps, records the traffic,
checks signed run records, and produces the result. Code review, automated tests,
and clear limits are still needed because backend code can also have bugs.

### “How do you know the checking code is correct?”

**Short answer:** We do not claim that it is mathematically proven. It is normal,
reviewable code supported by repeat tests, full-process tests, strict input
checks, and a second check of the reported numbers. These steps lower risk, but
they cannot promise that the code has no bugs.

**More detail:** Important facts are checked in more than one place. The test
reports a true-or-false result, and the backend reads the final response and
checks that answer again. Captured requests must match the signed traffic record.
The business rule comes from a separate backend list. If two records disagree,
the system reports an error instead of calling the test successful. See
[`validate_probe_output`](../backend/runtime/probe_executor.py) and
[`_claim_coverage_error`](../backend/runtime/verification.py).

### “Is AI really finding issues, or are they built in?”

**Short answer:** Both AI suggestions and planned backend tests are used. The
report shows where each test came from.

**More detail:** AI suggests tests from the recording. For the controlled
Northstar demo, saved backend test plans also make sure important known cases are
run. These plans contain test steps and checks, but they do not contain the
expected result. The actual result depends on what happens during the run. It
would be misleading to call a saved test plan an independent AI discovery. See
[`compile_plans`](../backend/runtime/generated_probes.py),
[`prepare_coverage_probes`](../backend/runtime/coverage_plans.py), and
[`coverage_plans.json`](../backend/runtime/coverage_plans.json).

### “Is the demonstrated $130 overpayment found only in combined-process mode?”

**Short answer:** No. The problem needs steps from more than one business
process, but a normal test run has also run the same price-change and completed-
refund steps.

**More detail:** Combined-process mode clearly links one test to actions from
separate recorded processes. A normal test can still call more than one API. The
current demo does not prove that AI could find this problem only in combined-
process mode. We can say that the combined test used actions from separate,
signed recordings and then ran those actions as one sequence.

### “Can the activity timeline prove which click caused a request?”

**Short answer:** No. It shows events in order, but it does not create an exact
link between every browser click and every web request.

**More detail:** The recorder takes small samples of readable screen text, joins
matching samples, and stores the screen timeline separately from the network
timeline. It can miss very fast screen changes. The timeline can support a test
idea, but the report should not say that it proves the exact request caused by
each click. See `compact_snapshot`, `add_ui_state`, `record`, and
`network_sequence` in [`recorder.py`](../backend/runtime/recorder.py).

## Questions about trust and records

### “What does a signed run record prove?”

**Short answer:** It shows that the run record has not changed since this
FlowBusters installation signed it with its local key.

It does not prove that an outside party checked the run, that the target server
is genuine, or that a person with control of both the files and signing key is
trustworthy. It also does not make a badly written test useful. Record signing
and test checks are separate parts of
[`probe_executor.py`](../backend/runtime/probe_executor.py).

### “Why use HMAC instead of public-key signing?”

**Short answer:** The current goal is to find local file changes inside one demo
installation, not to let outside parties confirm the records.

Public-key signing would make outside checking easier, but the signing key would
still need strong protection. It would not fix a wrong business rule or a badly
defined test.

### “How do you know the target is Northstar Market?”

**Short answer:** In this controlled local demo, FlowBusters calls a stable app
identity address and compares the response and allowed web address with a backend
list. It then signs the identity record.

**Limit:** This is a practical identity check for a controlled demo, not strong
proof of the running program's identity. A service does not receive the
Northstar rules just because it uses the same web address; it must also return
the saved identity and version. However, a harmful local service could copy that
response. See
[`discover_application_identity`](../backend/runtime/application_identity.py),
[`issue_identity`](../backend/runtime/application_identity.py), and
[`applications.json`](../backend/runtime/applications.json).

### “Can AI write `validated: true` or invent a business rule?”

**Short answer:** AI can write those words, but the backend does not accept them
as approval.

The backend gets the app identity, rule version, exact check, and record links
from backend-managed files. Recorded screen facts must point to saved copies that
cannot change during the run. See
[`validate_identity`](../backend/runtime/application_identity.py),
[`validate_rule_reference`](../backend/runtime/ui_provenance.py), and
[`apply_backend_requirements`](../backend/runtime/business_requirements.py).

### “Is the test runner a secure sandbox?”

**Short answer:** No. It is a test tool for approved, temporary target systems.

It limits supported web request methods and blocks some ways of starting other
programs, but it is not a full operating-system security wall. A production
version would need separate processes or containers, network limits, resource
limits, and stronger protection for signing keys. See
[`probe_capture.py`](../backend/runtime/probe_capture.py).

## Questions about report results

### “What does `CONFIRMED` mean?”

The signed test run finished, captured requests matched the recorded traffic,
the backend checked the expected business condition against the correct final
response, the check supported the stated issue, and an approved business rule
applied. It means that this exact case happened again. It does not prove every
sentence or risk rating in an early AI draft.

### “What does `NOT_REPRODUCED` mean?”

The exact signed test finished and the expected business condition was met. This
only describes that test. It does not prove that the API or application is safe.
A missing page, an unfinished refund, or an unrelated number limit cannot count
as a successful control. These checks are in
[`_claim_coverage_error`](../backend/runtime/verification.py).

### “What does `NEEDS_REVIEW` mean?”

The run gave useful information, but there is not enough support for a security
result. For example, the test check may not be supported, the approved rule may
be missing, some steps may be missing, or the written claim may be wider than
the test. These results stay visible because changing them to pass or fail would
give more confidence than the records support.

### “What does `CHECK_ERROR` mean?”

Something important failed, such as the run process, input checks, signature,
data reading, or traffic matching. The result cannot be trusted. A program exit
code of zero is not enough by itself.

### “Why can test-run counts differ from issue counts?”

Every signed run stays in the list of test results. Several similar runs can
support one issue. Extra data may support only part of an issue, and demo-focused
runs may remain visible without becoming security findings. The report counts
test plans, run records, findings, and grouped issues separately. Similar issues
are grouped by
[`_unique_vulnerabilities`](../backend/runtime/verification.py).

### “Does one grouped issue mean one proven code cause?”

No. It is a report group, not an analysis of the product's source code. The
grouping uses the signed app and rule details, business record IDs, the ordered
API steps, overlapping requests, and the business check. Different request data
can still appear in one group and remains available for review. Describe it as
“one product issue shown by several test runs,” not as proof that one code bug
caused every result.

### “Could grouping hide two real issues?”

Yes, if the grouping rules are wrong. That is why the system does not group only
by title, weakness number, or business record. It also uses the signed app and
rule details, action order, timing, and business check. The different records
remain available for review. Different action sequences or checks stay separate.
The system groups repeated results; it does not claim to know an unseen code
cause.

### “Does web traffic in sequence prove a timing bug?”

No. A timing claim needs proof that requests overlapped or that the test was set
up to run them at the same time. Requests that happen one after another may show
a problem with business steps or their order, but the report should not call it
a timing bug only because an AI draft used CWE-362.

## Questions about the process

### “Why are findings and fixes written before tests run?”

They are early AI drafts that save the test idea and its intended links. The UI
labels them as drafts. After the test, the checked run record replaces the old
draft status in the report shown by the app. The original files remain unchanged
for later review. Draft text cannot overrule the run record. See
[`reconcile`](../backend/runtime/probe_executor.py) and
[`normalize_report`](../backend/runtime/verification.py).

### “Why turn generated plans into backend tests instead of running any Python code?”

A plan gives a small, ordered set of actions and one check. The backend turns it
into a standard test with setup, before, actions, and after steps. It also gives
each web request its order number. This makes it easier to compare the stated
test with the test that actually ran. Older free-form scripts still need support
for now, but the goal is to use more limited, clearly defined plans.

### “What stops the system from checking the wrong final state?”

Each main test case has its own final GET request. The backend checks the result
against the final response matched to that case. A reset separates one case from
the next, so values are not carried across reset points. If records disagree,
the error names the case and values and stops the remaining tests.

### “How are combined-process sources checked?”

Recorded processes are copied into the combined run, given a file fingerprint,
and listed in a signed file. A test idea must point to matching actions that
change data in at least two signed recordings. Its exact request method, path,
body, and business check must match one runnable plan. Similar file names, file
order, or simply adding a second recording ID are not enough. See
[`prepare_inputs`](../backend/runtime/application_model.py),
[`validate_candidate`](../backend/runtime/candidate_correction.py), and
[`correct_candidates`](../backend/runtime/candidate_correction.py).

### “What happens if AI gives bad links to the recordings?”

The backend returns a clear error and gives AI a limited chance to correct the
idea. It then checks the updated idea again. It does not guess which recording
AI meant or make up a link. If no idea passes the checks, the combined-process
run stops before testing and does not report that the product is safe.

### “Can a test result create a product rule?”

No. A test only records what happened. The Northstar payout limit was approved
by the product owner and saved once in the backend rule list with its date, app
identity, rule-list version, and exact check. Older records can be checked against
that rule only when the report clearly says the rule was applied later.

## Design choices and limits

| Current choice | Benefit | Limit or future improvement |
|---|---|---|
| Local HMAC signatures | Simple way to find changed records | Outside parties cannot confirm them; protect keys better or use public-key signing |
| Controlled app identity address | Easy local demo setup without a secret | A harmful local service could copy it; use stronger service identity checks in production |
| Simple numeric `sum_lte` check | Consistent and easy to explain | Supports only a small set of checks; add new types carefully |
| Sampled readable screen text | Low-cost business context | May miss quick changes and cannot prove what every click caused |
| One active FastAPI review | Simple run process | Not designed for long-term jobs or many servers |
| AI plus saved backend tests | New ideas plus dependable demo coverage | The report must show which one created each test |
| Local test runner | Easy traffic recording | Not a full security sandbox; separate it before use by many customers |

## What this early version does not claim

- It does not prove that there are no security problems.
- It does not prove that AI tested everything.
- It does not decide a result from an HTTP success or error code alone.
- It does not create a business rule from a button, API field, or test output.
- It does not prove the cause inside the product code by looking only at outside
  behavior.
- It does not provide proof backed by outside parties or special hardware.
- It is not ready to run tests for many separate customers in production.
- It does not prove that combined-process review is the only way to find the
  demonstrated issue.

## Current display limit

The current `ReportView` has Northstar-specific text for confirmed issues. It
assumes a price change followed by a completed refund. That text is correct for
the saved combined-process issue, but it is too specific for every possible
amount-change or repeated-request issue. During review, rely on the ordered
request list, final GET response, and business rule check. Do not apply the same
explanation to a different action sequence. This is a display problem; it does
not mean the run record changed. See
[`ReportView.tsx`](../frontend/src/components/ReportView.tsx).

## If the live demo behaves differently

Say only what the process proved:

- **Input or traffic error:** “The test ran, but its records did not pass the
  checks, so I am not treating it as a security result.”
- **NEEDS_REVIEW:** “The run record is signed, but the written issue, business
  check, or approved rule link is incomplete.”
- **Combined-process idea not checked:** “The idea could not be linked to two
  signed recordings and one exact test plan, so it was not run.”
- **Different count:** “Test attempts, run results, findings, and grouped issues
  are different counts. Let me show how the run records connect to them.”

Do not call a failure harmless unless the exact business check was run and
passed.

## Where to start a code review

For a lead engineer reviewing how the system makes decisions, start here:

1. [`probe_executor.py`](../backend/runtime/probe_executor.py): runs tests,
   checks traffic and signed records, checks final response values, and updates
   results.
2. [`verification.py`](../backend/runtime/verification.py): checks whether the
   test supports the written issue, prepares the report, and groups similar
   issues.
3. [`business_requirements.py`](../backend/runtime/business_requirements.py) and
   [`business_requirements.json`](../backend/runtime/business_requirements.json):
   find the right approved business rule and exact check.
4. [`application_identity.py`](../backend/runtime/application_identity.py):
   finds, creates, and checks the local app identity record.
5. [`candidate_correction.py`](../backend/runtime/candidate_correction.py): links
   combined-process ideas to recorded actions and one exact test.
6. [`ui_provenance.py`](../backend/runtime/ui_provenance.py): checks the saved
   screen-state format and links screen facts to their records.

The full process, file list, and operating limits are in
[`ARCHITECTURE.md`](ARCHITECTURE.md). The spoken two-run demo is in
[`DEMO-CHEATSHEET.md`](DEMO-CHEATSHEET.md).
