import { useEffect, useRef, useState } from 'react';

import { Banner, Button, Card, Pill, SectionTitle, Stat } from '../components/ui.tsx';
import { ApiError, api, copyToClipboard } from '../lib/api.ts';
import { band, bandTone, burn, effortLabel, pct, titleCase } from '../lib/format.ts';
import type { PiStatus, TaskResponse } from '../lib/types.ts';

/**
 * Staged progress messages.
 *
 * Analysis takes a real model call and can run for tens of seconds. These
 * describe what is actually happening in order: Pi classifies, then the
 * deterministic engine does the rest locally and fast.
 */
const STAGES = [
  'Sending the task to the analyzer...',
  'Classifying complexity and risk...',
  'Calculating the required reliability...',
  'Comparing every model and effort combination...',
  'Checking historical performance...',
];

export function RouterPage({
  pi,
  preferences,
}: {
  pi: PiStatus | null;
  preferences: { analyzer_source: string; allow_analyzer_fallback: boolean } | null;
}) {
  const [task, setTask] = useState('');
  const [busy, setBusy] = useState(false);
  const [stage, setStage] = useState(0);
  const [result, setResult] = useState<TaskResponse | null>(null);
  const [error, setError] = useState<{ message: string; detail?: unknown } | null>(null);
  const [copied, setCopied] = useState<string | null>(null);
  const [showDetails, setShowDetails] = useState(false);
  const timer = useRef<number | null>(null);

  useEffect(() => () => { if (timer.current) window.clearInterval(timer.current); }, []);

  const analyzerSource = preferences?.analyzer_source ?? 'pi';
  const allowFallback = preferences?.allow_analyzer_fallback ?? false;
  const piDown = analyzerSource === 'pi' && pi !== null && !pi.available;

  async function analyze() {
    if (!task.trim() || busy) return;
    setBusy(true);
    setError(null);
    setResult(null);
    setShowDetails(false);
    setStage(0);

    timer.current = window.setInterval(
      () => setStage((s) => Math.min(s + 1, STAGES.length - 1)),
      1400,
    );

    try {
      const response = await api.createTask(task, {
        analyzer: analyzerSource,
        allowFallback,
      });
      setResult(response);
    } catch (err) {
      const apiError = err as ApiError;
      setError({ message: apiError.message, detail: apiError.detail });
    } finally {
      if (timer.current) window.clearInterval(timer.current);
      timer.current = null;
      setBusy(false);
    }
  }

  async function copy(kind: 'handoff' | 'task') {
    if (!result) return;
    const text = kind === 'handoff' ? (result.handoff ?? '') : result.original_task;
    const ok = await copyToClipboard(text);
    setCopied(ok ? kind : 'failed');
    window.setTimeout(() => setCopied(null), 2200);
  }

  const decision = result?.recommendation ?? null;
  const fingerprint = result?.fingerprint ?? null;

  return (
    <div className="space-y-6">
      {piDown && (
        <Banner tone="warn" title="Pi bridge unavailable">
          {pi?.note ?? 'The analyzer is not reachable.'} Start it with{' '}
          <code className="rounded bg-slate-800 px-1">scripts/start-pi.ps1</code>, or enable the
          offline fallback analyzer in Settings.
        </Banner>
      )}

      <Card>
        <label
          htmlFor="task-input"
          className="mb-3 block text-lg font-medium text-slate-100"
        >
          What do you want the AI to do?
        </label>
        <textarea
          id="task-input"
          value={task}
          onChange={(event) => setTask(event.target.value)}
          onKeyDown={(event) => {
            if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') void analyze();
          }}
          rows={9}
          spellCheck={false}
          placeholder="Paste the coding task exactly as you would give it to Claude or Codex."
          className="w-full resize-y rounded-lg border border-slate-800 bg-slate-950/70 p-4 font-mono text-sm text-slate-200 outline-none placeholder:text-slate-600 focus:border-sky-700"
        />
        <div className="mt-4 flex items-center gap-4">
          <Button onClick={() => void analyze()} disabled={busy || !task.trim()}>
            {busy ? 'Analyzing...' : 'Analyze task'}
          </Button>
          <span className="text-xs text-slate-500">
            {busy ? STAGES[stage] : 'Ctrl+Enter to analyze'}
          </span>
        </div>
      </Card>

      {error && (
        <Banner tone="error" title="Analysis failed">
          <p>{error.message}</p>
          {typeof error.detail === 'string' && error.detail && (
            <p className="mt-2 font-mono text-xs opacity-80">{error.detail}</p>
          )}
        </Banner>
      )}

      {result?.analyzer?.fallback_used && (
        <Banner tone="warn" title="Offline analyzer was used">
          {result.analyzer.note ??
            'The Pi bridge was unreachable, so a keyword analyzer classified this task.'}
        </Banner>
      )}

      {result && decision && fingerprint && (
        <>
          <Card className="border-sky-900/60 bg-gradient-to-b from-sky-950/30 to-slate-900/60">
            <div className="flex flex-wrap items-start justify-between gap-6">
              <div>
                <SectionTitle>Recommended</SectionTitle>
                <div className="text-xs uppercase tracking-[0.2em] text-sky-400">
                  {decision.provider_display}
                </div>
                <div className="mt-1 text-4xl font-semibold text-slate-50">
                  {decision.model_display}
                </div>
                <div className="mt-1 text-lg text-slate-300">
                  {effortLabel(decision.effort)} reasoning
                </div>
                <div className="mt-3 font-mono text-xs text-slate-500">
                  {result.public_task_id}
                </div>
              </div>

              <div className="grid grid-cols-2 gap-x-10 gap-y-4 sm:grid-cols-3">
                <Stat label="Confidence" value={pct(decision.confidence)} />
                <Stat
                  label="Task family"
                  value={titleCase(fingerprint.task_family)}
                />
                <Stat
                  label="Complexity"
                  value={band(fingerprint.complexity)}
                  tone={bandTone(fingerprint.complexity)}
                />
                <Stat
                  label="Regression risk"
                  value={band(fingerprint.regression_risk)}
                  tone={bandTone(fingerprint.regression_risk)}
                />
                <Stat
                  label="Reliability"
                  value={`${pct(decision.predicted_reliability, 1)}`}
                  hint={`needs ${pct(decision.required_reliability, 1)}`}
                />
                <Stat
                  label="Expected burn"
                  value={burn(decision.predicted_burn)}
                  hint="normalised quota units"
                />
              </div>
            </div>

            {!decision.threshold_met && (
              <div className="mt-5">
                <Banner tone="warn" title="Nothing clears the bar">
                  No available configuration reaches the reliability this task requires. The
                  strongest option was selected. Consider splitting the task into smaller pieces.
                </Banner>
              </div>
            )}

            <div className="mt-6 flex flex-wrap items-center gap-3">
              <Button onClick={() => void copy('handoff')}>
                {copied === 'handoff' ? 'Copied' : 'Copy handoff'}
              </Button>
              <Button variant="secondary" onClick={() => void copy('task')}>
                {copied === 'task' ? 'Copied' : 'Copy task only'}
              </Button>
              {decision.fallback_display && (
                <span className="text-xs text-slate-500">
                  Fallback: {decision.fallback_display}
                </span>
              )}
              {copied === 'failed' && (
                <span className="text-xs text-rose-300">
                  The browser blocked clipboard access. Select the handoff below and copy manually.
                </span>
              )}
            </div>
          </Card>

          <div className="grid gap-4 lg:grid-cols-3">
            <Card>
              <SectionTitle>Why</SectionTitle>
              <p className="text-sm leading-relaxed text-slate-300">
                {decision.explanation.why}
              </p>
            </Card>
            <Card>
              <SectionTitle>Why not lighter?</SectionTitle>
              <p className="text-sm leading-relaxed text-slate-300">
                {decision.explanation.why_not_lighter}
              </p>
            </Card>
            <Card>
              <SectionTitle>Why not stronger?</SectionTitle>
              <p className="text-sm leading-relaxed text-slate-300">
                {decision.explanation.why_not_stronger}
              </p>
            </Card>
          </div>

          <Card>
            <div className="flex flex-wrap items-center justify-between gap-3">
              <p className="text-sm text-slate-400">{decision.explanation.evidence_note}</p>
              <Button variant="ghost" onClick={() => setShowDetails((value) => !value)}>
                {showDetails ? 'Hide details' : 'Show the arithmetic'}
              </Button>
            </div>

            {showDetails && (
              <div className="mt-5 space-y-6 border-t border-slate-800 pt-5">
                <div>
                  <SectionTitle>Handoff</SectionTitle>
                  <pre className="max-h-56 overflow-auto rounded-lg border border-slate-800 bg-slate-950 p-3 font-mono text-[11px] leading-relaxed text-slate-300">
                    {result.handoff}
                  </pre>
                </div>

                <div>
                  <SectionTitle>Reason codes</SectionTitle>
                  <div className="flex flex-wrap gap-2">
                    {decision.reason_codes.map((code) => (
                      <Pill key={code}>{code.toLowerCase().replaceAll('_', ' ')}</Pill>
                    ))}
                  </div>
                </div>

                <div>
                  <SectionTitle>Fingerprint</SectionTitle>
                  <div className="grid grid-cols-2 gap-x-8 gap-y-1.5 text-xs sm:grid-cols-3">
                    {(
                      [
                        ['complexity', fingerprint.complexity],
                        ['ambiguity', fingerprint.ambiguity],
                        ['requirements clarity', fingerprint.requirements_clarity],
                        ['regression risk', fingerprint.regression_risk],
                        ['architecture reasoning', fingerprint.architecture_reasoning],
                        ['database reasoning', fingerprint.database_reasoning],
                        ['concurrency risk', fingerprint.concurrency_risk],
                        ['security risk', fingerprint.security_risk],
                        ['repository understanding', fingerprint.repository_understanding],
                        ['analyzer confidence', fingerprint.confidence],
                      ] as const
                    ).map(([label, value]) => (
                      <div key={label} className="flex justify-between gap-3">
                        <span className="text-slate-500">{label}</span>
                        <span className="font-mono text-slate-300">{value.toFixed(2)}</span>
                      </div>
                    ))}
                    <div className="flex justify-between gap-3">
                      <span className="text-slate-500">scope</span>
                      <span className="font-mono text-slate-300">{fingerprint.scope}</span>
                    </div>
                    <div className="flex justify-between gap-3">
                      <span className="text-slate-500">estimated files</span>
                      <span className="font-mono text-slate-300">
                        {fingerprint.estimated_files}
                      </span>
                    </div>
                    <div className="flex justify-between gap-3">
                      <span className="text-slate-500">difficulty</span>
                      <span className="font-mono text-slate-300">
                        {decision.risk.difficulty.toFixed(3)}
                      </span>
                    </div>
                  </div>
                  <p className="mt-3 text-xs text-slate-500">
                    Classified by {result.analyzer?.provider ?? 'unknown'}/
                    {result.analyzer?.model ?? 'unknown'}
                    {result.analyzer?.repaired && ' (after one schema repair)'}.
                  </p>
                </div>

                <div>
                  <SectionTitle>Every configuration considered</SectionTitle>
                  <div className="overflow-x-auto">
                    <table className="w-full text-left text-xs">
                      <thead className="text-slate-500">
                        <tr className="border-b border-slate-800">
                          <th className="py-2 pr-4 font-medium">Configuration</th>
                          <th className="py-2 pr-4 font-medium">Reliability</th>
                          <th className="py-2 pr-4 font-medium">Base burn</th>
                          <th className="py-2 pr-4 font-medium">Effective burn</th>
                          <th className="py-2 font-medium">Eligible</th>
                        </tr>
                      </thead>
                      <tbody className="font-mono">
                        {decision.evaluated.map((score) => {
                          const isWinner =
                            score.configuration.provider === decision.provider &&
                            score.configuration.model === decision.model &&
                            score.configuration.effort === decision.effort;
                          return (
                            <tr
                              key={
                                score.configuration.provider +
                                score.configuration.model +
                                score.configuration.effort
                              }
                              className={`border-b border-slate-900 ${
                                isWinner ? 'bg-sky-950/40 text-sky-200' : 'text-slate-400'
                              }`}
                            >
                              <td className="py-1.5 pr-4">
                                {score.provider_display} {score.model_display}{' '}
                                {score.configuration.effort}
                                {isWinner && ' <-- selected'}
                              </td>
                              <td className="py-1.5 pr-4">
                                {pct(score.predicted_reliability, 1)}
                              </td>
                              <td className="py-1.5 pr-4">{burn(score.base_burn)}</td>
                              <td className="py-1.5 pr-4">{burn(score.predicted_burn)}</td>
                              <td className="py-1.5">{score.eligible ? 'yes' : 'no'}</td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                  <p className="mt-3 text-xs text-slate-500">
                    Burn is in normalised quota units, not currency, and is an estimate rather
                    than a measurement. Effort intensity uses this application's own routing
                    priors; neither provider publishes a fixed token multiplier per effort level.
                  </p>
                </div>
              </div>
            )}
          </Card>
        </>
      )}
    </div>
  );
}
