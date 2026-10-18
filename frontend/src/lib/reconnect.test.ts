import { describe, expect, it } from 'vitest';

import { AUTO_RECONNECT_INTERVAL_MS, shouldAutoReconnect } from './reconnect.ts';
import type { PiStatus } from './types.ts';

const NOW = 1_000_000_000;
const status = (s: Pick<PiStatus, 'status' | 'available'>): PiStatus => ({
  provider: null,
  model: null,
  ...s,
});

describe('shouldAutoReconnect', () => {
  it('restarts a bridge that cannot be reached', () => {
    expect(shouldAutoReconnect(status({ status: 'unavailable', available: false }), -Infinity, NOW)).toBe(true);
  });

  it.each(['degraded', 'misconfigured'])(
    'leaves a reachable but %s bridge alone: a restart cannot log in or fix config',
    (s) => {
      expect(shouldAutoReconnect(status({ status: s, available: false }), -Infinity, NOW)).toBe(false);
    },
  );

  it('leaves a healthy bridge alone', () => {
    expect(shouldAutoReconnect(status({ status: 'ok', available: true }), -Infinity, NOW)).toBe(false);
  });

  it('waits out the interval after an attempt, then tries again', () => {
    const down = status({ status: 'unavailable', available: false });
    expect(shouldAutoReconnect(down, NOW - AUTO_RECONNECT_INTERVAL_MS + 1, NOW)).toBe(false);
    expect(shouldAutoReconnect(down, NOW - AUTO_RECONNECT_INTERVAL_MS, NOW)).toBe(true);
  });
});
