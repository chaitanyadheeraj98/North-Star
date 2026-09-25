/** Presentation helpers. No business logic lives here. */

export function pct(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '--';
  return `${(value * 100).toFixed(digits)}%`;
}

export function burn(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '--';
  return value.toFixed(2);
}

export function titleCase(value: string): string {
  return value
    .split('_')
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ');
}

/**
 * Turn a 0..1 score into a word.
 *
 * The Router page shows these instead of raw numbers: "High" tells the user
 * what they need in order to sanity-check the recommendation, and 0.84 does
 * not. The numbers stay available in the details panel.
 */
export function band(value: number): 'Minimal' | 'Low' | 'Moderate' | 'High' | 'Very high' {
  if (value >= 0.85) return 'Very high';
  if (value >= 0.65) return 'High';
  if (value >= 0.4) return 'Moderate';
  if (value >= 0.18) return 'Low';
  return 'Minimal';
}

export function bandTone(value: number): string {
  if (value >= 0.85) return 'text-rose-300';
  if (value >= 0.65) return 'text-amber-300';
  if (value >= 0.4) return 'text-yellow-200';
  return 'text-emerald-300';
}

export function effortLabel(effort: string): string {
  return { xhigh: 'xHigh', none: 'None' }[effort] ?? titleCase(effort);
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return '--';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '--';
  return date.toLocaleString(undefined, {
    year: 'numeric',
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

export function outcomeTone(outcome: string | null | undefined): string {
  switch (outcome) {
    case 'success':
      return 'text-emerald-300';
    case 'partial':
      return 'text-amber-300';
    case 'failed':
      return 'text-rose-300';
    default:
      return 'text-slate-400';
  }
}

export function reasonCodeLabel(code: string): string {
  return code.toLowerCase().split('_').join(' ');
}
