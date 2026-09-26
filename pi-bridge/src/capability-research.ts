/**
 * Capability-research request/response types and validation.
 *
 * Mirrors fingerprint.ts's role for the analyzer: pure types plus a
 * validator, independently testable with no session or HTTP involved. Kept
 * separate from researcher.ts (the orchestration) the same way
 * validateFingerprint is kept separate from analyzeTask.
 *
 * `CAPABILITY_DIMENSIONS` mirrors backend/app/schemas/model_registry.py. This
 * is duplicated rather than shared for the same reason SCORE_FIELDS is
 * duplicated from task_fingerprint.py: the backend revalidates everything it
 * receives anyway, so a drift between the two shows up as a clear backend
 * rejection, not as bad data reaching the registry.
 */

export const CAPABILITY_DIMENSIONS = [
  'coding',
  'debugging',
  'architecture',
  'database',
  'concurrency',
  'security',
  'repository_understanding',
] as const;

export type CapabilityDimension = (typeof CAPABILITY_DIMENSIONS)[number];

export type CapabilityPriors = Record<CapabilityDimension, number>;

/** A model that has never been researched: needs scores this cycle. */
export type ResearchCandidate = {
  /** `<provider>/<model key>`, matching the registry key the backend uses. */
  key: string;
  provider: string;
  display_name: string;
  model_id: string;
  source_pricing: Record<string, number>;
  supported_efforts: string[];
};

/** An already-reviewed model, given as a fixed calibration point. */
export type RegistryAnchor = {
  key: string;
  provider: string;
  display_name: string;
  relative_model_burn: number;
  source_pricing: Record<string, number>;
  capability_priors: Record<string, number>;
};

export type ResearchRequestBody = {
  candidates: ResearchCandidate[];
  registry_anchors: RegistryAnchor[];
  claude_docs_text?: string;
  codex_catalog?: unknown[];
  claude_reference_md?: string | null;
  codex_reference_md?: string | null;
};

export type ResearchResponseBody = {
  capability_priors: Record<string, CapabilityPriors>;
  claude_reference_md: string | null;
  codex_reference_md: string | null;
};

export type ValidationResult =
  | { ok: true; value: ResearchResponseBody }
  | { ok: false; errors: string[] };

/**
 * Validate a research response against exactly the candidates that were
 * asked about, and exactly the reference docs that were supplied.
 *
 * `expect.claude`/`expect.codex` say whether a document for that provider was
 * part of the request: the response must return real replacement text for a
 * provider that was asked about, and `null` for one that was not - returning
 * either the wrong way round is treated as invalid, the same way an
 * unexpected or missing candidate key is.
 */
export function validateResearch(
  raw: unknown,
  candidateKeys: string[],
  expect: { claude: boolean; codex: boolean },
): ValidationResult {
  const errors: string[] = [];
  if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) {
    return { ok: false, errors: ['the researcher response is not a JSON object'] };
  }
  const data = raw as Record<string, unknown>;
  const expectedKeys = new Set(candidateKeys);

  const priorsRaw = data.capability_priors;
  const priors: Record<string, CapabilityPriors> = {};
  if (typeof priorsRaw !== 'object' || priorsRaw === null || Array.isArray(priorsRaw)) {
    errors.push('capability_priors must be a JSON object');
  } else {
    const record = priorsRaw as Record<string, unknown>;
    const gotKeys = new Set(Object.keys(record));
    for (const key of expectedKeys) {
      if (!gotKeys.has(key)) errors.push(`capability_priors is missing candidate "${key}"`);
    }
    for (const key of gotKeys) {
      if (!expectedKeys.has(key)) {
        errors.push(`capability_priors has an entry for "${key}", which was not a candidate`);
        continue;
      }
      const entry = record[key];
      if (typeof entry !== 'object' || entry === null || Array.isArray(entry)) {
        errors.push(`capability_priors["${key}"] must be a JSON object`);
        continue;
      }
      const dims = entry as Record<string, unknown>;
      const dimKeys = new Set(Object.keys(dims));
      const scored: Partial<CapabilityPriors> = {};
      for (const dim of CAPABILITY_DIMENSIONS) {
        const value = dims[dim];
        if (typeof value !== 'number' || !Number.isFinite(value)) {
          errors.push(`capability_priors["${key}"].${dim} must be a number, got ${JSON.stringify(value)}`);
          continue;
        }
        if (value < 0 || value > 1) {
          errors.push(`capability_priors["${key}"].${dim} must be between 0.0 and 1.0, got ${value}`);
          continue;
        }
        scored[dim] = value;
        dimKeys.delete(dim);
      }
      for (const extra of dimKeys) {
        errors.push(`capability_priors["${key}"] has an unknown dimension "${extra}"`);
      }
      priors[key] = scored as CapabilityPriors;
    }
  }

  function checkDoc(field: 'claude_reference_md' | 'codex_reference_md', expected: boolean): string | null {
    const value = data[field];
    if (expected) {
      if (typeof value !== 'string' || !value.trim()) {
        errors.push(`${field} must be a non-empty string because a candidate for that provider was given`);
        return null;
      }
      return value;
    }
    if (value !== null && value !== undefined) {
      errors.push(`${field} must be null; no candidate for that provider was given`);
      return null;
    }
    return null;
  }

  const claudeDoc = checkDoc('claude_reference_md', expect.claude);
  const codexDoc = checkDoc('codex_reference_md', expect.codex);

  if (errors.length > 0) return { ok: false, errors };

  return {
    ok: true,
    value: {
      capability_priors: priors,
      claude_reference_md: claudeDoc,
      codex_reference_md: codexDoc,
    },
  };
}
