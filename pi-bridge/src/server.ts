/**
 * Pi Bridge - the host-local HTTP seam between the Dockerised backend and Pi.
 *
 * It runs on the host rather than in a container because Pi authenticates
 * against the user's existing subscriptions through its own credential store,
 * and a container would have neither the credentials nor a browser to complete
 * an OAuth login with.
 *
 * SECURITY: binds to 127.0.0.1 only, never 0.0.0.0. Anything that can reach
 * this port can spend the user's subscription quota, so it must not be
 * reachable from the LAN. Docker Desktop reaches it through
 * host.docker.internal, which resolves to the host loopback.
 *
 * The analyzer provider, model and thinking level are CONFIGURED, never
 * inferred from whichever provider happens to authenticate. See config.ts.
 */

import { createServer, type IncomingMessage, type ServerResponse } from 'node:http';

import { AnalyzerFailure, analyzeTask } from './analyzer.ts';
import type { ResearchRequestBody } from './capability-research.ts';
import {
  AnalyzerConfigError,
  bootConfig,
  loadDotEnv,
  THINKING_LEVELS,
  withOverrides,
  type AnalyzerConfig,
} from './config.ts';
import { loadTaskFamilies, type TaskFamily } from './families.ts';
import {
  PiUnavailable,
  authenticatedAlternatives,
  authenticatedProviders,
  createRuntime,
  listModels,
  resolveAnalyzer,
  type PiRuntime,
  type Resolution,
} from './pi.ts';
import { ResearchFailure, researchModels } from './researcher.ts';

const VERSION = '1.1.0';

const envFile = loadDotEnv();

const HOST = process.env.PI_BRIDGE_HOST ?? '127.0.0.1';
const PORT = Number(process.env.PI_BRIDGE_PORT ?? 31415);
const MAX_BODY_BYTES = 1_000_000;

if (HOST !== '127.0.0.1' && HOST !== 'localhost' && process.env.PI_BRIDGE_ALLOW_REMOTE !== '1') {
  console.error(
    `Refusing to bind to ${HOST}. This bridge can spend your subscription quota and must\n` +
      'stay on loopback. Set PI_BRIDGE_ALLOW_REMOTE=1 only if you genuinely understand\n' +
      'the consequences of exposing it.',
  );
  process.exit(1);
}

let runtime: PiRuntime | undefined;
let configured: AnalyzerConfig | undefined;
let configError: string | undefined;
let families: TaskFamily[] = [];

async function boot(): Promise<void> {
  families = loadTaskFamilies();
  if (families.length === 0) {
    console.warn(
      'WARNING: could not read backend/config/task_families.yaml. The analyzer will run\n' +
        'without the canonical family list, and the backend will reject any family it\n' +
        'does not recognise. Set TASK_FAMILIES_PATH if the bridge lives outside the repo.',
    );
  }

  try {
    configured = bootConfig();
    configError = undefined;
  } catch (error) {
    configured = undefined;
    configError =
      error instanceof AnalyzerConfigError
        ? [error.message, error.detail].filter(Boolean).join(' ')
        : String(error);
  }

  try {
    runtime = createRuntime({});
  } catch (error) {
    runtime = undefined;
    configError = error instanceof Error ? error.message : String(error);
  }
}

/** Resolve the configured analyzer right now, for /health and /analyze. */
async function currentResolution(
  overrides: Record<string, unknown> = {},
): Promise<{ config: AnalyzerConfig; resolution: Resolution } | { error: string; detail: string }> {
  if (!runtime || !configured) {
    return {
      error: configError ?? 'The bridge has no analyzer configuration.',
      detail: `Set PI_PROVIDER / PI_MODEL / PI_THINKING_LEVEL${envFile ? ` in ${envFile}` : ''}.`,
    };
  }
  let config: AnalyzerConfig;
  try {
    config = withOverrides(configured, overrides);
  } catch (error) {
    if (error instanceof AnalyzerConfigError) {
      return { error: error.message, detail: error.detail };
    }
    throw error;
  }
  return { config, resolution: await resolveAnalyzer(runtime, config) };
}

// --- HTTP plumbing ---------------------------------------------------------

function send(res: ServerResponse, status: number, body: unknown): void {
  const payload = JSON.stringify(body);
  res.writeHead(status, {
    'content-type': 'application/json; charset=utf-8',
    'content-length': Buffer.byteLength(payload),
    'cache-control': 'no-store',
  });
  res.end(payload);
}

async function readJsonBody(req: IncomingMessage): Promise<unknown> {
  const chunks: Buffer[] = [];
  let size = 0;
  for await (const chunk of req) {
    size += (chunk as Buffer).length;
    if (size > MAX_BODY_BYTES) throw new Error('request body is too large');
    chunks.push(chunk as Buffer);
  }
  const raw = Buffer.concat(chunks).toString('utf8');
  if (!raw.trim()) return {};
  return JSON.parse(raw);
}

// --- Handlers --------------------------------------------------------------

/**
 * Report what is configured AND what would actually run.
 *
 * Both halves matter. `configured_*` is what the user asked for; `actual_*` is
 * what a request would use right now. When they differ, or when actual is null,
 * something is wrong and the note says what.
 */
async function handleHealth(res: ServerResponse): Promise<void> {
  const current = await currentResolution();

  if ('error' in current) {
    send(res, 200, {
      status: 'misconfigured',
      pi_available: false,
      version: VERSION,
      configured_provider: null,
      configured_model: null,
      configured_effort: null,
      authenticated: false,
      actual_provider: null,
      actual_model: null,
      authenticated_providers: runtime ? authenticatedProviders(runtime) : [],
      alternatives: runtime ? authenticatedAlternatives(runtime) : [],
      task_families: families.length,
      env_file: envFile ?? null,
      note: `${current.error} ${current.detail}`.trim(),
    });
    return;
  }

  const { config, resolution } = current;
  const base = {
    version: VERSION,
    configured_provider: config.provider,
    configured_model: config.model,
    configured_effort: config.thinkingLevel,
    configured_from: config.source,
    task_families: families.length,
    env_file: envFile ?? null,
    authenticated_providers: runtime ? authenticatedProviders(runtime) : [],
  };

  if (!resolution.ok) {
    send(res, 200, {
      ...base,
      status: 'degraded',
      pi_available: false,
      authenticated: false,
      actual_provider: null,
      actual_model: null,
      error_code: resolution.failure.code,
      alternatives: resolution.failure.alternatives,
      note: `${resolution.failure.message} ${resolution.failure.detail}`.trim(),
    });
    return;
  }

  send(res, 200, {
    ...base,
    status: 'ok',
    pi_available: true,
    authenticated: true,
    actual_provider: resolution.analyzer.provider,
    actual_model: resolution.analyzer.modelId,
    // Retained for older backends that read these names.
    provider: resolution.analyzer.provider,
    model: resolution.analyzer.modelId,
    thinking_level: resolution.analyzer.thinkingLevel,
  });
}

async function handleAnalyze(req: IncomingMessage, res: ServerResponse): Promise<void> {
  let body: unknown;
  try {
    body = await readJsonBody(req);
  } catch (error) {
    send(res, 400, { error: `invalid request body: ${(error as Error).message}` });
    return;
  }

  const payload = body as Record<string, unknown>;
  const task = typeof payload.task === 'string' ? payload.task : '';
  if (!task.trim()) {
    send(res, 400, { error: 'field "task" is required and must be a non-empty string' });
    return;
  }
  const profile =
    typeof payload.analysis_profile === 'string' ? payload.analysis_profile : 'router-v1';
  if (profile !== 'router-v1') {
    send(res, 400, {
      error: `unknown analysis_profile "${profile}"; this bridge speaks router-v1`,
    });
    return;
  }

  // Per-request overrides are what the Settings page ultimately drives, so a
  // preference change takes effect on the next analysis with no restart.
  const current = await currentResolution({
    provider: payload.provider,
    model: payload.model,
    thinking_level: payload.thinking_level,
  });

  if ('error' in current) {
    console.error(`analyze rejected: ${current.error}`);
    send(res, 400, {
      error: current.error,
      detail: current.detail,
      valid_thinking_levels: THINKING_LEVELS,
    });
    return;
  }

  const { config, resolution } = current;

  if (!resolution.ok) {
    // No fallback. Choosing a different model on the user's behalf is exactly
    // the behaviour this rewrite removes.
    console.error(
      `analyze unavailable: ${config.provider}/${config.model} -> ${resolution.failure.code}`,
    );
    send(res, 503, {
      error: resolution.failure.message,
      detail: resolution.failure.detail,
      code: resolution.failure.code,
      configured_provider: config.provider,
      configured_model: config.model,
      configured_effort: config.thinkingLevel,
      alternatives: resolution.failure.alternatives,
    });
    return;
  }

  try {
    const result = await analyzeTask(runtime!, resolution.analyzer, task, families);
    console.log(
      `analyze ok  ${result.analyzer.provider}/${result.analyzer.model} ` +
        `(${result.analyzer.thinking_level})  family=${result.fingerprint.task_family} ` +
        `confidence=${result.fingerprint.confidence.toFixed(2)} ` +
        `repaired=${result.analyzer.repaired} ${result.analyzer.duration_ms}ms`,
    );
    send(res, 200, result);
  } catch (error) {
    if (error instanceof AnalyzerFailure) {
      console.error(`analyze failed: ${error.message} :: ${error.detail}`);
      send(res, 422, { error: error.message, detail: error.detail });
      return;
    }
    if (error instanceof PiUnavailable) {
      console.error(`analyze unavailable: ${error.message}`);
      send(res, 503, { error: error.message, detail: error.detail });
      return;
    }
    const message = error instanceof Error ? error.message : String(error);
    console.error(`analyze error: ${message}`);
    send(res, 500, { error: 'The analyzer failed.', detail: message });
  }
}

/**
 * Score a batch of never-reviewed models, grounded in official docs and the
 * existing registry. Reuses the SAME configured analyzer as /analyze - no
 * separate model choice or credential path for research versus classification.
 */
async function handleResearch(req: IncomingMessage, res: ServerResponse): Promise<void> {
  let body: unknown;
  try {
    body = await readJsonBody(req);
  } catch (error) {
    send(res, 400, { error: `invalid request body: ${(error as Error).message}` });
    return;
  }

  const payload = body as Partial<ResearchRequestBody>;
  const candidates = Array.isArray(payload.candidates) ? payload.candidates : null;
  if (!candidates || candidates.length === 0) {
    send(res, 400, { error: 'field "candidates" is required and must be a non-empty array' });
    return;
  }

  const current = await currentResolution({});
  if ('error' in current) {
    console.error(`research rejected: ${current.error}`);
    send(res, 400, { error: current.error, detail: current.detail });
    return;
  }

  const { resolution } = current;
  if (!resolution.ok) {
    console.error(`research unavailable: ${resolution.failure.code}`);
    send(res, 503, {
      error: resolution.failure.message,
      detail: resolution.failure.detail,
      code: resolution.failure.code,
      alternatives: resolution.failure.alternatives,
    });
    return;
  }

  const requestBody: ResearchRequestBody = {
    candidates,
    registry_anchors: Array.isArray(payload.registry_anchors) ? payload.registry_anchors : [],
    claude_docs_text: typeof payload.claude_docs_text === 'string' ? payload.claude_docs_text : undefined,
    codex_catalog: Array.isArray(payload.codex_catalog) ? payload.codex_catalog : undefined,
    claude_reference_md:
      typeof payload.claude_reference_md === 'string' ? payload.claude_reference_md : null,
    codex_reference_md:
      typeof payload.codex_reference_md === 'string' ? payload.codex_reference_md : null,
  };

  try {
    const { result, researcher } = await researchModels(runtime!, resolution.analyzer, requestBody);
    console.log(
      `research ok  ${researcher.provider}/${researcher.model} (${researcher.thinking_level}) ` +
        `candidates=${requestBody.candidates.length} repaired=${researcher.repaired} ${researcher.duration_ms}ms`,
    );
    send(res, 200, { ...result, researcher });
  } catch (error) {
    if (error instanceof ResearchFailure) {
      console.error(`research failed: ${error.message} :: ${error.detail}`);
      send(res, 422, { error: error.message, detail: error.detail });
      return;
    }
    if (error instanceof PiUnavailable) {
      console.error(`research unavailable: ${error.message}`);
      send(res, 503, { error: error.message, detail: error.detail });
      return;
    }
    const message = error instanceof Error ? error.message : String(error);
    console.error(`research error: ${message}`);
    send(res, 500, { error: 'The researcher failed.', detail: message });
  }
}

function handleProviders(res: ServerResponse): void {
  if (!runtime) {
    send(res, 200, { providers: [] });
    return;
  }
  send(res, 200, {
    providers: authenticatedAlternatives(runtime).map((entry) => ({
      name: entry.provider,
      models: entry.models.length,
      model_ids: entry.models,
      configured: entry.provider === configured?.provider,
    })),
  });
}

function handleModels(res: ServerResponse): void {
  send(res, 200, {
    models: runtime ? listModels(runtime) : [],
    thinking_levels: THINKING_LEVELS,
    configured_provider: configured?.provider ?? null,
    configured_model: configured?.model ?? null,
    configured_effort: configured?.thinkingLevel ?? null,
  });
}

// --- Server ----------------------------------------------------------------

const server = createServer((req, res) => {
  const url = new URL(req.url ?? '/', `http://${HOST}:${PORT}`);
  const route = `${req.method} ${url.pathname}`;

  if (req.method === 'OPTIONS') {
    res.writeHead(204, {
      'access-control-allow-origin': '*',
      'access-control-allow-methods': 'GET, POST, OPTIONS',
      'access-control-allow-headers': 'content-type',
    });
    res.end();
    return;
  }

  switch (route) {
    case 'GET /health':
      void handleHealth(res);
      return;
    case 'POST /analyze':
      void handleAnalyze(req, res);
      return;
    case 'POST /research':
      void handleResearch(req, res);
      return;
    case 'GET /providers':
      handleProviders(res);
      return;
    case 'GET /models':
      handleModels(res);
      return;
    case 'POST /reload':
      // Re-reads .env, task families and credentials, so a /login in another
      // terminal can be picked up without restarting the bridge.
      loadDotEnv();
      void boot().then(() => handleHealth(res));
      return;
    default:
      send(res, 404, { error: `no route for ${route}` });
  }
});

await boot();

server.listen(PORT, HOST, async () => {
  const current = await currentResolution();
  console.log(`Pi Bridge ${VERSION} listening on http://${HOST}:${PORT}`);
  if (envFile) console.log(`  env      : ${envFile}`);

  if ('error' in current) {
    console.log(`  analyzer : NOT CONFIGURED`);
    console.log('');
    console.log(`  ${current.error}`);
    console.log(`  ${current.detail}`);
    return;
  }

  const { config, resolution } = current;
  console.log(
    `  analyzer : ${config.provider} / ${config.model} / ${config.thinkingLevel}` +
      `  (provider=${config.source.provider}, model=${config.source.model},` +
      ` effort=${config.source.thinkingLevel})`,
  );
  console.log(`  families : ${families.length} loaded`);
  console.log(`  tools    : none (the classifier gets no filesystem or shell access)`);

  if (resolution.ok) {
    console.log(`  auth     : ok`);
  } else {
    console.log(`  auth     : NOT READY (${resolution.failure.code})`);
    console.log('');
    console.log(`  ${resolution.failure.message}`);
    console.log(`  ${resolution.failure.detail}`);
    if (resolution.failure.alternatives.length > 0) {
      console.log('');
      console.log('  Authenticated alternatives (the bridge will NOT pick one for you):');
      for (const alt of resolution.failure.alternatives) {
        console.log(`    ${alt.provider}: ${alt.models.slice(0, 6).join(', ')}`);
      }
    }
  }
});

for (const signal of ['SIGINT', 'SIGTERM'] as const) {
  process.on(signal, () => {
    console.log(`\n${signal} received, shutting down.`);
    server.close(() => process.exit(0));
  });
}
