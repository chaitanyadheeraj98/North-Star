import { describe, expect, it } from 'vitest';

import {
  AUTO_RECONNECT_AFTER_MISSES,
  AUTO_RECONNECT_INTERVAL_MS,
  nextMissCount,
  shouldAutoReconnect,
} from './reconnect.ts';
import type { PiStatus } from './types.ts';

const NOW = 1_000_000_000;
const status = (s: Pick<PiStatus, 'status' | 'available'>): PiStatus => ({
  provider: null,
  model: null,
  ...s,
});
const DOWN = status({ status: 'unavailable', available: false });

/** Replays a sequence of polls through the same logic App.tsx runs. */
function replay(polls: PiStatus[]): boolean[] {
  let misses = 0;
  return polls.map((pi) => {
    misses = nextMissCount(pi, misses);
    return shouldAutoReconnect(misses, -Infinity, NOW);
  });
}

describe('background auto-reconnect', () => {
  it('restarts a bridge that stays unreachable for two polls', () => {
    expect(replay([DOWN, DOWN])).toEqual([false, true]);
    expect(AUTO_RECONNECT_AFTER_MISSES).toBe(2);
  });

  it('does not restart on a single slow or failed poll', () => {
    // A bridge that timed out the 5s status check once, then answered.
    expect(replay([DOWN, status({ status: 'ok', available: true }), DOWN])).toEqual([
      false,
      false,
      false,
    ]);
  });

  it.each(['degraded', 'misconfigured'])(
    'never restarts a reachable but %s bridge: a restart cannot log in or fix config',
    (s) => {
      const reachable = status({ status: s, available: false });
      expect(replay([reachable, reachable, reachable])).toEqual([false, false, false]);
    },
  );

  it('leaves a healthy bridge alone', () => {
    const ok = status({ status: 'ok', available: true });
    expect(replay([ok, ok, ok])).toEqual([false, false, false]);
  });

  it('waits out the interval after an attempt, then tries again', () => {
    expect(shouldAutoReconnect(2, NOW - AUTO_RECONNECT_INTERVAL_MS + 1, NOW)).toBe(false);
    expect(shouldAutoReconnect(2, NOW - AUTO_RECONNECT_INTERVAL_MS, NOW)).toBe(true);
  });
});
