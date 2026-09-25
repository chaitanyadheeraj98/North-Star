/**
 * Backend client.
 *
 * Every business rule lives on the backend. This file transports JSON and
 * turns error responses into messages a person can act on; it never computes
 * a reliability, a burn, or a recommendation of its own.
 */

import type {
  Analytics,
  ModelUpdateResult,
  PiModelOption,
  HandoffResponse,
  HistoryResponse,
  ImportResultResponse,
  SettingsResponse,
  TaskResponse,
} from './types.ts';

const BASE = import.meta.env.VITE_API_BASE ?? '';

export class ApiError extends Error {
  readonly status: number;
  readonly detail: unknown;
  constructor(message: string, status: number, detail: unknown = null) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
  }
}

/** FastAPI puts errors in `detail`, which may be a string or a structured object. */
function describeError(status: number, body: unknown): { message: string; detail: unknown } {
  if (typeof body === 'object' && body !== null && 'detail' in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === 'string') return { message: detail, detail: null };
    if (typeof detail === 'object' && detail !== null) {
      const record = detail as Record<string, unknown>;
      const message = typeof record.message === 'string' ? record.message : JSON.stringify(detail);
      return { message, detail: record.detail ?? null };
    }
    if (Array.isArray(detail)) {
      return { message: 'The request was rejected as invalid.', detail };
    }
  }
  return { message: `Request failed with HTTP ${status}.`, detail: body };
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, {
      ...init,
      headers: { 'content-type': 'application/json', ...(init?.headers ?? {}) },
    });
  } catch (error) {
    throw new ApiError(
      'Cannot reach the backend. Is it running? Try: docker compose up -d',
      0,
      (error as Error).message,
    );
  }

  if (response.status === 204) return undefined as T;

  const text = await response.text();
  let body: unknown = null;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = text;
    }
  }

  if (!response.ok) {
    const { message, detail } = describeError(response.status, body);
    throw new ApiError(message, response.status, detail);
  }
  return body as T;
}

export const api = {
  health: () => request<{ status: string; version: string }>('/api/health'),

  createTask: (task: string, options?: { analyzer?: string; allowFallback?: boolean }) =>
    request<TaskResponse>('/api/tasks', {
      method: 'POST',
      body: JSON.stringify({
        task,
        analyze: true,
        analyzer: options?.analyzer ?? 'pi',
        allow_fallback: options?.allowFallback ?? false,
      }),
    }),

  getTask: (id: string) => request<TaskResponse>(`/api/tasks/${encodeURIComponent(id)}`),

  reanalyze: (id: string, options?: { analyzer?: string; allowFallback?: boolean }) =>
    request<TaskResponse>(`/api/tasks/${encodeURIComponent(id)}/analyze`, {
      method: 'POST',
      body: JSON.stringify({
        analyzer: options?.analyzer ?? 'pi',
        allow_fallback: options?.allowFallback ?? false,
      }),
    }),

  handoff: (id: string, provider: string) =>
    request<HandoffResponse>(`/api/tasks/${encodeURIComponent(id)}/handoff?provider=${encodeURIComponent(provider)}`),

  importResult: (receipt: string) =>
    request<ImportResultResponse>('/api/results/import', {
      method: 'POST',
      body: JSON.stringify({ receipt }),
    }),

  history: (params?: { limit?: number; offset?: number; taskFamily?: string }) => {
    const query = new URLSearchParams();
    if (params?.limit) query.set('limit', String(params.limit));
    if (params?.offset) query.set('offset', String(params.offset));
    if (params?.taskFamily) query.set('task_family', params.taskFamily);
    const suffix = query.toString() ? `?${query}` : '';
    return request<HistoryResponse>(`/api/history${suffix}`);
  },

  analytics: () => request<Analytics>('/api/analytics'),

  settings: () => request<SettingsResponse>('/api/settings'),

  updateSettings: (body: Record<string, unknown>) =>
    request<{ status: string; preferences: Record<string, unknown> }>('/api/settings', {
      method: 'PUT',
      body: JSON.stringify(body),
    }),

  piStatus: () => request<Record<string, unknown>>('/api/pi/status'),

  piModels: () => request<{ models: PiModelOption[] }>('/api/pi/models'),

  updateModels: () =>
    request<ModelUpdateResult>('/api/models/update', { method: 'POST' }),

  reloadConfig: () =>
    request<{ status: string }>('/api/models/reload', { method: 'POST' }),
};

/** Clipboard write with a graceful answer when the browser refuses. */
export async function copyToClipboard(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}
