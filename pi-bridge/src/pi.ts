/**
 * Pi SDK adapter.
 *
 * Everything Pi-specific in this project lives here and in `config.ts`. The
 * bridge exposes a fixed HTTP contract, so swapping the Pi SDK for Pi RPC or a
 * subprocess touches these files and nothing on the Python side.
 *
 * Verified against @mariozechner/pi-coding-agent 0.73.1:
 *   AuthStorage.create()                      credential store (~/.pi/agent/auth.json)
 *   ModelRegistry.create(authStorage)         built-in + custom models
 *   registry.find(provider, id)               resolve one model explicitly
 *   registry.getAvailable()                   models that have SOME credential
 *   authStorage.getApiKey(provider)           refreshes OAuth; undefined if it cannot
 *   createAgentSession({ model, thinkingLevel, ... })
 *
 * There is no `ModelRuntime` export in this version of the SDK. The surface
 * that resolves a model is `ModelRegistry`, and `AgentSessionRuntime` is a
 * different thing (session replacement, not model lookup). `ModelRegistry.find`
 * is used below and the resolved `Model` object is passed explicitly into
 * `createAgentSession`, which is the behaviour that matters.
 *
 * SECURITY: the bridge never reads, logs or forwards a credential. It reports
 * only which providers are configured and whether a token could be obtained.
 */

import {
  AuthStorage,
  DefaultResourceLoader,
  ModelRegistry,
  SessionManager,
  createAgentSession,
  getAgentDir,
} from '@mariozechner/pi-coding-agent';

import type { AnalyzerConfig, ThinkingLevel } from './config.ts';

export type PiModel = {
  id: string;
  name?: string;
  provider: string;
  reasoning?: boolean;
  contextWindow?: number;
  cost?: { input: number; output: number };
};

export type PiRuntime = {
  authStorage: ReturnType<typeof AuthStorage.create>;
  registry: ReturnType<typeof ModelRegistry.create>;
  agentDir: string;
  cwd: string;
};

export type ResolvedAnalyzer = {
  model: PiModel;
  provider: string;
  modelId: string;
  thinkingLevel: ThinkingLevel;
};

export type ResolutionFailure = {
  code: 'model_not_found' | 'not_authenticated' | 'auth_check_failed';
  message: string;
  detail: string;
  alternatives: Array<{ provider: string; models: string[] }>;
};

export type Resolution =
  | { ok: true; analyzer: ResolvedAnalyzer }
  | { ok: false; failure: ResolutionFailure };

export class PiUnavailable extends Error {
  readonly detail: string;
  readonly failure?: ResolutionFailure;
  constructor(message: string, detail = '', failure?: ResolutionFailure) {
    super(message);
    this.name = 'PiUnavailable';
    this.detail = detail;
    this.failure = failure;
  }
}

export function createRuntime(options: { cwd?: string } = {}): PiRuntime {
  const authStorage = AuthStorage.create();
  return {
    authStorage,
    registry: ModelRegistry.create(authStorage),
    agentDir: getAgentDir(),
    cwd: options.cwd ?? process.cwd(),
  };
}

/**
 * Resolve the configured analyzer, or explain precisely why it cannot be used.
 *
 * This function has NO fallback path on purpose. If the configured model is
 * missing or its provider is not usable, it fails and lists what IS available
 * so the user can choose. It must never quietly substitute a different model:
 * the whole reason this rewrite exists is that "pick whatever authenticates"
 * silently swapped the analyzer out from under the user.
 */
export async function resolveAnalyzer(
  runtime: PiRuntime,
  config: AnalyzerConfig,
): Promise<Resolution> {
  const model = runtime.registry.find(config.provider, config.model) as
    | PiModel
    | undefined;

  if (!model) {
    return {
      ok: false,
      failure: {
        code: 'model_not_found',
        message: `Pi has no model "${config.model}" for provider "${config.provider}".`,
        detail:
          'Check PI_PROVIDER / PI_MODEL, or the analyzer settings. ' +
          'Run "pi --list-models" to see every model Pi knows about.',
        alternatives: authenticatedAlternatives(runtime),
      },
    };
  }

  // `getAvailable()` only proves a credential EXISTS. `getApiKey` is the call
  // the request path actually makes: it refreshes an expired OAuth token and
  // returns undefined when that refresh fails. Checking it here turns a
  // confusing mid-analysis "No API key for provider" from deep inside the
  // provider into an actionable startup message.
  let token: string | undefined;
  try {
    token = await runtime.authStorage.getApiKey(config.provider);
  } catch (error) {
    return {
      ok: false,
      failure: {
        code: 'auth_check_failed',
        message: `Could not check credentials for "${config.provider}".`,
        detail: (error as Error).message,
        alternatives: authenticatedAlternatives(runtime),
      },
    };
  }

  if (!token) {
    const status = runtime.authStorage.getAuthStatus(config.provider);
    return {
      ok: false,
      failure: {
        code: 'not_authenticated',
        message: status.configured
          ? `Pi has credentials for "${config.provider}" but could not obtain a usable token (expired, and refresh failed).`
          : `Pi is not authenticated with "${config.provider}".`,
        detail: `Run "pi" in a terminal and use /login to authenticate ${config.provider}, then restart the bridge or POST /reload.`,
        alternatives: authenticatedAlternatives(runtime),
      },
    };
  }

  return {
    ok: true,
    analyzer: {
      model,
      provider: String(model.provider),
      modelId: String(model.id),
      thinkingLevel: config.thinkingLevel,
    },
  };
}

/**
 * Providers that can currently produce a token, with their model ids.
 *
 * Offered as CHOICES when the configured analyzer cannot be used. The bridge
 * never picks one of these by itself.
 */
export function authenticatedAlternatives(
  runtime: PiRuntime,
): Array<{ provider: string; models: string[] }> {
  const grouped = new Map<string, string[]>();
  for (const model of safeAvailable(runtime.registry)) {
    const list = grouped.get(model.provider) ?? [];
    list.push(String(model.id));
    grouped.set(String(model.provider), list);
  }
  return [...grouped.entries()]
    .map(([provider, models]) => ({ provider, models: models.sort() }))
    .sort((a, b) => a.provider.localeCompare(b.provider));
}

/**
 * Create a tool-less analyzer session bound to an explicitly resolved model.
 *
 * The model object and the thinking level are passed straight into
 * `createAgentSession`. Nothing is left to Pi's own default-model logic, which
 * reads the interactive session's settings and would reintroduce exactly the
 * ambiguity this module exists to remove.
 *
 * Security posture: NO TOOLS. Classifying a pasted task needs no filesystem,
 * no shell and no repository access. Pi runs with the permissions of the user
 * who launched it, so the correct amount of capability to hand a classifier is
 * none.
 */
export async function createSession(
  runtime: PiRuntime,
  analyzer: ResolvedAnalyzer,
  systemPrompt: string,
) {
  const loader = new DefaultResourceLoader({
    cwd: runtime.cwd,
    agentDir: runtime.agentDir,
    systemPrompt,
    // The analyzer is a pure classifier. Loading the user's extensions, skills,
    // prompt templates or AGENTS.md/CLAUDE.md files would let unrelated project
    // instructions leak into a classification, and would make the fingerprint
    // depend on which directory the bridge happened to start in.
    noExtensions: true,
    noSkills: true,
    noPromptTemplates: true,
    noThemes: true,
    noContextFiles: true,
  });
  await loader.reload();

  const { session } = await createAgentSession({
    cwd: runtime.cwd,
    agentDir: runtime.agentDir,
    authStorage: runtime.authStorage,
    modelRegistry: runtime.registry,
    model: analyzer.model as never,
    thinkingLevel: analyzer.thinkingLevel as never,
    noTools: 'all',
    tools: [],
    resourceLoader: loader,
    sessionManager: SessionManager.inMemory(),
  });
  return session;
}

export function listModels(runtime: PiRuntime): PiModel[] {
  return safeAvailable(runtime.registry).map((model) => ({
    id: String(model.id),
    name: model.name ? String(model.name) : undefined,
    provider: String(model.provider),
    reasoning: Boolean(model.reasoning),
    contextWindow: model.contextWindow,
    cost: model.cost,
  }));
}

/** Providers Pi holds credentials for. Names only; never the credentials. */
export function authenticatedProviders(runtime: PiRuntime): string[] {
  const providers = new Set<string>();
  for (const model of safeAvailable(runtime.registry)) providers.add(String(model.provider));
  return [...providers].sort();
}

function safeAvailable(registry: ReturnType<typeof ModelRegistry.create>): PiModel[] {
  try {
    return (registry.getAvailable() as unknown as PiModel[]) ?? [];
  } catch {
    return [];
  }
}
