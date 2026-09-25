import { describe, expect, it } from 'vitest';

import { band, burn, effortLabel, outcomeTone, pct, titleCase } from './format.ts';

describe('pct', () => {
  it('renders a fraction as a percentage', () => {
    expect(pct(0.955, 1)).toBe('95.5%');
    expect(pct(0.8)).toBe('80%');
  });

  it('renders missing values as a dash rather than NaN%', () => {
    expect(pct(null)).toBe('--');
    expect(pct(undefined)).toBe('--');
    expect(pct(Number.NaN)).toBe('--');
  });
});

describe('burn', () => {
  it('shows two decimals', () => {
    expect(burn(3.456)).toBe('3.46');
    expect(burn(0)).toBe('0.00');
  });

  it('distinguishes absent from zero', () => {
    expect(burn(null)).toBe('--');
  });
});

describe('band', () => {
  it('maps scores onto words the user can sanity-check', () => {
    expect(band(0.02)).toBe('Minimal');
    expect(band(0.25)).toBe('Low');
    expect(band(0.5)).toBe('Moderate');
    expect(band(0.7)).toBe('High');
    expect(band(0.92)).toBe('Very high');
  });

  it('is monotonic across the boundaries', () => {
    const order = ['Minimal', 'Low', 'Moderate', 'High', 'Very high'];
    const scores = [0, 0.18, 0.4, 0.65, 0.85];
    expect(scores.map(band)).toEqual(order);
  });
});

describe('effortLabel', () => {
  it('renders xhigh the way the providers write it', () => {
    expect(effortLabel('xhigh')).toBe('xHigh');
  });

  it('title-cases the rest', () => {
    expect(effortLabel('medium')).toBe('Medium');
    expect(effortLabel('max')).toBe('Max');
  });
});

describe('titleCase', () => {
  it('turns a family key into a label', () => {
    expect(titleCase('backend_data_integrity')).toBe('Backend Data Integrity');
  });
});

describe('outcomeTone', () => {
  it('gives each outcome a distinct tone', () => {
    const tones = new Set([
      outcomeTone('success'),
      outcomeTone('partial'),
      outcomeTone('failed'),
      outcomeTone(null),
    ]);
    expect(tones.size).toBe(4);
  });
});
