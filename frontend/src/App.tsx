import { useCallback, useEffect, useState } from 'react';

import { api } from './lib/api.ts';
import type { PiStatus, SettingsResponse } from './lib/types.ts';
import { AnalyticsPage } from './pages/AnalyticsPage.tsx';
import { HistoryPage } from './pages/HistoryPage.tsx';
import { ResultPage } from './pages/ResultPage.tsx';
import { RouterPage } from './pages/RouterPage.tsx';
import { SettingsPage } from './pages/SettingsPage.tsx';

const TABS = ['router', 'result', 'history', 'analytics', 'settings'] as const;
type Tab = (typeof TABS)[number];

function tabFromHash(): Tab {
  const hash = window.location.hash.replace('#/', '').replace('#', '');
  return (TABS as readonly string[]).includes(hash) ? (hash as Tab) : 'router';
}

/**
 * Hash routing rather than a router dependency: five flat tabs need no
 * nested routes, and the hash keeps refresh and bookmarking working.
 */
export function App() {
  const [tab, setTab] = useState<Tab>(tabFromHash);
  const [pi, setPi] = useState<PiStatus | null>(null);
  const [preferences, setPreferences] = useState<SettingsResponse['preferences'] | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    const onHashChange = () => setTab(tabFromHash());
    window.addEventListener('hashchange', onHashChange);
    return () => window.removeEventListener('hashchange', onHashChange);
  }, []);

  const refreshStatus = useCallback(() => {
    api
      .settings()
      .then((settings) => {
        setPi(settings.pi);
        setPreferences(settings.preferences);
      })
      .catch(() => setPi(null));
  }, []);

  useEffect(refreshStatus, [refreshStatus]);

  function go(next: Tab) {
    window.location.hash = `/${next}`;
    setTab(next);
  }

  return (
    <div className="min-h-screen bg-slate-950 text-slate-200">
      <header className="sticky top-0 z-10 border-b border-slate-800 bg-slate-950/90 backdrop-blur">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-6 px-6 py-4">
          <div>
            <h1 className="text-sm font-semibold tracking-tight text-slate-100">
              Adaptive LLM Router
            </h1>
            <p className="text-[11px] text-slate-500">Use the right amount of AI for the task.</p>
          </div>

          <nav className="flex gap-1" aria-label="Sections">
            {TABS.map((name) => (
              <button
                key={name}
                type="button"
                onClick={() => go(name)}
                aria-current={tab === name ? 'page' : undefined}
                className={`rounded-lg px-3 py-1.5 text-xs font-medium uppercase tracking-[0.1em] transition-colors ${
                  tab === name
                    ? 'bg-slate-800 text-slate-100'
                    : 'text-slate-500 hover:text-slate-300'
                }`}
              >
                {name}
              </button>
            ))}
          </nav>

          <button
            type="button"
            onClick={() => go('settings')}
            className="ml-auto flex items-center gap-2 text-xs text-slate-500 transition-colors hover:text-slate-300"
            title={pi?.note ?? undefined}
          >
            <span
              className={`h-2 w-2 rounded-full ${
                pi === null ? 'bg-slate-600' : pi.available ? 'bg-emerald-400' : 'bg-rose-500'
              }`}
              aria-hidden
            />
            Pi {pi === null ? 'unknown' : pi.available ? 'connected' : 'unavailable'}
          </button>
        </div>
      </header>

      <main className="mx-auto max-w-6xl px-6 py-8">
        {/* Hidden rather than unmounted so the task and result survive a tab switch. */}
        <div hidden={tab !== 'router'}>
          <RouterPage pi={pi} preferences={preferences} />
        </div>
        {tab === 'result' && <ResultPage onImported={() => setReloadKey((k) => k + 1)} />}
        {tab === 'history' && <HistoryPage key={`history-${reloadKey}`} />}
        {tab === 'analytics' && <AnalyticsPage key={`analytics-${reloadKey}`} />}
        {tab === 'settings' && <SettingsPage onChanged={refreshStatus} />}
      </main>
    </div>
  );
}
