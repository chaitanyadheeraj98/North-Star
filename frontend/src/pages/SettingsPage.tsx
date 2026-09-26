import { useEffect, useMemo, useState } from 'react';

import { Banner, Button, Card, Pill, SectionTitle, Spinner, Stat } from '../components/ui.tsx';
import { ApiError, api } from '../lib/api.ts';
import { effortLabel } from '../lib/format.ts';
import { THINKING_LEVELS, type PiModelOption, type ModelUpdateResult, type SettingsResponse } from '../lib/types.ts';

export function SettingsPage({ onChanged }: { onChanged?: () => void }) {
  const [data, setData] = useState<SettingsResponse | null>(null);
  const [models, setModels] = useState<PiModelOption[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [updating, setUpdating] = useState(false);
  const [update, setUpdate] = useState<ModelUpdateResult | null>(null);

  async function load() {
    try {
      setData(await api.settings());
    } catch (err) {
      setError((err as ApiError).message);
    }
  }

  useEffect(() => {
    void load();
    api
      .piModels()
      .then((body) => setModels(body.models ?? []))
      .catch(() => setModels([]));
  }, []);

  async function savePreference(patch: Record<string, unknown>) {
    setSaving(true);
    setNotice(null);
    try {
      await api.updateSettings(patch);
      await load();
      onChanged?.();
      setNotice('Saved.');
    } catch (err) {
      setError((err as ApiError).message);
    } finally {
      setSaving(false);
    }
  }

  async function reload() {
    setNotice(null);
    try {
      await api.reloadConfig();
      await load();
      setNotice('Configuration reloaded from YAML.');
    } catch (err) {
      setError((err as ApiError).message);
    }
  }

  async function updateModels() {
    if (updating) return;
    setUpdating(true);
    setError(null);
    setNotice(null);
    setUpdate(null);
    try {
      const result = await api.updateModels();
      setUpdate(result);
      await load();
      onChanged?.();
    } catch (err) {
      setError((err as ApiError).message);
    } finally {
      setUpdating(false);
    }
  }

  if (error && !data) return <Banner tone="error">{error}</Banner>;
  if (!data)
    return (
      <Card>
        <Spinner label="Loading settings..." />
      </Card>
    );

  const pi = data.pi;

  return (
    <div className="space-y-6">
      {notice && <Banner tone="success">{notice}</Banner>}
      {error && <Banner tone="error">{error}</Banner>}

      <Card>
        <div className="flex items-start justify-between gap-4">
          <div>
            <SectionTitle>Task analyzer (Pi)</SectionTitle>
            <div className="flex items-center gap-3">
              <span
                className={`h-2.5 w-2.5 rounded-full ${
                  pi.available ? 'bg-emerald-400' : 'bg-rose-500'
                }`}
                aria-hidden
              />
              <span className="text-base font-medium text-slate-100">
                {pi.available ? 'Connected' : 'Unavailable'}
              </span>
              <span className="font-mono text-xs text-slate-500">{data.pi_bridge_url}</span>
            </div>
          </div>
          <Button variant="secondary" onClick={() => void load()}>
            Recheck
          </Button>
        </div>

        {pi.note && (
          <p className="mt-3 text-sm text-slate-400">{pi.note}</p>
        )}

        <div className="mt-5 grid grid-cols-2 gap-x-8 gap-y-4 sm:grid-cols-4">
          <Stat
            label="Configured"
            value={
              pi.configured_provider
                ? `${pi.configured_provider} / ${pi.configured_model}`
                : '--'
            }
            hint={pi.configured_effort ? `${pi.configured_effort} thinking` : undefined}
          />
          <Stat
            label="Actually in use"
            value={pi.actual_provider ? `${pi.actual_provider} / ${pi.actual_model}` : 'none'}
            tone={
              pi.actual_provider && pi.actual_provider === pi.configured_provider
                ? 'text-emerald-300'
                : 'text-rose-300'
            }
            hint={
              pi.actual_provider
                ? undefined
                : 'the configured analyzer cannot be used right now'
            }
          />
          <Stat label="Authenticated" value={pi.authenticated ? 'Yes' : 'No'} />
          <Stat label="Bridge version" value={pi.bridge_version ?? '--'} />
        </div>

        {pi.alternatives && pi.alternatives.length > 0 && !pi.authenticated && (
          <div className="mt-4 rounded-lg border border-slate-800 bg-slate-950/50 p-3">
            <div className="text-xs text-slate-400">
              Authenticated alternatives. The bridge will not switch to one of these on its
              own; pick one below if you want it.
            </div>
            <div className="mt-2 flex flex-wrap gap-2">
              {pi.alternatives.map((alt) => (
                <Pill key={alt.provider}>
                  {alt.provider} ({alt.models.length})
                </Pill>
              ))}
            </div>
          </div>
        )}

        {pi.authenticated_providers && pi.authenticated_providers.length > 0 && (
          <div className="mt-4 flex flex-wrap items-center gap-2">
            <span className="text-xs text-slate-500">Pi holds credentials for:</span>
            {pi.authenticated_providers.map((provider) => (
              <Pill key={provider}>{provider}</Pill>
            ))}
          </div>
        )}

        <div className="mt-6 space-y-3 border-t border-slate-800 pt-5">
          <label className="flex items-center gap-3 text-sm text-slate-300">
            <input
              type="checkbox"
              className="h-4 w-4 accent-sky-500"
              checked={data.preferences.analyzer_source === 'heuristic'}
              disabled={saving}
              onChange={(event) =>
                void savePreference({
                  analyzer_source: event.target.checked ? 'heuristic' : 'pi',
                })
              }
            />
            Use the offline keyword analyzer instead of Pi
          </label>
          <label className="flex items-center gap-3 text-sm text-slate-300">
            <input
              type="checkbox"
              className="h-4 w-4 accent-sky-500"
              checked={data.preferences.allow_analyzer_fallback}
              disabled={saving}
              onChange={(event) =>
                void savePreference({ allow_analyzer_fallback: event.target.checked })
              }
            />
            Fall back to the offline analyzer when Pi is unreachable
          </label>
          <p className="text-xs text-slate-500">
            The offline analyzer is keyword-based and much cruder than Pi. Fallback is off by
            default so you always know which one classified a task; when it is used, the Router
            page says so.
          </p>
        </div>
      </Card>

      <AnalyzerPicker data={data} models={models} saving={saving} onSave={savePreference} />

      <Card>
        <div className="flex items-start justify-between gap-4">
          <SectionTitle>Model registry</SectionTitle>
          <div className="flex gap-2">
            <Button onClick={() => void updateModels()} disabled={updating}>
              {updating ? 'Updating models...' : 'Update Models'}
            </Button>
            <Button variant="secondary" onClick={() => void reload()} disabled={updating}>
              Reload YAML
            </Button>
          </div>
        </div>

        <p className="mb-4 text-sm text-slate-400">
          Fetch official OpenAI and Anthropic model information. Existing routing priors are
          preserved. A new or never-reviewed model is researched and enabled automatically when
          the Pi bridge is reachable; if Pi can&apos;t be reached it stays disabled with placeholder
          priors until the next update.
        </p>
        {update && <div role="status" className="mb-4 space-y-2 text-sm text-slate-300">
          <p>{update.status === 'updated' ? 'Models updated' : 'No model changes'} &middot; Registry {update.registry_version}</p>
          <p>Added: {update.added.join(', ') || 'none'}</p>
          <p>Changed: {update.changed.join(', ') || 'none'}</p>
          {update.researched.length > 0 && <p>Researched and enabled: {update.researched.join(', ')}</p>}
          {update.unconfirmed.length > 0 && <p>Not listed by these sources; preserved: {update.unconfirmed.join(', ')}</p>}
          {update.backup && <p>Backup: {update.backup}</p>}
          {update.warnings.map((warning) => <p key={warning}>{warning}</p>)}
          <p>Sources: {update.sources.map((source) => <a className="mr-3 underline" key={source} href={source} target="_blank" rel="noreferrer">{new URL(source).hostname}</a>)}</p>
        </div>}

        <div className="space-y-5">
          {data.providers.map((provider) => (
            <div key={provider.key}>
              <div className="flex flex-wrap items-baseline gap-3">
                <h4 className="text-base font-medium text-slate-100">{provider.display_name}</h4>
                {!provider.enabled && <Pill tone="rose">disabled</Pill>}
                <span className="text-xs text-slate-500">
                  burn weight {provider.burn_weight} &middot; {provider.burn_reference}
                </span>
              </div>
              <div className="mt-2 overflow-x-auto">
                <table className="w-full min-w-[720px] text-left text-sm">
                  <thead>
                    <tr className="border-b border-slate-800 text-[11px] uppercase tracking-[0.1em] text-slate-500">
                      <th className="py-2 pr-4 font-medium">Model</th>
                      <th className="py-2 pr-4 font-medium">Identifier</th>
                      <th className="py-2 pr-4 font-medium">Routable efforts</th>
                      <th className="py-2 pr-4 text-right font-medium">Model burn</th>
                      <th className="py-2 font-medium">State</th>
                    </tr>
                  </thead>
                  <tbody>
                    {provider.models.map((model) => (
                      <tr key={model.key} className="border-b border-slate-900">
                        <td className="py-2 pr-4 text-slate-200">{model.display_name}</td>
                        <td className="py-2 pr-4 font-mono text-xs text-slate-500">
                          {model.model_id}
                        </td>
                        <td className="py-2 pr-4 text-xs text-slate-400">
                          {model.routable_efforts.map(effortLabel).join(', ') || 'none'}
                          {model.supported_efforts.length > model.routable_efforts.length && (
                            <span className="ml-2 text-slate-600">
                              (supports{' '}
                              {model.supported_efforts.map(effortLabel).join(', ')})
                            </span>
                          )}
                        </td>
                        <td className="py-2 pr-4 text-right font-mono text-xs text-slate-400">
                          {model.relative_model_burn}
                        </td>
                        <td className="py-2">
                          {model.review_required ? (
                            <Pill tone="amber">needs review</Pill>
                          ) : model.enabled ? (
                            <Pill tone="emerald">enabled</Pill>
                          ) : (
                            <Pill tone="rose">disabled</Pill>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          ))}
        </div>

        <p className="mt-5 text-xs leading-relaxed text-slate-500">
          Model names, supported effort levels and published pricing come from the provider
          reference documents. Capability priors, burn weights and effort priors are this
          application&apos;s own routing assumptions, not provider guarantees. Neither provider
          publishes a fixed token multiplier per effort level; both use adaptive reasoning.
        </p>
      </Card>

      <Card>
        <SectionTitle>Routing policy</SectionTitle>
        <p className="mb-4 text-sm text-slate-400">
          Policy is edited in YAML, not here, so the comments recording which numbers are sourced
          facts and which are assumptions stay attached to them. Edit{' '}
          <code className="rounded bg-slate-800 px-1">backend/config/routing.yaml</code> or{' '}
          <code className="rounded bg-slate-800 px-1">backend/config/models.yaml</code>, then press
          Reload YAML above.
        </p>
        <pre className="max-h-96 overflow-auto rounded-lg border border-slate-800 bg-slate-950 p-4 font-mono text-[11px] leading-relaxed text-slate-400">
          {JSON.stringify(data.policy, null, 2)}
        </pre>
      </Card>

      <Card>
        <SectionTitle>About this instance</SectionTitle>
        <div className="grid grid-cols-2 gap-x-8 gap-y-4 sm:grid-cols-3">
          <Stat label="App" value={data.app_version} />
          <Stat label="Router" value={data.router_version} />
          <Stat label="Registry" value={data.registry_version} />
          <Stat label="Task families" value={data.task_families.length} />
          <Stat label="Config" value={<span className="font-mono text-xs">{data.config_dir}</span>} />
          <Stat
            label="Database"
            value={<span className="font-mono text-xs">{data.database_url}</span>}
          />
        </div>
        <p className="mt-4 text-xs text-slate-500">
          Everything stays on this machine. No telemetry, no cloud database. Pi uses your subscription for task analysis. Update Models fetches public official
          provider documentation without sending task text.
        </p>
      </Card>
    </div>
  );
}


/**
 * Which model classifies tasks.
 *
 * Explicit on purpose. An earlier version of the bridge picked whichever
 * provider authenticated first, which meant the analyzer could change without
 * anyone choosing it. The fingerprint drives every routing decision, so this is
 * a choice the user makes and can see.
 */
function AnalyzerPicker({
  data,
  models,
  saving,
  onSave,
}: {
  data: SettingsResponse;
  models: PiModelOption[];
  saving: boolean;
  onSave: (patch: Record<string, unknown>) => Promise<void>;
}) {
  const { preferences, pi } = data;

  const providers = useMemo(() => [...new Set(models.map((m) => m.provider))].sort(), [models]);
  const activeProvider = preferences.analyzer_provider ?? pi.configured_provider ?? '';
  const providerModels = useMemo(
    () =>
      models
        .filter((m) => m.provider === activeProvider)
        .sort((a, b) => a.id.localeCompare(b.id)),
    [models, activeProvider],
  );

  const select =
    'rounded-lg border border-slate-800 bg-slate-950/70 px-3 py-2 text-sm text-slate-200 ' +
    'outline-none focus:border-sky-700 disabled:opacity-40';

  return (
    <Card>
      <SectionTitle>Analyzer model</SectionTitle>
      <p className="mb-4 text-sm text-slate-400">
        The model that classifies your tasks into a fingerprint. It is not the model that does
        the work, which the router recommends separately. Leave a field on &quot;Use bridge
        default&quot; to inherit it from <code className="rounded bg-slate-800 px-1">.env</code>.
      </p>

      <div className="grid gap-4 sm:grid-cols-3">
        <label className="flex flex-col gap-1.5">
          <span className="text-[11px] uppercase tracking-[0.12em] text-slate-500">Provider</span>
          <select
            aria-label="Analyzer provider"
            className={select}
            disabled={saving || providers.length === 0}
            value={preferences.analyzer_provider ?? ''}
            onChange={(event) => {
              const provider = event.target.value || null;
              // Changing provider must change the model too, or the pair cannot
              // resolve. Start from that provider's first model.
              const first = models.find((m) => m.provider === provider)?.id ?? null;
              void onSave({
                analyzer_provider: provider,
                analyzer_model: provider ? first : null,
              });
            }}
          >
            <option value="">Use bridge default ({pi.configured_provider ?? 'unset'})</option>
            {providers.map((provider) => (
              <option key={provider} value={provider}>
                {provider}
              </option>
            ))}
          </select>
        </label>

        <label className="flex flex-col gap-1.5">
          <span className="text-[11px] uppercase tracking-[0.12em] text-slate-500">Model</span>
          <select
            aria-label="Analyzer model"
            className={select}
            disabled={saving || providerModels.length === 0}
            value={preferences.analyzer_model ?? ''}
            onChange={(event) => void onSave({ analyzer_model: event.target.value || null })}
          >
            <option value="">Use bridge default ({pi.configured_model ?? 'unset'})</option>
            {providerModels.map((model) => (
              <option key={model.id} value={model.id}>
                {model.id}
              </option>
            ))}
          </select>
        </label>

        <label className="flex flex-col gap-1.5">
          <span className="text-[11px] uppercase tracking-[0.12em] text-slate-500">
            Thinking level
          </span>
          <select
            aria-label="Analyzer thinking level"
            className={select}
            disabled={saving}
            value={preferences.analyzer_effort ?? ''}
            onChange={(event) => void onSave({ analyzer_effort: event.target.value || null })}
          >
            <option value="">Use bridge default ({pi.configured_effort ?? 'unset'})</option>
            {THINKING_LEVELS.map((level) => (
              <option key={level} value={level}>
                {level}
              </option>
            ))}
          </select>
        </label>
      </div>

      {models.length === 0 && (
        <p className="mt-4 text-xs text-amber-300">
          No models listed, because the Pi bridge is not reachable. Start it with
          scripts/start-pi.ps1 and press Recheck.
        </p>
      )}

      <p className="mt-4 text-xs text-slate-500">
        A change here applies to the next analysis, with no restart. If the chosen model is
        unavailable or unauthenticated the analysis fails with that reason: the bridge never
        substitutes a different model.
      </p>
    </Card>
  );
}
