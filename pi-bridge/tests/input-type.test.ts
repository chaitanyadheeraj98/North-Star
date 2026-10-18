/**
 * Input types: the user says whether the text is a task, a spec or a plan, and
 * the analyzer is told so. Offline: only the prompt text is checked.
 */

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';

import { buildUserPrompt, INPUT_TYPES, isInputType } from '../src/analyzer.ts';

test('a task keeps the original prompt wording', () => {
  const prompt = buildUserPrompt('  Fix the typo  ');
  assert.match(prompt, /^Classify the following software-development task\./);
  assert.ok(prompt.includes('--- TASK BEGINS ---\nFix the typo\n--- TASK ENDS ---'));
});

test('a plan is labelled as a plan, start and end', () => {
  const prompt = buildUserPrompt('1. Do it', 'plan');
  assert.match(prompt, /an IMPLEMENTATION PLAN/);
  assert.ok(prompt.includes('--- IMPLEMENTATION PLAN BEGINS ---\n1. Do it\n--- IMPLEMENTATION PLAN ENDS ---'));
  assert.ok(!prompt.includes('--- TASK BEGINS ---'));
});

test('a spec is labelled as a spec', () => {
  const prompt = buildUserPrompt('Goal: x', 'spec');
  assert.match(prompt, /a SPEC/);
  assert.ok(prompt.includes('--- SPEC BEGINS ---'));
});

test('only the three known input types are accepted', () => {
  assert.deepEqual([...INPUT_TYPES], ['task', 'spec', 'plan']);
  for (const ok of INPUT_TYPES) assert.ok(isInputType(ok));
  for (const bad of ['Plan', 'code', '', null, undefined, 3]) assert.ok(!isInputType(bad));
});

test('the system prompt explains every input type the user prompt can name', () => {
  const system = readFileSync(new URL('../prompts/task-analyzer.md', import.meta.url), 'utf8');
  assert.match(system, /## Input types/);
  for (const label of ['**TASK**', '**SPEC**', '**IMPLEMENTATION PLAN**']) {
    assert.ok(system.includes(label), `missing ${label}`);
  }
  assert.match(system, /CONDENSED/);
});
