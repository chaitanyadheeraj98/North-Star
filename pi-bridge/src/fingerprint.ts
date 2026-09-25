/**
 * TaskFingerprint validation, mirrored from backend/app/schemas/task_fingerprint.py.
 *
 * The bridge validates before returning so that a malformed analyzer response
 * can be repaired here, where the model is still in hand, rather than failing
 * two processes away in Python.
 *
 * This is duplicated logic, and that is a deliberate trade. The alternative is
 * shipping a schema-generation step between two languages for one small
 * object; the cost of that machinery is higher than the cost of this file, and
 * the backend revalidates everything anyway, so a drift between the two shows
 * up as a clear backend rejection rather than as bad data.
 */

export const SCHEMA_VERSION = '1.0';

export const SCOPES = [
  'single_line',
  'single_function',
  'single_file',
  'multi_file',
  'service',
  'multi_service',
  'architecture',
] as const;

export const TEST_INTENSITIES = ['low', 'medium', 'high'] as const;

export const SCORE_FIELDS = [
  'complexity',
  'ambiguity',
  'requirements_clarity',
  'regression_risk',
  'architecture_reasoning',
  'database_reasoning',
  'concurrency_risk',
  'security_risk',
  'repository_understanding',
  'confidence',
] as const;

export const BOOLEAN_FIELDS = ['frontend', 'backend', 'database', 'infrastructure'] as const;

export type Scope = (typeof SCOPES)[number];
export type TestIntensity = (typeof TEST_INTENSITIES)[number];

export type TaskFingerprint = {
  schema_version: string;
  task_family: string;
  task_type: string;
  complexity: number;
  ambiguity: number;
  requirements_clarity: number;
  regression_risk: number;
  architecture_reasoning: number;
  database_reasoning: number;
  concurrency_risk: number;
  security_risk: number;
  repository_understanding: number;
  scope: Scope;
  estimated_files: number;
  frontend: boolean;
  backend: boolean;
  database: boolean;
  infrastructure: boolean;
  tests_required: TestIntensity;
  confidence: number;
};

export type ValidationResult =
  | { ok: true; value: TaskFingerprint }
  | { ok: false; errors: string[] };

/**
 * Pull a JSON object out of a model response.
 *
 * Models wrap JSON in fences, prefix it with "Here is the classification:",
 * or emit it after a thinking block. All of that is recoverable; a response
 * with no balanced object in it is not.
 */
export function extractJson(text: string): unknown {
  if (!text || !text.trim()) throw new Error('the analyzer returned an empty response');

  let body = text.trim();

  const fenced = body.match(/```(?:json)?\s*\n([\s\S]*?)\n?```/);
  if (fenced) body = fenced[1].trim();

  const start = body.indexOf('{');
  if (start === -1) throw new Error('no JSON object found in the analyzer response');

  // Walk to the matching brace so trailing prose does not break the parse.
  let depth = 0;
  let inString = false;
  let escaped = false;
  let end = -1;
  for (let i = start; i < body.length; i++) {
    const ch = body[i];
    if (escaped) {
      escaped = false;
      continue;
    }
    if (ch === '\\') {
      escaped = true;
      continue;
    }
    if (ch === '"') {
      inString = !inString;
      continue;
    }
    if (inString) continue;
    if (ch === '{') depth++;
    else if (ch === '}') {
      depth--;
      if (depth === 0) {
        end = i;
        break;
      }
    }
  }
  if (end === -1) throw new Error('the JSON object in the analyzer response is not closed');

  return JSON.parse(body.slice(start, end + 1));
}

export function validateFingerprint(raw: unknown, knownFamilies: string[]): ValidationResult {
  const errors: string[] = [];
  if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) {
    return { ok: false, errors: ['the analyzer response is not a JSON object'] };
  }
  const data = raw as Record<string, unknown>;

  const version = typeof data.schema_version === 'string' ? data.schema_version : SCHEMA_VERSION;
  if (version !== SCHEMA_VERSION) {
    errors.push(`schema_version must be "${SCHEMA_VERSION}", got ${JSON.stringify(data.schema_version)}`);
  }

  const family = typeof data.task_family === 'string' ? data.task_family.trim().toLowerCase() : '';
  if (!family) {
    errors.push('task_family is missing');
  } else if (knownFamilies.length > 0 && !knownFamilies.includes(family)) {
    errors.push(
      `task_family "${family}" is not one of the allowed families: ${knownFamilies.join(', ')}`,
    );
  }

  const scores: Record<string, number> = {};
  for (const field of SCORE_FIELDS) {
    const value = data[field];
    if (typeof value !== 'number' || !Number.isFinite(value)) {
      errors.push(`${field} must be a number, got ${JSON.stringify(value)}`);
      continue;
    }
    if (value < 0 || value > 1) {
      errors.push(`${field} must be between 0.0 and 1.0, got ${value}`);
      continue;
    }
    scores[field] = value;
  }

  const scope = data.scope;
  if (typeof scope !== 'string' || !(SCOPES as readonly string[]).includes(scope)) {
    errors.push(`scope must be one of ${SCOPES.join(', ')}, got ${JSON.stringify(scope)}`);
  }

  const tests = data.tests_required;
  if (typeof tests !== 'string' || !(TEST_INTENSITIES as readonly string[]).includes(tests)) {
    errors.push(
      `tests_required must be one of ${TEST_INTENSITIES.join(', ')}, got ${JSON.stringify(tests)}`,
    );
  }

  const files = data.estimated_files;
  if (typeof files !== 'number' || !Number.isFinite(files) || files < 0) {
    errors.push(`estimated_files must be a non-negative number, got ${JSON.stringify(files)}`);
  }

  const booleans: Record<string, boolean> = {};
  for (const field of BOOLEAN_FIELDS) {
    const value = data[field];
    if (value !== undefined && typeof value !== 'boolean') {
      errors.push(`${field} must be a boolean, got ${JSON.stringify(value)}`);
    } else {
      booleans[field] = value === true;
    }
  }

  if (errors.length > 0) return { ok: false, errors };

  return {
    ok: true,
    value: {
      schema_version: SCHEMA_VERSION,
      task_family: family,
      task_type: typeof data.task_type === 'string' ? data.task_type.slice(0, 120) : '',
      complexity: scores.complexity,
      ambiguity: scores.ambiguity,
      requirements_clarity: scores.requirements_clarity,
      regression_risk: scores.regression_risk,
      architecture_reasoning: scores.architecture_reasoning,
      database_reasoning: scores.database_reasoning,
      concurrency_risk: scores.concurrency_risk,
      security_risk: scores.security_risk,
      repository_understanding: scores.repository_understanding,
      scope: scope as Scope,
      estimated_files: Math.round(files as number),
      frontend: booleans.frontend,
      backend: booleans.backend,
      database: booleans.database,
      infrastructure: booleans.infrastructure,
      tests_required: tests as TestIntensity,
      confidence: scores.confidence,
    },
  };
}
