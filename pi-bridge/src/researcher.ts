/**
 * The Pi-backed model-capability researcher.
 *
 * Structurally mirrors analyzer.ts: a static system prompt, a per-call user
 * prompt, one repair attempt on schema failure, then a loud failure. The
 * analyzer classifies tasks; this classifies models. Both run through the
 * same authenticated session (createSession in pi.ts) - no separate
 * credential path, same "no tools" security posture.
 */

import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { assistantText, lastAssistant, type AssistantLike } from './analyzer.ts';
import {
  validateResearch,
  type ResearchCandidate,
  type ResearchRequestBody,
  type ResearchResponseBody,
  type RegistryAnchor,
} from './capability-research.ts';
import { extractJson } from './fingerprint.ts';
import { createSession, type PiRuntime, type ResolvedAnalyzer } from './pi.ts';

const HERE = dirname(fileURLToPath(import.meta.url));
const PROMPT_PATH = join(HERE, '..', 'prompts', 'model-researcher.md');

export class ResearchFailure extends Error {
  readonly detail: string;
  constructor(message: string, detail = '') {
    super(message);
    this.name = 'ResearchFailure';
    this.detail = detail;
  }
}

let cachedPrompt: string | null = null;

/** The researcher's system prompt. Static - no per-request interpolation. */
export function buildResearchSystemPrompt(): string {
  if (cachedPrompt === null) cachedPrompt = readFileSync(PROMPT_PATH, 'utf8');
  return cachedPrompt;
}

function formatPricing(pricing: Record<string, number>): string {
  const input = pricing.input_per_1m_usd;
  const output = pricing.output_per_1m_usd;
  if (input === undefined && output === undefined) return 'pricing unknown';
  return `$${input ?? '?'} input / $${output ?? '?'} output per 1M tokens`;
}

function formatCandidate(candidate: ResearchCandidate): string {
  const efforts = candidate.supported_efforts.length
    ? candidate.supported_efforts.join(', ')
    : 'none published';
  return (
    `- ${candidate.key} (${candidate.display_name}, id: ${candidate.model_id}): ` +
    `${formatPricing(candidate.source_pricing)}; supported efforts: ${efforts}`
  );
}

function formatAnchor(anchor: RegistryAnchor): string {
  const scores = Object.entries(anchor.capability_priors)
    .map(([dim, value]) => `${dim}=${value}`)
    .join(', ');
  return (
    `- ${anchor.key} (${anchor.display_name}): burn ${anchor.relative_model_burn}x, ` +
    `${formatPricing(anchor.source_pricing)}, ${scores}`
  );
}

/** Pure string builder, independently testable with no session involved. */
export function buildResearchUserPrompt(body: ResearchRequestBody): string {
  const sections: string[] = [
    'Research and score the following candidate models.',
    '',
    '--- CANDIDATES TO SCORE ---',
    ...body.candidates.map(formatCandidate),
  ];

  if (body.registry_anchors.length > 0) {
    sections.push('', '--- CALIBRATION ANCHORS (already reviewed; do not rescore) ---');
    sections.push(...body.registry_anchors.map(formatAnchor));
  }

  if (body.claude_docs_text) {
    sections.push('', '--- OFFICIAL CLAUDE DOCS (raw) ---', body.claude_docs_text.trim());
  }

  if (body.codex_catalog && body.codex_catalog.length > 0) {
    sections.push(
      '',
      '--- OFFICIAL CODEX CATALOG (structured) ---',
      JSON.stringify(body.codex_catalog, null, 2),
    );
  }

  if (body.claude_reference_md) {
    sections.push('', '--- CURRENT reference/ClaudeLLM.md ---', body.claude_reference_md.trim());
  }

  if (body.codex_reference_md) {
    sections.push('', '--- CURRENT reference/CodexLLM.md ---', body.codex_reference_md.trim());
  }

  sections.push('', 'Return only the JSON object described in your instructions.');
  return sections.join('\n');
}

/**
 * A failed research turn is reported with the same specificity
 * assertTurnSucceeded gives the analyzer, but as ResearchFailure: the two
 * error types are caught separately in server.ts, so a research failure never
 * gets mistaken for a task-classification failure downstream.
 */
function assertTurnSucceeded(message: AssistantLike | undefined): void {
  if (!message) {
    throw new ResearchFailure('The researcher produced no response at all.');
  }
  if (message.stopReason === 'error') {
    const detail = message.errorMessage ?? 'no detail reported';
    if (/no api key|401|unauthor|expired|invalid.*key/i.test(detail)) {
      throw new ResearchFailure(
        'Pi could not authenticate with the configured analyzer provider.',
        `${detail}. Run "pi" in a terminal and use /login to re-authenticate, then restart the bridge or POST /reload.`,
      );
    }
    throw new ResearchFailure('The researcher provider returned an error.', detail);
  }
  if (message.stopReason === 'aborted') {
    throw new ResearchFailure('The research turn was aborted before it finished.');
  }
  if (message.stopReason === 'length') {
    throw new ResearchFailure(
      'The researcher hit its output limit before completing the JSON object.',
      'This usually means too many candidates were requested in one call.',
    );
  }
}

function tryParse(
  text: string,
  candidateKeys: string[],
  expect: { claude: boolean; codex: boolean },
): { ok: true; value: ResearchResponseBody } | { ok: false; errors: string[] } {
  if (!text.trim()) return { ok: false, errors: ['the researcher returned no text'] };
  let parsed: unknown;
  try {
    parsed = extractJson(text);
  } catch (error) {
    return { ok: false, errors: [(error as Error).message] };
  }
  return validateResearch(parsed, candidateKeys, expect);
}

/**
 * Research one batch of candidate models with an explicitly resolved
 * analyzer session. Exactly one repair attempt on malformed output, mirroring
 * analyzeTask - filling in plausible-looking scores on the model's behalf
 * would silently poison the registry, so a loud failure is correct here too.
 */
export async function researchModels(
  runtime: PiRuntime,
  analyzer: ResolvedAnalyzer,
  body: ResearchRequestBody,
): Promise<{
  result: ResearchResponseBody;
  researcher: {
    provider: string;
    model: string;
    thinking_level: string;
    repaired: boolean;
    duration_ms: number;
  };
}> {
  const started = Date.now();
  const candidateKeys = body.candidates.map((c) => c.key);
  // Whether a rewritten doc is required is driven by whether one was SUPPLIED
  // to extend, not by which providers have candidates: those normally
  // coincide, but requiring a doc only when there is one to build on keeps
  // the contract correct even if a reference file happens to be missing.
  const expect = {
    claude: body.claude_reference_md != null,
    codex: body.codex_reference_md != null,
  };
  const systemPrompt = buildResearchSystemPrompt();

  const session = await createSession(runtime, analyzer, systemPrompt);
  let repaired = false;

  try {
    await session.prompt(buildResearchUserPrompt(body));
    let message = lastAssistant(session.messages);
    assertTurnSucceeded(message);

    let result = tryParse(assistantText(message), candidateKeys, expect);

    if (!result.ok) {
      repaired = true;
      await session.prompt(
        [
          'Your previous response was rejected by the schema validator:',
          '',
          ...result.errors.map((e) => `- ${e}`),
          '',
          'Return ONLY the corrected JSON object. No prose, no markdown fences.',
          'Do not change your scores for candidates that already validated; only',
          'fix the format and the invalid values.',
        ].join('\n'),
      );
      message = lastAssistant(session.messages);
      assertTurnSucceeded(message);
      result = tryParse(assistantText(message), candidateKeys, expect);
    }

    if (!result.ok) {
      throw new ResearchFailure(
        'The researcher could not produce a valid response after one repair attempt.',
        result.errors.join('; '),
      );
    }

    return {
      result: result.value,
      researcher: {
        provider: analyzer.provider,
        model: analyzer.modelId,
        thinking_level: analyzer.thinkingLevel,
        repaired,
        duration_ms: Date.now() - started,
      },
    };
  } finally {
    session.dispose();
  }
}
