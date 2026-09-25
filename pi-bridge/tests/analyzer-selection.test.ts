/**
 * Analyzer selection.
 *
 * These exist because of a real defect: the bridge used to pick "the first
 * authenticated provider whose credentials resolved", which classified tasks
 * with kimi-coding while interactive Pi was signed in to openai-codex. The
 * tests below pin the behaviour that replaced it.
 *
 * `resolveAnalyzer` is exercised against a fake registry and auth storage, so
 * the suite runs offline and spends no quota.
 */

import assert from 'node:assert/strict';
import { test } from 'node:test';

import {
  AnalyzerConfigError,
  bootConfig,
  isThinkingLevel,
  THINKING_LEVELS,
  withOverrides,
} from '../src/config.ts';
import { authenticatedAlternatives, resolveAnalyzer, type PiRuntime } from '../src/pi.ts';

/** Mirrors the shape of the user's machine: three providers, one configured. */
const MODELS = [
  { provider: 'github-copilot', id: 'claude-haiku-4.5' },
  { provider: 'github-copilot', id: 'claude-sonnet-4.5' },
  { provider: 'kimi-coding', id: 'kimi-for-coding' },
  { provider: 'openai-codex', id: 'gpt-5.4-mini' },
  { provider: 'openai-codex', id: 'gpt-5.5' },
];

function fakeRuntime(options: {
  resolvable?: string[];
  models?: Array<{ provider: string; id: string }>;
  throwOnAuth?: boolean;
}): PiRuntime {
  const models = options.models ?? MODELS;
  const resolvable = new Set(options.resolvable ?? ['kimi-coding', 'openai-codex']);
  return {
    authStorage: {
      async getApiKey(provider: string) {
        if (options.throwOnAuth) throw new Error('auth backend exploded');
        return resolvable.has(provider) ? 'token-'.padEnd(40, 'x') : undefined;
      },
      getAuthStatus(provider: string) {
        return { configured: models.some((m) => m.provider === provider) };
      },
    },
    registry: {
      find: (provider: string, id: string) =>
        models.find((m) => m.provider === provider && m.id === id),
      getAvailable: () => models,
    },
    agentDir: '/tmp/agent',
    cwd: '/tmp',
  } as unknown as PiRuntime;
}

const CONFIG = {
  provider: 'openai-codex',
  model: 'gpt-5.5',
  thinkingLevel: 'low' as const,
  source: { provider: 'env', model: 'env', thinkingLevel: 'env' },
};

// --- The reported defect ----------------------------------------------------

test('openai-codex/gpt-5.5 can be selected explicitly', async () => {
  const result = await resolveAnalyzer(fakeRuntime({}), CONFIG);
  assert.equal(result.ok, true);
  if (result.ok) {
    assert.equal(result.analyzer.provider, 'openai-codex');
    assert.equal(result.analyzer.modelId, 'gpt-5.5');
    assert.equal(result.analyzer.thinkingLevel, 'low');
  }
});

test('kimi is NOT selected merely because it is authenticated', async () => {
  // Both kimi and codex have usable credentials; kimi sorts earlier and used to
  // win the old "first provider that resolves" loop.
  const result = await resolveAnalyzer(
    fakeRuntime({ resolvable: ['kimi-coding', 'openai-codex'] }),
    CONFIG,
  );
  assert.equal(result.ok, true);
  if (result.ok) assert.notEqual(result.analyzer.provider, 'kimi-coding');
});

test('an unusable configured provider does not fall through to kimi', async () => {
  // Only kimi can produce a token. The old code would have selected it.
  const result = await resolveAnalyzer(fakeRuntime({ resolvable: ['kimi-coding'] }), CONFIG);
  assert.equal(result.ok, false);
  if (!result.ok) {
    assert.equal(result.failure.code, 'not_authenticated');
    assert.match(result.failure.message, /openai-codex/);
    // It offers kimi as a CHOICE, but does not take it.
    const providers = result.failure.alternatives.map((a) => a.provider);
    assert.ok(providers.includes('kimi-coding'));
  }
});

// --- Failing clearly rather than silently -----------------------------------

test('an unknown model fails with a configuration error, not a substitution', async () => {
  const result = await resolveAnalyzer(fakeRuntime({}), { ...CONFIG, model: 'gpt-9.9' });
  assert.equal(result.ok, false);
  if (!result.ok) {
    assert.equal(result.failure.code, 'model_not_found');
    assert.match(result.failure.message, /gpt-9\.9/);
    assert.match(result.failure.detail, /pi --list-models/);
  }
});

test('an unknown provider fails with a configuration error', async () => {
  const result = await resolveAnalyzer(fakeRuntime({}), { ...CONFIG, provider: 'made-up' });
  assert.equal(result.ok, false);
  if (!result.ok) assert.equal(result.failure.code, 'model_not_found');
});

test('a failure lists authenticated alternatives so the user can choose', async () => {
  const result = await resolveAnalyzer(fakeRuntime({ resolvable: [] }), CONFIG);
  assert.equal(result.ok, false);
  if (!result.ok) {
    assert.ok(result.failure.alternatives.length > 0);
    const codex = result.failure.alternatives.find((a) => a.provider === 'openai-codex');
    assert.ok(codex?.models.includes('gpt-5.5'));
  }
});

test('a credential check that throws is reported, not swallowed', async () => {
  const result = await resolveAnalyzer(fakeRuntime({ throwOnAuth: true }), CONFIG);
  assert.equal(result.ok, false);
  if (!result.ok) {
    assert.equal(result.failure.code, 'auth_check_failed');
    assert.match(result.failure.detail, /exploded/);
  }
});

test('a provider with stored but unusable credentials says so specifically', async () => {
  const result = await resolveAnalyzer(fakeRuntime({ resolvable: [] }), CONFIG);
  assert.equal(result.ok, false);
  if (!result.ok) assert.match(result.failure.message, /could not obtain a usable token/);
});

test('alternatives are listed for choosing from, grouped by provider', () => {
  const alternatives = authenticatedAlternatives(fakeRuntime({}));
  assert.deepEqual(
    alternatives.map((a) => a.provider),
    ['github-copilot', 'kimi-coding', 'openai-codex'],
  );
});

// --- Thinking level ---------------------------------------------------------

test('the configured thinking level reaches the resolved analyzer', async () => {
  for (const level of THINKING_LEVELS) {
    const result = await resolveAnalyzer(fakeRuntime({}), { ...CONFIG, thinkingLevel: level });
    assert.equal(result.ok, true);
    if (result.ok) assert.equal(result.analyzer.thinkingLevel, level);
  }
});

test('only Pi thinking levels are accepted', () => {
  assert.equal(isThinkingLevel('low'), true);
  assert.equal(isThinkingLevel('xhigh'), true);
  // `max` and `ultra` belong to the ROUTER's effort vocabulary, not Pi's.
  assert.equal(isThinkingLevel('max'), false);
  assert.equal(isThinkingLevel('ultra'), false);
  assert.equal(isThinkingLevel('off'), false);
});

// --- Boot configuration -----------------------------------------------------

test('boot config defaults to openai-codex / gpt-5.5 / low', () => {
  const config = bootConfig({} as NodeJS.ProcessEnv);
  assert.equal(config.provider, 'openai-codex');
  assert.equal(config.model, 'gpt-5.5');
  assert.equal(config.thinkingLevel, 'low');
  assert.equal(config.source.provider, 'default');
});

test('the environment overrides the defaults and records that it did', () => {
  const config = bootConfig({
    PI_PROVIDER: 'anthropic',
    PI_MODEL: 'claude-haiku-4-5',
    PI_THINKING_LEVEL: 'medium',
  } as NodeJS.ProcessEnv);
  assert.equal(config.provider, 'anthropic');
  assert.equal(config.model, 'claude-haiku-4-5');
  assert.equal(config.thinkingLevel, 'medium');
  assert.equal(config.source.provider, 'env:PI_PROVIDER');
});

test('an invalid PI_THINKING_LEVEL fails loudly at boot', () => {
  assert.throws(
    () => bootConfig({ PI_THINKING_LEVEL: 'max' } as NodeJS.ProcessEnv),
    AnalyzerConfigError,
  );
});

// --- Per-request overrides (what the Settings page drives) ------------------

test('a request override replaces the boot configuration', () => {
  const config = withOverrides(CONFIG, {
    provider: 'openai-codex',
    model: 'gpt-5.4-mini',
    thinking_level: 'high',
  });
  assert.equal(config.model, 'gpt-5.4-mini');
  assert.equal(config.thinkingLevel, 'high');
  assert.equal(config.source.model, 'request');
});

test('omitted overrides keep the boot configuration', () => {
  const config = withOverrides(CONFIG, {});
  assert.deepEqual(
    [config.provider, config.model, config.thinkingLevel],
    ['openai-codex', 'gpt-5.5', 'low'],
  );
  assert.equal(config.source.provider, 'env');
});

test('the thinking level can be pinned on its own', () => {
  const config = withOverrides(CONFIG, { thinking_level: 'xhigh' });
  assert.equal(config.provider, 'openai-codex');
  assert.equal(config.model, 'gpt-5.5');
  assert.equal(config.thinkingLevel, 'xhigh');
});

test('switching provider without a model is refused rather than mismatched', () => {
  // Pairing a new provider with the old provider's model id cannot resolve, so
  // it is caught here instead of failing confusingly later.
  assert.throws(() => withOverrides(CONFIG, { provider: 'anthropic' }), /without a model/);
});

test('an invalid override thinking level is refused, not ignored', () => {
  assert.throws(() => withOverrides(CONFIG, { thinking_level: 'max' }), AnalyzerConfigError);
});
