/**
 * The Pi-backed task analyzer.
 *
 * The model is supplied by the caller, already resolved. This module never
 * chooses one.
 */

import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { extractJson, validateFingerprint, type TaskFingerprint } from './fingerprint.ts';
import { createSession, type PiRuntime, type ResolvedAnalyzer } from './pi.ts';

const HERE = dirname(fileURLToPath(import.meta.url));
const PROMPT_PATH = join(HERE, '..', 'prompts', 'task-analyzer.md');

export type AnalyzerResult = {
  fingerprint: TaskFingerprint;
  analyzer: {
    provider: string;
    model: string;
    thinking_level: string;
    confidence: number;
    repaired: boolean;
    duration_ms: number;
  };
};

export class AnalyzerFailure extends Error {
  readonly detail: string;
  constructor(message: string, detail = '') {
    super(message);
    this.name = 'AnalyzerFailure';
    this.detail = detail;
  }
}

let cachedPrompt: string | null = null;

/** The analyzer prompt, with the canonical task-family list interpolated. */
export function buildSystemPrompt(families: Array<{ key: string; definition: string }>): string {
  if (cachedPrompt === null) cachedPrompt = readFileSync(PROMPT_PATH, 'utf8');
  const block = families
    .map((f) => `- \`${f.key}\`: ${f.definition.replace(/\s+/g, ' ').trim()}`)
    .join('\n');
  return cachedPrompt.replace('{{TASK_FAMILIES}}', block);
}

type AssistantLike = {
  role?: string;
  content?: unknown;
  stopReason?: string;
  errorMessage?: string;
};

function lastAssistant(messages: readonly unknown[]): AssistantLike | undefined {
  for (let i = messages.length - 1; i >= 0; i--) {
    const message = messages[i] as AssistantLike;
    if (message?.role === 'assistant') return message;
  }
  return undefined;
}

function assistantText(message: AssistantLike | undefined): string {
  if (!message) return '';
  const content = message.content;
  if (typeof content === 'string') return content;
  if (!Array.isArray(content)) return '';
  return content
    .filter((block: { type?: string }) => block?.type === 'text')
    .map((block: { text?: string }) => block.text ?? '')
    .join('');
}

/**
 * Turn a failed turn into an error the user can act on.
 *
 * Pi reports provider failures on the message rather than by throwing, so
 * without this check an expired token surfaces as "the analyzer returned no
 * text", which tells nobody anything.
 */
function assertTurnSucceeded(message: AssistantLike | undefined): void {
  if (!message) {
    throw new AnalyzerFailure('The analyzer produced no response at all.');
  }
  if (message.stopReason === 'error') {
    const detail = message.errorMessage ?? 'no detail reported';
    if (/no api key|401|unauthor|expired|invalid.*key/i.test(detail)) {
      throw new AnalyzerFailure(
        'Pi could not authenticate with the configured analyzer provider.',
        `${detail}. Run "pi" in a terminal and use /login to re-authenticate, then restart the bridge or POST /reload.`,
      );
    }
    throw new AnalyzerFailure('The analyzer provider returned an error.', detail);
  }
  if (message.stopReason === 'aborted') {
    throw new AnalyzerFailure('The analysis was aborted before it finished.');
  }
  if (message.stopReason === 'length') {
    throw new AnalyzerFailure(
      'The analyzer hit its output limit before completing the JSON object.',
      'This usually means the analyzer model started explaining itself. Try a different analyzer model.',
    );
  }
}

/**
 * Classify one task with an explicitly resolved analyzer.
 *
 * On malformed output, exactly ONE repair attempt is made, feeding the
 * validation errors back to the model. If that also fails the call errors out.
 * Filling in plausible-looking values on the model's behalf would silently
 * poison every statistic downstream, so a loud failure is the correct outcome.
 */
export async function analyzeTask(
  runtime: PiRuntime,
  analyzer: ResolvedAnalyzer,
  task: string,
  families: Array<{ key: string; definition: string }>,
): Promise<AnalyzerResult> {
  const started = Date.now();
  const familyKeys = families.map((f) => f.key);
  const systemPrompt = buildSystemPrompt(families);

  const session = await createSession(runtime, analyzer, systemPrompt);
  let repaired = false;

  try {
    await session.prompt(buildUserPrompt(task));
    let message = lastAssistant(session.messages);
    assertTurnSucceeded(message);

    let result = tryParse(assistantText(message), familyKeys);

    if (!result.ok) {
      repaired = true;
      await session.prompt(
        [
          'Your previous response was rejected by the schema validator:',
          '',
          ...result.errors.map((e) => `- ${e}`),
          '',
          'Return ONLY the corrected JSON object. No prose, no markdown fences.',
          'Do not change your judgement of the task; only fix the format and the',
          'invalid values.',
        ].join('\n'),
      );
      message = lastAssistant(session.messages);
      assertTurnSucceeded(message);
      result = tryParse(assistantText(message), familyKeys);
    }

    if (!result.ok) {
      throw new AnalyzerFailure(
        'The analyzer could not produce a valid TaskFingerprint after one repair attempt.',
        result.errors.join('; '),
      );
    }

    return {
      fingerprint: result.value,
      analyzer: {
        provider: analyzer.provider,
        model: analyzer.modelId,
        thinking_level: analyzer.thinkingLevel,
        confidence: result.value.confidence,
        repaired,
        duration_ms: Date.now() - started,
      },
    };
  } finally {
    session.dispose();
  }
}

function buildUserPrompt(task: string): string {
  return [
    'Classify the following software-development task.',
    'Return only the TaskFingerprint JSON object.',
    '',
    '--- TASK BEGINS ---',
    task.trim(),
    '--- TASK ENDS ---',
  ].join('\n');
}

function tryParse(
  text: string,
  familyKeys: string[],
): { ok: true; value: TaskFingerprint } | { ok: false; errors: string[] } {
  if (!text.trim()) return { ok: false, errors: ['the analyzer returned no text'] };
  let parsed: unknown;
  try {
    parsed = extractJson(text);
  } catch (error) {
    return { ok: false, errors: [(error as Error).message] };
  }
  return validateFingerprint(parsed, familyKeys);
}
