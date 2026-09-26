/**
 * Capability research: validation and prompt construction.
 *
 * Mirrors analyzer-selection.test.ts's style - pure functions only, no
 * session or HTTP mocking, so the suite runs offline and spends no quota.
 */

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';

import { CAPABILITY_DIMENSIONS, validateResearch } from '../src/capability-research.ts';
import { buildResearchUserPrompt } from '../src/researcher.ts';

const HERE = dirname(fileURLToPath(import.meta.url));

function goodPriors(): Record<string, number> {
  return Object.fromEntries(CAPABILITY_DIMENSIONS.map((dim) => [dim, 0.8]));
}

// --- validateResearch --------------------------------------------------------

test('a fully valid response with one candidate is accepted', () => {
  const raw = {
    capability_priors: { 'codex/gpt_6_sol': goodPriors() },
    claude_reference_md: null,
    codex_reference_md: '# CodexLLM.md',
  };
  const result = validateResearch(raw, ['codex/gpt_6_sol'], { claude: false, codex: true });
  assert.equal(result.ok, true);
  if (result.ok) {
    assert.deepEqual(Object.keys(result.value.capability_priors), ['codex/gpt_6_sol']);
    assert.equal(result.value.codex_reference_md, '# CodexLLM.md');
  }
});

test('a missing candidate entry is rejected', () => {
  const raw = { capability_priors: {}, claude_reference_md: null, codex_reference_md: 'x' };
  const result = validateResearch(raw, ['codex/gpt_6_sol'], { claude: false, codex: true });
  assert.equal(result.ok, false);
  if (!result.ok) assert.ok(result.errors.some((e) => e.includes('missing candidate "codex/gpt_6_sol"')));
});

test('an entry for a model that was not a candidate is rejected', () => {
  const raw = {
    capability_priors: { 'codex/gpt_6_sol': goodPriors(), 'codex/uninvited': goodPriors() },
    claude_reference_md: null,
    codex_reference_md: 'x',
  };
  const result = validateResearch(raw, ['codex/gpt_6_sol'], { claude: false, codex: true });
  assert.equal(result.ok, false);
  if (!result.ok) assert.ok(result.errors.some((e) => e.includes('"codex/uninvited"')));
});

test('an out-of-range dimension score is rejected', () => {
  const raw = {
    capability_priors: { 'codex/gpt_6_sol': { ...goodPriors(), coding: 1.5 } },
    claude_reference_md: null,
    codex_reference_md: 'x',
  };
  const result = validateResearch(raw, ['codex/gpt_6_sol'], { claude: false, codex: true });
  assert.equal(result.ok, false);
  if (!result.ok) assert.ok(result.errors.some((e) => e.includes('coding')));
});

test('a missing dimension is rejected', () => {
  const priors = goodPriors();
  delete (priors as Record<string, number>).security;
  const raw = {
    capability_priors: { 'codex/gpt_6_sol': priors },
    claude_reference_md: null,
    codex_reference_md: 'x',
  };
  const result = validateResearch(raw, ['codex/gpt_6_sol'], { claude: false, codex: true });
  assert.equal(result.ok, false);
  if (!result.ok) assert.ok(result.errors.some((e) => e.includes('security')));
});

test('a reference doc required for a requested provider but returned null is rejected', () => {
  const raw = {
    capability_priors: { 'claude/opus_5_5': goodPriors() },
    claude_reference_md: null,
    codex_reference_md: null,
  };
  const result = validateResearch(raw, ['claude/opus_5_5'], { claude: true, codex: false });
  assert.equal(result.ok, false);
  if (!result.ok) assert.ok(result.errors.some((e) => e.includes('claude_reference_md')));
});

test('a reference doc returned for a provider that was not requested is rejected', () => {
  const raw = {
    capability_priors: { 'codex/gpt_6_sol': goodPriors() },
    claude_reference_md: '# unexpected',
    codex_reference_md: 'x',
  };
  const result = validateResearch(raw, ['codex/gpt_6_sol'], { claude: false, codex: true });
  assert.equal(result.ok, false);
  if (!result.ok) assert.ok(result.errors.some((e) => e.includes('claude_reference_md')));
});

// --- buildResearchUserPrompt -------------------------------------------------

test('the prompt includes every candidate key', () => {
  const prompt = buildResearchUserPrompt({
    candidates: [
      {
        key: 'codex/gpt_6_sol',
        provider: 'codex',
        display_name: 'GPT-6-Sol',
        model_id: 'gpt-6-sol',
        source_pricing: { input_per_1m_usd: 3, output_per_1m_usd: 15 },
        supported_efforts: ['low', 'medium'],
      },
    ],
    registry_anchors: [],
  });
  assert.match(prompt, /codex\/gpt_6_sol/);
  assert.match(prompt, /CANDIDATES TO SCORE/);
});

test('claude and codex sections are omitted when no data is given for them', () => {
  const prompt = buildResearchUserPrompt({
    candidates: [
      {
        key: 'codex/gpt_6_sol',
        provider: 'codex',
        display_name: 'GPT-6-Sol',
        model_id: 'gpt-6-sol',
        source_pricing: {},
        supported_efforts: [],
      },
    ],
    registry_anchors: [],
  });
  assert.doesNotMatch(prompt, /OFFICIAL CLAUDE DOCS/);
  assert.doesNotMatch(prompt, /CURRENT reference\/ClaudeLLM\.md/);
  assert.doesNotMatch(prompt, /CURRENT reference\/CodexLLM\.md/);
});

test('anchors and reference docs are included when supplied', () => {
  const prompt = buildResearchUserPrompt({
    candidates: [
      {
        key: 'claude/opus_5_5',
        provider: 'claude',
        display_name: 'Claude Opus 5.5',
        model_id: 'claude-opus-5-5',
        source_pricing: { input_per_1m_usd: 4, output_per_1m_usd: 20 },
        supported_efforts: ['low', 'medium', 'high'],
      },
    ],
    registry_anchors: [
      {
        key: 'claude/opus_5',
        provider: 'claude',
        display_name: 'Opus 5',
        relative_model_burn: 2.5,
        source_pricing: { input_per_1m_usd: 5, output_per_1m_usd: 25 },
        capability_priors: { coding: 0.95 },
      },
    ],
    claude_docs_text: 'raw docs text',
    claude_reference_md: '# ClaudeLLM.md current',
  });
  assert.match(prompt, /CALIBRATION ANCHORS/);
  assert.match(prompt, /claude\/opus_5/);
  assert.match(prompt, /OFFICIAL CLAUDE DOCS/);
  assert.match(prompt, /raw docs text/);
  assert.match(prompt, /CURRENT reference\/ClaudeLLM\.md/);
  assert.match(prompt, /ClaudeLLM\.md current/);
});

// --- Prompt file regression guard -------------------------------------------

test('the researcher prompt file mentions every capability dimension', () => {
  const promptPath = join(HERE, '..', 'prompts', 'model-researcher.md');
  const text = readFileSync(promptPath, 'utf8');
  for (const dim of CAPABILITY_DIMENSIONS) {
    assert.ok(text.includes(dim), `prompt is missing dimension "${dim}"`);
  }
});
