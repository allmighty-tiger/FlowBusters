# Security and Responsible Use

## Authorized testing only

FlowBusters is an offensive-security research project intended exclusively for
authorized testing. Use it only against systems you own or have explicit,
written permission to test.

The operator is responsible for obtaining permission, defining scope,
protecting captured data, reviewing generated probes, and complying with all
applicable laws, contracts, rules of engagement, and organizational policies.

## Trust boundary

FlowBusters executes AI-generated Python probe scripts against a live target.
Those scripts can send state-changing requests, replay authenticated sessions,
modify application data, and trigger real business operations.

This MVP must be treated as an **untrusted code-execution system**:

- Claude Code currently runs with `--dangerously-skip-permissions`.
- Generated scripts run with the permissions of the local FlowBusters process.
- Probe restrictions are instruction-level, not an operating-system sandbox.
- `scope.json` is a guardrail, not a hard security boundary.
- Model errors, prompt injection, or malicious target responses may influence
  generated behavior.

## Required precautions

Before an assessment:

1. Obtain written authorization for the exact target and activity.
2. Use a QA, staging, or intentionally vulnerable environment.
3. Use disposable accounts and synthetic data.
4. Run FlowBusters in a disposable VM, container, or isolated workstation.
5. Restrict network access to the approved target and required API services.
6. Use least-privileged credentials.
7. Review the target URL, `scope.json`, and generated probe scripts.
8. Ensure the target can be restored if state changes occur.

After an assessment:

- Treat HAR files, cookies, tokens, generated scripts, and reports as sensitive.
- Redact secrets and personal data before sharing artifacts.
- Revoke or rotate temporary credentials and sessions.
- Delete artifacts that are no longer required.
- Confirm that test data and state changes were cleaned up.

## Scope configuration

When `scope.json` is present, the Captain checks the target against:

- `allowed_domains`
- `allowed_paths_prefix`
- `block_production`

This check can reduce accidental misuse, but it does not provide hard isolation.
It can fail, be misconfigured, or be bypassed by behavior outside the intended
agent workflow.

Enforce scope independently through network segmentation, firewall or proxy
rules, dedicated credentials, and target-side access controls.

## Prohibited use

Do not use FlowBusters:

- without explicit authorization
- against production merely because configuration permits it
- from a workstation with unrestricted access to corporate systems
- with real customer, employee, payment, health, or other sensitive data
- as an unattended autonomous scanner
- when generated code cannot be inspected or the target cannot be restored

Stopping the backend does not guarantee that external side effects have been
reversed. Assume that any executed probe may have changed the target.

## Reporting a security issue

If you discover a security issue in FlowBusters itself, do not include
credentials, tokens, HAR files, or other sensitive artifacts in a public issue.
Provide a minimal reproducible description and redact all sensitive data before
sharing it.
