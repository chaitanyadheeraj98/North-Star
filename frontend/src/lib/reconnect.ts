import type { PiStatus } from './types.ts';

/** At most one automatic restart per window, so a restart that fails does not loop. */
export const AUTO_RECONNECT_INTERVAL_MS = 120_000;

/**
 * Consecutive unreachable polls (30s apart) before restarting. One is not
 * enough: a bridge that is merely slow for a moment (a token refresh, a busy
 * machine) times out the 5s status check once, and restarting it would kill
 * any analysis it is in the middle of. The watchdog is similarly patient.
 */
export const AUTO_RECONNECT_AFTER_MISSES = 2;

/**
 * Count of consecutive polls in which the bridge could not be reached.
 *
 * Only `status: 'unavailable'` counts. A bridge that answers but reports
 * `available: false` - not logged in, misconfigured, an unknown model - has a
 * problem a restart cannot fix, so it resets the count like a healthy one.
 */
export function nextMissCount(pi: PiStatus, misses: number): number {
  return pi.status === 'unavailable' ? misses + 1 : 0;
}

/** Whether the background poll should restart the Pi bridge now. */
export function shouldAutoReconnect(misses: number, lastAttemptMs: number, nowMs: number): boolean {
  return (
    misses >= AUTO_RECONNECT_AFTER_MISSES && nowMs - lastAttemptMs >= AUTO_RECONNECT_INTERVAL_MS
  );
}
