import { useEffect, useState } from 'react';

import { Banner, Card, Empty, Pill, SectionTitle, Spinner, Stat } from '../components/ui.tsx';
import { ApiError, api } from '../lib/api.ts';
import { burn, pct, titleCase } from '../lib/format.ts';
import type { Analytics } from '../lib/types.ts';

export function AnalyticsPage() {
  const [data, setData] = useState<Analytics | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .analytics()
      .then(setData)
      .catch((err) => setError((err as ApiError).message));
  }, []);

  if (error) return <Banner tone="error">{error}</Banner>;
  if (!data)
    return (
      <Card>
        <Spinner label="Computing analytics..." />
      </Card>
    );

  const { totals } = data;

  return (
    <div className="space-y-6">
      {data.notes.map((note) => (
        <Banner key={note} tone="info">
          {note}
        </Banner>
      ))}

      <Card>
        <SectionTitle>Totals</SectionTitle>
        <div className="grid grid-cols-2 gap-x-8 gap-y-5 sm:grid-cols-3 lg:grid-cols-6">
          <Stat label="Executions" value={totals.executions} />
          <Stat
            label="Success rate"
            value={pct(totals.success_rate)}
            hint={`${totals.successes} of ${totals.executions}`}
          />
          <Stat label="First pass" value={pct(totals.first_pass_rate)} />
          <Stat
            label="Failure rate"
            value={pct(totals.failure_rate)}
            tone={totals.failure_rate && totals.failure_rate > 0.2 ? 'text-rose-300' : undefined}
          />
          <Stat label="Escalation rate" value={pct(totals.escalation_rate)} />
          <Stat
            label="Followed advice"
            value={pct(totals.recommendation_followed_rate)}
            hint={`${totals.recommendation_followed} of ${totals.executions}`}
          />
        </div>
      </Card>

      {data.findings.length > 0 && (
        <Card>
          <SectionTitle>Patterns worth acting on</SectionTitle>
          <div className="space-y-3">
            {data.findings.map((finding, index) => (
              <div
                key={`${finding.kind}-${finding.configuration}-${index}`}
                className="rounded-lg border border-slate-800 bg-slate-950/50 p-4"
              >
                <div className="flex flex-wrap items-center gap-3">
                  <Pill tone={finding.kind === 'underpowered' ? 'rose' : 'amber'}>
                    {finding.kind}
                  </Pill>
                  <span className="text-sm font-medium text-slate-200">
                    {finding.configuration}
                  </span>
                  {finding.alternative && (
                    <span className="text-xs text-slate-500">
                      consider {finding.alternative} instead
                    </span>
                  )}
                  <span className="ml-auto text-xs text-slate-600">
                    {finding.evidence} executions
                  </span>
                </div>
                <p className="mt-2 text-sm leading-relaxed text-slate-400">{finding.detail}</p>
              </div>
            ))}
          </div>
          <p className="mt-4 text-xs text-slate-500">
            These are observations from your recorded outcomes, not automatic changes. The router
            already folds this evidence into its own estimates; act on it by hand only if a
            pattern is strong and persistent.
          </p>
        </Card>
      )}

      <Card className="p-0">
        <div className="px-5 pt-5">
          <SectionTitle>Configuration performance by task family</SectionTitle>
        </div>
        {data.by_configuration.length === 0 ? (
          <div className="px-5 pb-5">
            <Empty>Nothing recorded yet.</Empty>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[900px] text-left text-sm">
              <thead>
                <tr className="border-b border-slate-800 text-[11px] uppercase tracking-[0.1em] text-slate-500">
                  <th className="px-5 py-3 font-medium">Task family</th>
                  <th className="px-4 py-3 font-medium">Configuration</th>
                  <th className="px-4 py-3 text-right font-medium">Runs</th>
                  <th className="px-4 py-3 text-right font-medium">Raw success</th>
                  <th className="px-4 py-3 text-right font-medium">Smoothed</th>
                  <th className="px-4 py-3 text-right font-medium">First pass</th>
                  <th className="px-4 py-3 text-right font-medium">Escalations</th>
                  <th className="px-4 py-3 text-right font-medium">Debug</th>
                  <th className="px-5 py-3 text-right font-medium">Avg burn</th>
                </tr>
              </thead>
              <tbody>
                {data.by_configuration.map((row, index) => (
                  <tr
                    key={`${row.task_family}-${row.provider_display}-${row.model_display}-${row.effort}-${index}`}
                    className="border-b border-slate-900"
                  >
                    <td className="px-5 py-2.5 text-slate-400">{titleCase(row.task_family)}</td>
                    <td className="px-4 py-2.5 text-slate-200">
                      {row.provider_display} {row.model_display}{' '}
                      <span className="text-slate-500">{row.effort}</span>
                    </td>
                    <td className="px-4 py-2.5 text-right font-mono text-xs">{row.attempts}</td>
                    <td className="px-4 py-2.5 text-right font-mono text-xs">
                      {pct(row.success_rate)}
                    </td>
                    <td className="px-4 py-2.5 text-right font-mono text-xs text-sky-300">
                      {pct(row.smoothed_success_rate)}
                    </td>
                    <td className="px-4 py-2.5 text-right font-mono text-xs">
                      {row.first_pass_successes}
                    </td>
                    <td className="px-4 py-2.5 text-right font-mono text-xs">
                      {row.escalations}
                    </td>
                    <td className="px-4 py-2.5 text-right font-mono text-xs">
                      {row.avg_debug_cycles?.toFixed(1) ?? '--'}
                    </td>
                    <td className="px-5 py-2.5 text-right font-mono text-xs">
                      {burn(row.avg_effective_burn)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="px-5 pb-5 pt-4 text-xs text-slate-500">
          &quot;Smoothed&quot; applies Bayesian shrinkage toward a neutral prior, so one lucky run
          does not read as a 100% success rate. Average burn is an estimate reconstructed from
          work signals; it is not a token measurement.
        </p>
      </Card>
    </div>
  );
}
