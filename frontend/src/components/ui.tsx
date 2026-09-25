/** Small shared presentational pieces. Deliberately unglamorous: this is a tool. */

import type { ReactNode } from 'react';

export function Card({
  children,
  className = '',
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={`rounded-xl border border-slate-800 bg-slate-900/60 p-5 shadow-sm ${className}`}
    >
      {children}
    </div>
  );
}

export function SectionTitle({ children }: { children: ReactNode }) {
  return (
    <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-500">
      {children}
    </h3>
  );
}

export function Button({
  children,
  onClick,
  disabled,
  variant = 'primary',
  type = 'button',
  title,
}: {
  children: ReactNode;
  onClick?: () => void;
  disabled?: boolean;
  variant?: 'primary' | 'secondary' | 'ghost';
  type?: 'button' | 'submit';
  title?: string;
}) {
  const styles = {
    primary:
      'bg-sky-500 text-slate-950 hover:bg-sky-400 disabled:bg-slate-700 disabled:text-slate-500',
    secondary:
      'border border-slate-700 bg-slate-800/70 text-slate-200 hover:border-slate-600 hover:bg-slate-800 disabled:opacity-40',
    ghost: 'text-slate-400 hover:text-slate-200 disabled:opacity-40',
  }[variant];

  return (
    <button
      type={type}
      title={title}
      onClick={onClick}
      disabled={disabled}
      className={`rounded-lg px-4 py-2 text-sm font-medium transition-colors disabled:cursor-not-allowed ${styles}`}
    >
      {children}
    </button>
  );
}

export function Stat({
  label,
  value,
  tone = 'text-slate-100',
  hint,
}: {
  label: string;
  value: ReactNode;
  tone?: string;
  hint?: string;
}) {
  return (
    <div>
      <div className="text-[11px] uppercase tracking-[0.12em] text-slate-500">{label}</div>
      <div className={`mt-1 text-lg font-semibold ${tone}`}>{value}</div>
      {hint && <div className="mt-0.5 text-xs text-slate-500">{hint}</div>}
    </div>
  );
}

export function Pill({
  children,
  tone = 'slate',
}: {
  children: ReactNode;
  tone?: 'slate' | 'emerald' | 'amber' | 'rose' | 'sky';
}) {
  const styles = {
    slate: 'border-slate-700 bg-slate-800/60 text-slate-300',
    emerald: 'border-emerald-800 bg-emerald-950/50 text-emerald-300',
    amber: 'border-amber-800 bg-amber-950/50 text-amber-300',
    rose: 'border-rose-800 bg-rose-950/50 text-rose-300',
    sky: 'border-sky-800 bg-sky-950/50 text-sky-300',
  }[tone];
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-medium ${styles}`}
    >
      {children}
    </span>
  );
}

export function Banner({
  tone,
  title,
  children,
}: {
  tone: 'info' | 'warn' | 'error' | 'success';
  title?: string;
  children: ReactNode;
}) {
  const styles = {
    info: 'border-sky-900 bg-sky-950/40 text-sky-200',
    warn: 'border-amber-900 bg-amber-950/40 text-amber-200',
    error: 'border-rose-900 bg-rose-950/40 text-rose-200',
    success: 'border-emerald-900 bg-emerald-950/40 text-emerald-200',
  }[tone];
  return (
    <div className={`rounded-lg border px-4 py-3 text-sm ${styles}`} role="status">
      {title && <div className="mb-1 font-semibold">{title}</div>}
      <div className="leading-relaxed">{children}</div>
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return (
    <div className="rounded-xl border border-dashed border-slate-800 px-6 py-12 text-center text-sm text-slate-500">
      {children}
    </div>
  );
}

export function Spinner({ label }: { label?: string }) {
  return (
    <div className="flex items-center gap-3 text-sm text-slate-400">
      <span
        className="h-4 w-4 animate-spin rounded-full border-2 border-slate-700 border-t-sky-400"
        aria-hidden
      />
      {label}
    </div>
  );
}
