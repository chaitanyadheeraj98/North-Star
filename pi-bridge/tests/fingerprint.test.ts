import assert from 'node:assert/strict';
import { test } from 'node:test';

import { parseTaskFamilies } from '../src/families.ts';
import { extractJson, validateFingerprint } from '../src/fingerprint.ts';

const FAMILIES = ['ui_cosmetic', 'concurrency', 'backend_data_integrity'];

const VALID = {
  schema_version: '1.0',
  task_family: 'concurrency',
  task_type: 'bug_fix',
  complexity: 0.9,
  ambiguity: 0.3,
  requirements_clarity: 0.8,
  regression_risk: 0.85,
  architecture_reasoning: 0.7,
  database_reasoning: 0.6,
  concurrency_risk: 0.95,
  security_risk: 0.5,
  repository_understanding: 0.8,
  scope: 'multi_service',
  estimated_files: 7,
  frontend: false,
  backend: true,
  database: true,
  infrastructure: false,
  tests_required: 'high',
  confidence: 0.84,
};

test('extractJson reads a bare object', () => {
  assert.deepEqual(extractJson('{"a":1}'), { a: 1 });
});

test('extractJson strips markdown fences', () => {
  assert.deepEqual(extractJson('```json\n{"a":1}\n```'), { a: 1 });
});

test('extractJson survives prose on both sides', () => {
  assert.deepEqual(
    extractJson('Here is the classification:\n{"a":1}\nLet me know if you need more.'),
    { a: 1 },
  );
});

test('extractJson finds the matching brace with nested objects and braces in strings', () => {
  const text = 'noise {"a":{"b":2},"c":"} not the end {"} trailing';
  assert.deepEqual(extractJson(text), { a: { b: 2 }, c: '} not the end {' });
});

test('extractJson rejects a response with no object', () => {
  assert.throws(() => extractJson('I cannot classify this task.'), /no JSON object/);
});

test('extractJson rejects an empty response', () => {
  assert.throws(() => extractJson('   '), /empty response/);
});

test('validateFingerprint accepts a well-formed fingerprint', () => {
  const result = validateFingerprint(VALID, FAMILIES);
  assert.equal(result.ok, true);
  if (result.ok) {
    assert.equal(result.value.task_family, 'concurrency');
    assert.equal(result.value.estimated_files, 7);
  }
});

test('validateFingerprint rejects a score outside 0..1', () => {
  const result = validateFingerprint({ ...VALID, complexity: 1.4 }, FAMILIES);
  assert.equal(result.ok, false);
  if (!result.ok) assert.match(result.errors.join(' '), /complexity must be between/);
});

test('validateFingerprint rejects a score sent as a string', () => {
  const result = validateFingerprint({ ...VALID, ambiguity: '0.3' }, FAMILIES);
  assert.equal(result.ok, false);
  if (!result.ok) assert.match(result.errors.join(' '), /ambiguity must be a number/);
});

test('validateFingerprint rejects an unknown task family', () => {
  const result = validateFingerprint({ ...VALID, task_family: 'vibes' }, FAMILIES);
  assert.equal(result.ok, false);
  if (!result.ok) assert.match(result.errors.join(' '), /not one of the allowed families/);
});

test('validateFingerprint rejects an unknown scope', () => {
  const result = validateFingerprint({ ...VALID, scope: 'galaxy' }, FAMILIES);
  assert.equal(result.ok, false);
  if (!result.ok) assert.match(result.errors.join(' '), /scope must be one of/);
});

test('validateFingerprint rejects a negative estimated_files', () => {
  const result = validateFingerprint({ ...VALID, estimated_files: -2 }, FAMILIES);
  assert.equal(result.ok, false);
});

test('validateFingerprint rejects an unsupported schema version', () => {
  const result = validateFingerprint({ ...VALID, schema_version: '2.0' }, FAMILIES);
  assert.equal(result.ok, false);
  if (!result.ok) assert.match(result.errors.join(' '), /schema_version/);
});

test('validateFingerprint normalises family casing and whitespace', () => {
  const result = validateFingerprint({ ...VALID, task_family: '  Concurrency ' }, FAMILIES);
  assert.equal(result.ok, true);
  if (result.ok) assert.equal(result.value.task_family, 'concurrency');
});

test('validateFingerprint reports every problem at once, for the repair prompt', () => {
  const result = validateFingerprint(
    { ...VALID, complexity: 5, scope: 'nope', tests_required: 'extreme' },
    FAMILIES,
  );
  assert.equal(result.ok, false);
  if (!result.ok) assert.ok(result.errors.length >= 3);
});

test('parseTaskFamilies reads folded definitions from the backend config', () => {
  const yaml = [
    'schema_version: "1.0"',
    '',
    'families:',
    '',
    '  ui_cosmetic:',
    '    label: "UI Cosmetic"',
    '    definition: >',
    '      Purely visual changes with no behavioural',
    '      consequence.',
    '    required_reliability_adjustment: -0.020',
    '',
    '  concurrency:',
    '    label: "Concurrency"',
    '    definition: "Races, locking, idempotency."',
    '    required_reliability_adjustment: 0.020',
  ].join('\n');

  const families = parseTaskFamilies(yaml);
  assert.equal(families.length, 2);
  assert.equal(families[0].key, 'ui_cosmetic');
  assert.equal(families[0].definition, 'Purely visual changes with no behavioural consequence.');
  assert.equal(families[1].definition, 'Races, locking, idempotency.');
});
