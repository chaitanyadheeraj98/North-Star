import { useCallback, useEffect, useState } from 'react';

import { Banner, Button, Card, Empty, Pill, SectionTitle, Spinner } from '../components/ui.tsx';
import { ApiError, api } from '../lib/api.ts';
import { burn, formatDate, outcomeTone, pct, titleCase } from '../lib/format.ts';
import type { HistoryRow, TaskResponse } from '../lib/types.ts';

const PAGE_SIZE = 25;

export function HistoryPage() {
  const [rows, setRows] = useState<HistoryRow[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<TaskResponse | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  const load = useCallback(async (nextOffset: number) => {
    setLoading(true);
    setError(null);
    try {
      const response = await api.history({ limit: PAGE_SIZE, offset: nextOffset });
      setRows(response.rows);
      setTotal(response.total);
      setOffset(nextOffset);
    } catch (err) {
      setError((err as ApiError).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load(0);
  }, [load]);

  async function openDetail(taskId: string) {
    setDetailLoading(true);
    try {
      setSelected(await api.getTask(taskId));
    } catch (err) {
      setError((err as ApiError).message);
    } finally {
      setDetailLoading(false);
    }
  }

  if (loading && rows.length === 0) {
    return (
      <Card>
        <Spinner label="Loading history..." />
      </Card>
    );
  }

  return (
    <div className="space-y-6">
      {error && <Banner tone="error">{error}</Banner>}

      {rows.length === 0 ? (
        <Empty>
          No tasks yet. Route one from the Router tab, run it, then import the receipt.
        </Empty>
      ) : (
        <Card className="overflow-x-auto p-0">
          <table className="w-full min-w-[980px] text-left text-sm">
            <thead>
              <tr className="border-b border-slate-800 text-[11px] uppercase tracking-[0.1em] text-slate-500">
                <th className="px-4 py-3 font-medium">Task</th>
                <th className="px-4 py-3 font-medium">Family</th>
                <th className="px-4 py-3 font-medium">Recommended</th>
                <th className="px-4 py-3 font-medium">Actual</th>
                <th className="px-4 py-3 font-medium">Outcome</th>
                <th className="px-4 py-3 font-medium">Followed</th>
                <th className="px-4 py-3 text-right font-medium">Burn</th>
                <th className="px-4 py-3 font-medium">Date</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr
                  key={row.public_task_id}
                  onClick={() => void openDetail(row.public_task_id)}
                  className="cursor-pointer border-b border-slate-900 transition-colors hover:bg-slate-800/40"
                >
                  <td className="px-4 py-3">
                    <div className="max-w-[280px] truncate text-slate-200" title={row.title}>
                      {row.title}
                    </div>
                    <div className="font-mono text-[11px] text-slate-600">
                      {row.public_task_id}
                    </div>
                  </td>
                  <td className="px-4 py-3 text-slate-400">
                    {row.task_family ? titleCase(row.task_family) : '--'}
                  </td>
                  <td className="px-4 py-3 text-slate-300">{row.recommended_display ?? '--'}</td>
                  <td className="px-4 py-3 text-slate-300">{row.actual_display ?? 'not run'}</td>
                  <td className={`px-4 py-3 ${outcomeTone(row.outcome)}`}>
                    {row.outcome ? titleCase(row.outcome) : '--'}
                    {row.escalated && (
                      <span className="ml-2 text-[11px] text-amber-400">escalated</span>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    {row.recommendation_followed === null ? (
                      <span className="text-slate-600">--</span>
                    ) : row.recommendation_followed ? (
                      <Pill tone="emerald">yes</Pill>
                    ) : (
                      <Pill tone="amber">no</Pill>
                    )}
                  </td>
                  <td className="px-4 py-3 text-right font-mono text-xs text-slate-400">
                    {row.estimated_effective_burn !== null
                      ? burn(row.estimated_effective_burn)
                      : row.predicted_burn !== null
                        ? `~${burn(row.predicted_burn)}`
                        : '--'}
                  </td>
                  <td className="px-4 py-3 text-xs text-slate-500">
                    {formatDate(row.executed_at ?? row.created_at)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}

      {total > PAGE_SIZE && (
        <div className="flex items-center justify-between text-sm text-slate-500">
          <span>
            {offset + 1}-{Math.min(offset + PAGE_SIZE, total)} of {total}
          </span>
          <div className="flex gap-2">
            <Button
              variant="secondary"
              disabled={offset === 0}
              onClick={() => void load(Math.max(0, offset - PAGE_SIZE))}
            >
              Previous
            </Button>
            <Button
              variant="secondary"
              disabled={offset + PAGE_SIZE >= total}
              onClick={() => void load(offset + PAGE_SIZE)}
            >
              Next
            </Button>
          </div>
        </div>
      )}

      {(selected || detailLoading) && (
        <Card>
          <div className="flex items-start justify-between gap-4">
            <h3 className="text-base font-medium text-slate-100">
              {detailLoading ? 'Loading...' : selected?.public_task_id}
            </h3>
            <Button variant="ghost" onClick={() => setSelected(null)}>
              Close
            </Button>
          </div>

          {selected && !detailLoading && (
            <div className="mt-4 space-y-5">
              <div>
                <SectionTitle>Task</SectionTitle>
                <pre className="max-h-48 overflow-auto whitespace-pre-wrap rounded-lg border border-slate-800 bg-slate-950 p-3 font-mono text-[11px] leading-relaxed text-slate-300">
                  {selected.original_task}
                </pre>
              </div>

              {selected.recommendation && (
                <div>
                  <SectionTitle>Recommendation</SectionTitle>
                  <p className="text-sm text-slate-300">
                    {selected.recommendation.provider_display}{' '}
                    {selected.recommendation.model_display} at{' '}
                    {selected.recommendation.effort} effort, predicted{' '}
                    {pct(selected.recommendation.predicted_reliability, 1)} against a{' '}
                    {pct(selected.recommendation.required_reliability, 1)} requirement.
                  </p>
                  <p className="mt-2 text-sm leading-relaxed text-slate-400">
                    {selected.recommendation.explanation.why}
                  </p>
                </div>
              )}

              {selected.execution && (
                <div>
                  <SectionTitle>Execution</SectionTitle>
                  <pre className="overflow-auto rounded-lg border border-slate-800 bg-slate-950 p-3 font-mono text-[11px] text-slate-300">
                    {JSON.stringify(selected.execution, null, 2)}
                  </pre>
                </div>
              )}
            </div>
          )}
        </Card>
      )}
    </div>
  );
}
