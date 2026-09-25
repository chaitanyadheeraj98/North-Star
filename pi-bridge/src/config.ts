/**
 * Analyzer configuration.
 *
 * The analyzer provider, model and thinking level are CHOSEN, never inferred.
 *
 * The previous implementation picked "the first authenticated provider whose
 * credentials happened to resolve", which is how this bridge ended up
 * classifying tasks with kimi-coding while interactive Pi was signed in to
 * openai-codex. Those are different decisions made by different code, and only
 * one of them was ever visible to the user. An analyzer that silently changes
 * which model reads your tasks is not a defensible design: the fingerprint
 * drives every routing decision downstream, so a change of analyzer is a change
 * of routing behaviour.
 *
 * Precedence, lowest first:
 *
 *   1. Built-in defaults below
 *   2. Repository .env / process environment (boot-time defaults)
 *   3. Per-request overrides sent by the backend (what the Settings page edits)
 *
 * Nothing here touches credentials. Authentication stays entirely inside Pi's
 * own store at ~/.pi/agent/auth.json.
 */

import { existsSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = join(HERE, '..', '..');

/**
 * Thinking levels the installed Pi SDK accepts.
 *
 * Verified against @mariozechner/pi-ai 0.73.x:
 *   export type ThinkingLevel = "minimal" | "low" | "medium" | "high" | "xhigh"
 *
 * Note this is Pi's vocabulary for the ANALYZER, and is deliberately separate
 * from the router's own effort vocabulary (none/low/medium/high/xhigh/max/
 * ultra) which describes what the EXECUTING agent should use. They overlap in
 * spelling and mean different things; conflating them would let a change to
 * routing policy silently re-price task classification.
 */
export const THINKING_LEVELS = ['minimal', 'low', 'medium', 'high', 'xhigh'] as const;
export type ThinkingLevel = (typeof THINKING_LEVELS)[number];

export type AnalyzerConfig = {
  provider: string;
  model: string;
  thinkingLevel: ThinkingLevel;
  /** Where each value came from, for /health and for debugging. */
  source: { provider: string; model: string; thinkingLevel: string };
};

/**
 * Built-in defaults.
 *
 * Classification is a small, structured judgement. It does not need the
 * strongest model available, and spending premium quota to decide how to spend
 * quota would defeat the point of the tool. These are overridable in .env.
 */
const BUILT_IN = {
  provider: 'openai-codex',
  model: 'gpt-5.5',
  thinkingLevel: 'low' as ThinkingLevel,
};

/**
 * Load the repository .env so the bridge and the containers read one file.
 *
 * `process.loadEnvFile` is built into Node (20.12+/21.7+); no dotenv
 * dependency. Values already present in the environment win, so
 * `PI_MODEL=x npm start` still overrides the file.
 */
export function loadDotEnv(): string | undefined {
  const candidates = [
    process.env.ADAPTIVE_ROUTER_ENV_FILE,
    join(REPO_ROOT, '.env'),
  ].filter(Boolean) as string[];

  for (const candidate of candidates) {
    const path = resolve(candidate);
    if (!existsSync(path)) continue;
    const before = { ...process.env };
    try {
      process.loadEnvFile(path);
      // Restore anything that was explicitly set before the file was read:
      // an inline override must beat the file.
      for (const [key, value] of Object.entries(before)) {
        if (value !== undefined) process.env[key] = value;
      }
      return path;
    } catch {
      // A malformed .env should not stop the bridge from starting on defaults.
      return undefined;
    }
  }
  return undefined;
}

export function isThinkingLevel(value: unknown): value is ThinkingLevel {
  return typeof value === 'string' && (THINKING_LEVELS as readonly string[]).includes(value);
}

/** Boot configuration: built-in defaults overlaid with the environment. */
export function bootConfig(env: NodeJS.ProcessEnv = process.env): AnalyzerConfig {
  const provider = nonEmpty(env.PI_PROVIDER);
  const model = nonEmpty(env.PI_MODEL);
  const thinking = nonEmpty(env.PI_THINKING_LEVEL);

  if (thinking && !isThinkingLevel(thinking)) {
    throw new AnalyzerConfigError(
      `PI_THINKING_LEVEL is "${thinking}", which is not a Pi thinking level.`,
      `Valid levels: ${THINKING_LEVELS.join(', ')}.`,
    );
  }

  return {
    provider: provider ?? BUILT_IN.provider,
    model: model ?? BUILT_IN.model,
    thinkingLevel: (thinking as ThinkingLevel) ?? BUILT_IN.thinkingLevel,
    source: {
      provider: provider ? 'env:PI_PROVIDER' : 'default',
      model: model ? 'env:PI_MODEL' : 'default',
      thinkingLevel: thinking ? 'env:PI_THINKING_LEVEL' : 'default',
    },
  };
}

/**
 * Apply per-request overrides from the backend.
 *
 * Partial overrides are allowed: the Settings page may pin a provider and model
 * while leaving the thinking level at the boot default. An override that is
 * present but invalid is an error, never a silent ignore.
 */
export function withOverrides(
  base: AnalyzerConfig,
  overrides: { provider?: unknown; model?: unknown; thinking_level?: unknown },
): AnalyzerConfig {
  const provider = nonEmpty(overrides.provider);
  const model = nonEmpty(overrides.model);
  const thinking = nonEmpty(overrides.thinking_level);

  if (overrides.provider !== undefined && overrides.provider !== null && !provider) {
    throw new AnalyzerConfigError('The requested analyzer provider is empty.');
  }
  if (overrides.model !== undefined && overrides.model !== null && !model) {
    throw new AnalyzerConfigError('The requested analyzer model is empty.');
  }
  if (thinking && !isThinkingLevel(thinking)) {
    throw new AnalyzerConfigError(
      `"${thinking}" is not a Pi thinking level.`,
      `Valid levels: ${THINKING_LEVELS.join(', ')}.`,
    );
  }

  // Naming a provider without a model would otherwise pair a new provider with
  // the previous provider's model id, which cannot resolve.
  if (provider && !model && provider !== base.provider) {
    throw new AnalyzerConfigError(
      `Analyzer provider "${provider}" was requested without a model.`,
      `The configured model "${base.model}" belongs to "${base.provider}". ` +
        'Specify both, or neither.',
    );
  }

  return {
    provider: provider ?? base.provider,
    model: model ?? base.model,
    thinkingLevel: (thinking as ThinkingLevel) ?? base.thinkingLevel,
    source: {
      provider: provider ? 'request' : base.source.provider,
      model: model ? 'request' : base.source.model,
      thinkingLevel: thinking ? 'request' : base.source.thinkingLevel,
    },
  };
}

export class AnalyzerConfigError extends Error {
  readonly detail: string;
  constructor(message: string, detail = '') {
    super(message);
    this.name = 'AnalyzerConfigError';
    this.detail = detail;
  }
}

function nonEmpty(value: unknown): string | undefined {
  if (typeof value !== 'string') return undefined;
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : undefined;
}
