import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { api } from '../lib/api.ts';
import type { TaskResponse } from '../lib/types.ts';
import { RouterPage } from './RouterPage.tsx';

const PREFS = { analyzer_source: 'heuristic', allow_analyzer_fallback: false };

/** A FileReader whose reads finish only when the test says so. */
const RealFileReader = globalThis.FileReader;
const pending: Array<() => void> = [];
class ManualFileReader {
  result: ArrayBuffer | null = null;
  error: DOMException | null = null;
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  readAsArrayBuffer(file: Blob) {
    pending.push(() => {
      // Released: do the real read, then report it as this reader's.
      const real = new RealFileReader();
      real.onload = () => {
        this.result = real.result as ArrayBuffer;
        this.onload?.();
      };
      real.readAsArrayBuffer(file);
    });
  }
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  pending.length = 0;
});

async function finishRead(index = 0) {
  await act(async () => {
    pending[index]();
    await new Promise((r) => setTimeout(r, 20));
  });
}

function pick(name: string, text: string) {
  fireEvent.change(screen.getByLabelText(/choose a (plan|spec) file/i), {
    target: { files: [new File([text], name)] },
  });
}

describe('file reads that finish after the user moved on', () => {
  it('a read that finishes after switching to Task is ignored', async () => {
    vi.stubGlobal('FileReader', ManualFileReader);
    const user = userEvent.setup();
    const create = vi.spyOn(api, 'createTask').mockResolvedValue({
      public_task_id: 'RT-1', original_task: 'x', title: 'x', status: 'routed',
      created_at: '2026-10-18', analyzer: null, fingerprint: null, recommendation: null, handoffs: {},
    } as TaskResponse);
    render(<RouterPage pi={null} preferences={PREFS} />);

    await user.click(screen.getByRole('radio', { name: 'Implementation plan' }));
    pick('big-plan.md', '# Big plan');
    await user.click(screen.getByRole('radio', { name: 'Task / prompt' }));
    await finishRead();

    expect(screen.queryByLabelText('Path in your repository')).toBeNull();
    await user.type(screen.getByLabelText(/what do you want/i), 'Fix the typo');
    await user.click(screen.getByRole('button', { name: 'Analyze task' }));
    await waitFor(() => expect(create).toHaveBeenCalled());
    expect(create.mock.calls[0][0]).toBe('Fix the typo');
    expect(create.mock.calls[0][1]).toMatchObject({ inputType: 'task', sourcePath: undefined });
  });

  it('when two files are picked, the slower first read cannot replace the second', async () => {
    vi.stubGlobal('FileReader', ManualFileReader);
    render(<RouterPage pi={null} preferences={PREFS} />);
    fireEvent.click(screen.getByRole('radio', { name: 'Implementation plan' }));
    pick('first.md', '# First');
    pick('second.md', '# Second');
    await finishRead(1);
    expect(await screen.findByText('second.md')).toBeInTheDocument();
    await finishRead(0);
    expect(screen.queryByText('first.md')).toBeNull();
    expect(screen.getByText('second.md')).toBeInTheDocument();
  });

  it('cannot analyze while a file is still being read', async () => {
    vi.stubGlobal('FileReader', ManualFileReader);
    const user = userEvent.setup();
    render(<RouterPage pi={null} preferences={PREFS} />);
    await user.click(screen.getByRole('radio', { name: 'Implementation plan' }));
    await user.type(screen.getByLabelText(/paste the implementation plan/i), '1. pasted');
    pick('plan.md', '# Plan');
    expect(screen.getByRole('button', { name: /reading/i })).toBeDisabled();
    await finishRead();
    expect(await screen.findByText('plan.md')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Analyze plan' })).toBeEnabled();
  });
});
