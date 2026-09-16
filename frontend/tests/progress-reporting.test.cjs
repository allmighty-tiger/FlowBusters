const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const progressSource = fs.readFileSync(
  path.resolve(__dirname, '../src/pages/progress.tsx'), 'utf8');
const runnerSource = fs.readFileSync(
  path.resolve(__dirname, '../../backend/runtime/crew_runner.py'), 'utf8');

test('recorded-flow progress keeps the existing recording steps and instructions', () => {
  for (const text of [
    "label: 'Record'", "detail: 'Capturing the workflow'",
    'Finish recording', 'Opening recording browser',
    'Starting analysis of validated recording',
  ]) assert.ok(progressSource.includes(text) || runnerSource.includes(text), text);
});

test('cross-flow progress uses its own mode, phases, and technical disclosure', () => {
  for (const text of [
    "'CROSS_FLOW'", 'Loading {source_count} source flows',
    'Authenticating source evidence', 'Building application state model',
    'Identifying cross-flow conflicts', 'Conflict hypotheses: {candidate_count} validated candidates',
    'Preparing {script_count} executable probes', 'Verifying signed evidence and invariants',
    'Building normalized final report', 'Final security findings (', 'Technical details',
  ]) assert.ok(progressSource.includes(text) || runnerSource.includes(text), text);
  assert.ok(progressSource.includes("runMode === 'RECORDED_FLOW' && progress['record'] === 'active'"));
});

test('cross-flow loading message has one backend emission site', () => {
  assert.equal((runnerSource.match(/f'Loading \{source_count\} source flows'/g) || []).length, 1);
});

test('completed indicator has accessible text and no svg placeholder element', () => {
  assert.ok(progressSource.includes('role="img" aria-label="Complete"'));
  assert.ok(progressSource.includes('<span aria-hidden="true">✓</span>'));
  assert.ok(!progressSource.includes('<svg'));
});

test('final progress wording separates findings, probes, and deduplicated controls', () => {
  for (const text of [
    'normalized):', 'Partial coverage:', 'Excluded setup-path executions:',
    'authenticated terminal receipts', 'Primary execution results:',
    'Deduplicated primary chains:', 'deduplicated_primary_chains',
  ]) assert.ok(runnerSource.includes(text), text);
});

test('cross-flow artifact messages suppress recording files and label drafts', () => {
  assert.ok(runnerSource.includes("if name in ('demo.json', 'recording.har', 'mutations')"));
  assert.ok(runnerSource.includes('Draft findings prepared; execution has not verified them'));
  assert.ok(runnerSource.includes('Draft remediation prepared; final verdicts are pending'));
  assert.ok(progressSource.includes('event.technical_detail'));
});
