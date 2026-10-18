import type { PiStatus } from './types.ts';

/** At most one automatic restart per window, so a restart that fails does not loop. */
export const AUTO_RECONNECT_INTERVAL_MS = 120_000;

/**
 * Whether the background poll should restart the Pi bridge.
 *
 * Only when the bridge itself cannot be reached (`status: 'unavailable'`). A
 * bridge that answers but reports `available: false` - not logged in,
 * misconfigured, an unknown model - has a problem a restart cannot fix, and
 * restarting it every two minutes would only interrupt it.
 */
export function shouldAutoReconnect(pi: PiStatus, lastAttemptMs: number, nowMs: number): boolean {
  return pi.status === 'unavailable' && nowMs - lastAttemptMs >= AUTO_RECONNECT_INTERVAL_MS;
}
