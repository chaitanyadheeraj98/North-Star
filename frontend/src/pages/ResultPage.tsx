import { useState } from 'react';

import { Banner, Button, Card, Pill, SectionTitle, Stat } from '../components/ui.tsx';
import { ApiError, api } from '../lib/api.ts';
import { burn, titleCase } from '../lib/format.ts';
import type { ImportResultResponse } from '../lib/types.ts';

const PLACEHOLDER = `[LLM-ROUTER-RESULT]
{
  "receipt_version": "1.0",
  "task_id": "RT-000001",
  ...
}
[/LLM-ROUTER-RESULT]`;

export function ResultPage({ onImported }: { onImported?: () => void }) {
  const [receipt, setReceipt] = useState('');
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<ImportResultResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    if (!receipt.trim() || busy) return;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const response = await api.importResult(receipt);
      setResult(response);
      setReceipt('');
      onImported?.();
    } catch (err) {
      setError((err as ApiError).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-6">
      <Card>
        <h2 className="text-lg font-medium text-slate-100">Finished the task?</h2>
        <p className="mt-1 text-sm text-slate-400">
          Run the <code className="rounded bg-slate-800 px-1">router-outcome</code> skill in
          Claude or Codex (or just ask it to &quot;generate the router result receipt&quot;), then
          paste the whole block below.
        </p>

        <label htmlFor="receipt-input" className="sr-only">
          Execution receipt
        </label>
        <textarea
          id="receipt-input"
          value={receipt}
          onChange={(event) => setReceipt(event.target.value)}
          onKeyDown={(event) => {
            if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') void submit();
          }}
          rows={14}
          spellCheck={false}
          placeholder={PLACEHOLDER}
          className="mt-4 w-full resize-y rounded-lg border border-slate-800 bg-slate-950/70 p-4 font-mono text-xs text-slate-200 outline-none placeholder:text-slate-700 focus:border-sky-700"
        />

        <div className="mt-4 flex items-center gap-4">
          <Button onClick={() => void submit()} disabled={busy || !receipt.trim()}>
            {busy ? 'Importing...' : 'Import result'}
          </Button>
          <span className="text-xs text-slate-500">
            A receipt for a configuration you were not recommended is still worth importing.
          </span>
        </div>
      </Card>

      {error && (
        <Banner tone="error" title="Could not import that receipt">
          {error}
        </Banner>
      )}

      {result && (
        <Card className="border-emerald-900/60">
          <div className="flex items-center gap-3">
            <h2 className="text-lg font-medium text-emerald-300">Result learned</h2>
            <Pill tone="slate">{result.task_id}</Pill>
          </div>

          <div className="mt-5 grid grid-cols-2 gap-x-8 gap-y-5 sm:grid-cols-3 lg:grid-cols-5">
            <Stat
              label="Outcome"
              value={titleCase(result.status)}
              tone={
                result.status === 'success'
                  ? 'text-emerald-300'
                  : result.status === 'partial'
                    ? 'text-amber-300'
                    : 'text-rose-300'
              }
            />
            <Stat
              label="Followed recommendation"
              value={result.recommendation_followed ? 'Yes' : 'No'}
            />
            <Stat label="First pass" value={result.first_pass_success ? 'Yes' : 'No'} />
            <Stat label="Escalated" value={result.escalated ? 'Yes' : 'No'} />
            <Stat label="Debug cycles" value={result.debug_cycles} />
          </div>

          <div className="mt-5 grid gap-4 border-t border-slate-800 pt-5 sm:grid-cols-2">
            <div>
              <SectionTitle>Recommended</SectionTitle>
              <div className="font-mono text-sm text-slate-300">
                {result.recommended ?? 'no recommendation on record'}
              </div>
            </div>
            <div>
              <SectionTitle>Actually run</SectionTitle>
              <div className="font-mono text-sm text-slate-300">{result.actual}</div>
            </div>
          </div>

          <div className="mt-5 border-t border-slate-800 pt-5">
            <SectionTitle>What this changed</SectionTitle>
            <p className="text-sm leading-relaxed text-slate-300">{result.learning_summary}</p>
            <p className="mt-3 text-xs text-slate-500">
              Estimated effective burn {burn(result.estimated_effective_burn)} quota units,
              reconstructed from the work signals in the receipt. Token usage was{' '}
              {result.usage_source === 'provider_reported'
                ? 'reported by the provider and stored as measured.'
                : 'not reported by the provider, so it is recorded as unavailable rather than estimated.'}
            </p>
          </div>

          {result.warnings.length > 0 && (
            <div className="mt-5 space-y-2 border-t border-slate-800 pt-5">
              {result.warnings.map((warning) => (
                <Banner key={warning} tone="warn">
                  {warning}
                </Banner>
              ))}
            </div>
          )}
        </Card>
      )}
    </div>
  );
}
