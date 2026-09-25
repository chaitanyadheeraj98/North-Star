import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { ApiError, api } from '../lib/api.ts';
import type { ImportResultResponse } from '../lib/types.ts';
import { ResultPage } from './ResultPage.tsx';

const RECEIPT = '[LLM-ROUTER-RESULT]\n{"task_id":"RT-000001"}\n[/LLM-ROUTER-RESULT]';

function importResponse(overrides: Partial<ImportResultResponse> = {}): ImportResultResponse {
  return {
    task_id: 'RT-000001',
    execution_id: 1,
    task_family: 'backend_data_integrity',
    recommended: 'codex/sol/high',
    actual: 'codex/sol/high',
    recommendation_followed: true,
    status: 'success',
    first_pass_success: true,
    escalated: false,
    debug_cycles: 0,
    estimated_effective_burn: 3.0,
    usage_source: 'unavailable',
    replaced_previous: false,
    learning_summary: 'First recorded execution for codex/sol/high.',
    warnings: [],
    statistics: {},
    ...overrides,
  };
}

afterEach(() => vi.restoreAllMocks());

describe('ResultPage', () => {
  it('disables import until a receipt is pasted', () => {
    render(<ResultPage />);
    expect(screen.getByRole('button', { name: /import result/i })).toBeDisabled();
  });

  it('imports a receipt and reports what was learned', async () => {
    const spy = vi.spyOn(api, 'importResult').mockResolvedValue(importResponse());
    const onImported = vi.fn();
    const user = userEvent.setup();

    render(<ResultPage onImported={onImported} />);
    await user.click(screen.getByLabelText(/execution receipt/i));
    await user.paste(RECEIPT);
    await user.click(screen.getByRole('button', { name: /import result/i }));

    await waitFor(() => expect(screen.getByText(/result learned/i)).toBeInTheDocument());
    expect(spy).toHaveBeenCalledWith(RECEIPT);
    expect(onImported).toHaveBeenCalledOnce();
    expect(screen.getByText(/first recorded execution/i)).toBeInTheDocument();
  });

  it('surfaces a deviation warning rather than hiding it', async () => {
    vi.spyOn(api, 'importResult').mockResolvedValue(
      importResponse({
        recommendation_followed: false,
        actual: 'codex/terra/medium',
        warnings: ['Recommendation was codex/sol/high; you ran codex/terra/medium.'],
      }),
    );
    const user = userEvent.setup();

    render(<ResultPage />);
    await user.click(screen.getByLabelText(/execution receipt/i));
    await user.paste(RECEIPT);
    await user.click(screen.getByRole('button', { name: /import result/i }));

    await waitFor(() =>
      expect(screen.getByText(/you ran codex\/terra\/medium/i)).toBeInTheDocument(),
    );
  });

  it('shows a validation failure without clearing what the user typed', async () => {
    vi.spyOn(api, 'importResult').mockRejectedValue(
      new ApiError('No task named RT-000999 exists in this router.', 422),
    );
    const user = userEvent.setup();

    render(<ResultPage />);
    const input = screen.getByLabelText(/execution receipt/i);
    await user.click(input);
    await user.paste(RECEIPT);
    await user.click(screen.getByRole('button', { name: /import result/i }));

    await waitFor(() => expect(screen.getByText(/rt-000999/i)).toBeInTheDocument());
    expect(input).toHaveValue(RECEIPT);
  });

  it('says plainly when usage was not reported, instead of implying a measurement', async () => {
    vi.spyOn(api, 'importResult').mockResolvedValue(importResponse());
    const user = userEvent.setup();

    render(<ResultPage />);
    await user.click(screen.getByLabelText(/execution receipt/i));
    await user.paste(RECEIPT);
    await user.click(screen.getByRole('button', { name: /import result/i }));

    await waitFor(() =>
      expect(
        screen.getByText(/recorded as unavailable rather than estimated/i),
      ).toBeInTheDocument(),
    );
  });
});
