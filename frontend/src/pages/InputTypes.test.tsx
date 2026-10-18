import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { api } from '../lib/api.ts';
import {
  DOCUMENT_MAX_CHARS,
  TASK_MAX_CHARS,
  inputProblem,
  pathProblem,
  readPickedFile,
  sha256Hex,
  suggestedPath,
} from '../lib/input.ts';
import type { TaskResponse } from '../lib/types.ts';
import { RouterPage } from './RouterPage.tsx';

afterEach(() => vi.restoreAllMocks());

const PREFS = { analyzer_source: 'heuristic', allow_analyzer_fallback: false };

function response(overrides: Partial<TaskResponse> = {}): TaskResponse {
  return {
    public_task_id: 'RT-000200', original_task: 'x', title: 'x', status: 'routed',
    created_at: '2026-10-18', analyzer: null, fingerprint: null, recommendation: null,
    handoffs: {}, ...overrides,
  };
}

function planFile(text: string, name = 'billing-plan.md') {
  return new File([text], name, { type: 'text/markdown' });
}

describe('input helpers', () => {
  it('hashes bytes exactly as sha256sum does', async () => {
    // sha256("abc"), the standard test vector.
    expect(await sha256Hex(new TextEncoder().encode('abc').buffer as ArrayBuffer)).toBe(
      'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad',
    );
  });

  it('reads a file and hashes its raw bytes, BOM included', async () => {
    const bom = new Uint8Array([0xef, 0xbb, 0xbf]);
    const file = new File([bom, '# Plan'], 'p.md');
    const read = await readPickedFile(file);
    expect(read.text).toBe('# Plan');
    expect(read.sha256).toBe(await sha256Hex(new Uint8Array([...bom, ...new TextEncoder().encode('# Plan')]).buffer));
  });

  it('refuses a file that is not UTF-8 text', async () => {
    await expect(readPickedFile(new File([new Uint8Array([0xff, 0xfe, 0x00, 0xd8])], 'x.md')))
      .rejects.toThrow();
  });

  it('suggests a path by type, since the browser hides the folder', () => {
    expect(suggestedPath('plan', 'a.md')).toBe('docs/plans/a.md');
    expect(suggestedPath('spec', 'a.md')).toBe('docs/specs/a.md');
  });

  it('applies the task limit to tasks only', () => {
    const long = 'x'.repeat(TASK_MAX_CHARS + 1);
    expect(inputProblem('task', long)).toMatch(/choose that type/);
    expect(inputProblem('plan', long)).toBeNull();
    expect(inputProblem('plan', 'x'.repeat(DOCUMENT_MAX_CHARS + 1))).toMatch(/limit/);
  });

  it('rejects the same path characters the backend does', () => {
    expect(pathProblem('docs/plans/a.md')).toBeNull();
    expect(pathProblem('  ')).toMatch(/Enter the path/);
    expect(pathProblem('a\nb')).toMatch(/line breaks/);
    expect(pathProblem('a`b')).toMatch(/backticks/);
  });
});

describe('Router page input types', () => {
  it('sends a picked plan file with its path and hash, not as a task', async () => {
    const user = userEvent.setup();
    const create = vi.spyOn(api, 'createTask').mockResolvedValue(
      response({
        input: { input_type: 'plan', source_path: 'docs/plans/v2/billing-plan.md',
          source_sha256: 'a'.repeat(64), content_chars: 300_000, condensed: true },
      }),
    );
    render(<RouterPage pi={null} preferences={PREFS} />);

    expect(screen.queryByRole('button', { name: /choose file/i })).toBeNull();
    await user.click(screen.getByRole('radio', { name: 'Implementation plan' }));

    const text = '# Plan\n' + 'step\n'.repeat(60_000); // over the task limit
    fireEvent.change(screen.getByLabelText(/choose a plan file/i), {
      target: { files: [planFile(text)] },
    });
    const path = await screen.findByLabelText('Path in your repository');
    expect(path).toHaveValue('docs/plans/billing-plan.md');
    await user.clear(path);
    await user.type(path, 'docs/plans/v2/billing-plan.md');

    await user.click(screen.getByRole('button', { name: 'Analyze plan' }));
    await waitFor(() => expect(create).toHaveBeenCalled());
    const [sent, options] = create.mock.calls[0];
    expect(sent).toBe(text);
    expect(options).toMatchObject({
      inputType: 'plan',
      sourcePath: 'docs/plans/v2/billing-plan.md',
      sourceSha256: await sha256Hex(new TextEncoder().encode(text).buffer as ArrayBuffer),
    });
    expect(await screen.findByText(/condensed version/)).toBeInTheDocument();
  });

  it('blocks analysis until the path is filled in', async () => {
    const user = userEvent.setup();
    const create = vi.spyOn(api, 'createTask');
    render(<RouterPage pi={null} preferences={PREFS} />);
    await user.click(screen.getByRole('radio', { name: 'Spec' }));
    fireEvent.change(screen.getByLabelText(/choose a spec file/i), {
      target: { files: [planFile('Goal: x', 'spec.md')] },
    });
    const path = await screen.findByLabelText('Path in your repository');
    expect(path).toHaveValue('docs/specs/spec.md');
    await user.clear(path);
    expect(screen.getByRole('button', { name: 'Analyze spec' })).toBeDisabled();
    expect(screen.getByRole('alert')).toHaveTextContent(/Enter the path/);
    expect(create).not.toHaveBeenCalled();
  });

  it('warns about a task over the limit and points at the plan type', async () => {
    render(<RouterPage pi={null} preferences={PREFS} />);
    fireEvent.change(screen.getByLabelText(/what do you want/i), {
      target: { value: 'x'.repeat(TASK_MAX_CHARS + 1) },
    });
    expect(screen.getByRole('alert')).toHaveTextContent(/choose that type/);
    expect(screen.getByRole('button', { name: 'Analyze task' })).toBeDisabled();
  });

  it('switching back to task drops the picked file', async () => {
    const user = userEvent.setup();
    render(<RouterPage pi={null} preferences={PREFS} />);
    await user.click(screen.getByRole('radio', { name: 'Implementation plan' }));
    fireEvent.change(screen.getByLabelText(/choose a plan file/i), {
      target: { files: [planFile('# P')] },
    });
    await screen.findByLabelText('Path in your repository');
    await user.click(screen.getByRole('radio', { name: 'Task / prompt' }));
    expect(screen.queryByLabelText('Path in your repository')).toBeNull();
    expect(screen.getByLabelText(/what do you want/i)).toBeInTheDocument();
  });
});
