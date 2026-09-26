import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';

import { App } from '../App.tsx';
import { ApiError, api } from '../lib/api.ts';
import type { RoutingDecision, SettingsResponse } from '../lib/types.ts';
import { RouterPage } from './RouterPage.tsx';
import { SettingsPage } from './SettingsPage.tsx';

afterEach(() => vi.restoreAllMocks());

function decision(provider: string): RoutingDecision {
  return {
    router_version: '1.1.0', registry_version: '1.0.0', provider,
    model: `${provider}-model`, effort: 'high', provider_display: provider,
    model_display: `${provider} recommended model`, confidence: 0.8,
    required_reliability: 0.9, predicted_reliability: 0.95, predicted_burn: 2,
    risk: { difficulty: 0.5, difficulty_contributions: {}, required_reliability: 0.9,
      required_reliability_contributions: {}, dominant_risks: [] },
    explanation: { summary: 'summary', why: 'why', why_not_lighter: 'lighter',
      why_not_stronger: 'stronger', evidence_note: 'limited evidence', reason_codes: [] },
    reason_codes: [], fallback: { provider, model: `${provider}-backup`, effort: 'medium' },
    fallback_display: `${provider} backup (medium)`, fallback_threshold_met: true,
    rejected_lighter: null, rejected_stronger: null, evaluated: [], threshold_met: true,
    evidence_band: 'none', evidence_weight: 0,
  };
}

it('shows independent provider recommendations and copies the chosen handoff', async () => {
  const user = userEvent.setup();
  const clipboard = vi.spyOn(navigator.clipboard, 'writeText').mockResolvedValue();
  vi.spyOn(api, 'createTask').mockResolvedValue({
    public_task_id: 'RT-000123', original_task: 'Fix the bug', title: 'Fix the bug',
    status: 'routed', created_at: '2026-09-25', analyzer: null,
    fingerprint: { schema_version: '1.0', task_family: 'backend', task_type: 'bug_fix',
      complexity: 0.5, regression_risk: 0.5, ambiguity: 0.2, requirements_clarity: 0.8,
      scope: 'single_file', estimated_files: 1, architecture_reasoning: 0.2,
      database_reasoning: 0.2, concurrency_risk: 0.2, security_risk: 0.2,
      repository_understanding: 0.3, confidence: 0.9, frontend: false, backend: true,
      database: false, infrastructure: false, tests_required: 'medium' },
    recommendation: { router_version: '1.1.0', registry_version: '1.0.0',
      codex: decision('codex'), claude: decision('claude'), unavailable: {}, legacy: false },
    handoffs: { codex: 'codex handoff', claude: 'claude handoff' },
  });
  render(<RouterPage pi={null} preferences={{ analyzer_source: 'heuristic', allow_analyzer_fallback: false }} />);
  await user.type(screen.getByLabelText(/what do you want/i), 'Fix the bug');
  await user.click(screen.getByRole('button', { name: 'Analyze task' }));
  for (const provider of ['codex', 'claude']) {
    const card = await screen.findByRole('region', { name: `${provider} recommendation` });
    expect(within(card).getByText(`${provider} recommended model`)).toBeInTheDocument();
    expect(within(card).getByText(`Fallback: ${provider} backup (medium)`)).toBeInTheDocument();
    await user.click(within(card).getByRole('button', { name: `Copy ${provider} handoff` }));
    expect(clipboard).toHaveBeenLastCalledWith(`${provider} handoff`);
  }
});

function settings(): SettingsResponse {
  return {
    app_version: '1.0.0', router_version: '1.1.0', registry_version: '1.0.0',
    config_dir: 'config', database_url: 'sqlite', pi_bridge_url: 'localhost',
    pi: { available: false, authenticated: false, status: 'unavailable', provider: null, model: null },
    providers: [], task_families: [], policy: {},
    preferences: { analyzer_source: 'heuristic', allow_analyzer_fallback: false,
      analyzer_provider: null, analyzer_model: null, analyzer_effort: null },
  };
}

it('updates models from Settings, refreshes state, and displays the result', async () => {
  const user = userEvent.setup();
  const read = vi.spyOn(api, 'settings').mockResolvedValue(settings());
  vi.spyOn(api, 'piModels').mockResolvedValue({ models: [] });
  const update = vi.spyOn(api, 'updateModels').mockResolvedValue({
    status: 'updated', registry_version: '1.0.1', added: ['codex/new-model'], changed: [],
    unconfirmed: ['claude/older-model'], researched: ['codex/new-model'], warnings: [], sources: [],
    backup: 'models.1.0.0.yaml.bak',
  });
  const changed = vi.fn();
  render(<SettingsPage onChanged={changed} />);
  await user.click(await screen.findByRole('button', { name: 'Update Models' }));
  expect(await screen.findByRole('status')).toHaveTextContent('Models updated');
  expect(screen.getByRole('status')).toHaveTextContent('codex/new-model');
  expect(screen.getByRole('status')).toHaveTextContent('Researched and enabled: codex/new-model');
  expect(screen.getByRole('status')).toHaveTextContent('models.1.0.0.yaml.bak');
  expect(update).toHaveBeenCalledOnce();
  await waitFor(() => expect(changed).toHaveBeenCalledOnce());
  expect(read).toHaveBeenCalledTimes(2);
});

it('shows an update failure and enables retry', async () => {
  const user = userEvent.setup();
  vi.spyOn(api, 'settings').mockResolvedValue(settings());
  vi.spyOn(api, 'piModels').mockResolvedValue({ models: [] });
  vi.spyOn(api, 'updateModels').mockRejectedValue(new ApiError('Original registry restored.', 422));
  render(<SettingsPage />);
  await user.click(await screen.findByRole('button', { name: 'Update Models' }));
  expect(await screen.findByText('Original registry restored.')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Update Models' })).toBeEnabled();
});

it('keeps the task when switching tabs', async () => {
  const user = userEvent.setup();
  vi.spyOn(api, 'settings').mockResolvedValue(settings());
  vi.spyOn(api, 'piModels').mockResolvedValue({ models: [] });
  render(<App />);
  await user.type(screen.getByLabelText(/what do you want/i), 'Fix the bug');
  await user.click(screen.getByRole('button', { name: 'settings' }));
  await screen.findByRole('button', { name: 'Update Models' });
  await user.click(screen.getByRole('button', { name: 'router' }));
  expect(screen.getByLabelText(/what do you want/i)).toHaveValue('Fix the bug');
});
