import { describe, expect, it } from 'vitest';
import { displayNumber, formatValue, safeSourceUrl } from './format';

describe('presentation of backend decimal strings', () => {
  it('keeps missing values distinct from reported zero', () => {
    expect(formatValue(null, 'USD')).toBe('N/A');
    expect(formatValue('0', 'USD')).toBe('$0');
    expect(displayNumber(null)).toBeNull();
    expect(displayNumber('')).toBeNull();
    expect(displayNumber('0')).toBe(0);
    expect(displayNumber('Infinity')).toBeNull();
  });
  it('distinguishes percentage points, fractional returns and ratios', () => {
    expect(formatValue('15', 'percent')).toBe('15%');
    expect(formatValue('0.15', 'fraction')).toBe('15%');
    expect(formatValue('0.4', 'ratio')).toBe('0.4×');
    expect(formatValue('-3.5', 'percent')).toBe('-3.5%');
  });
  it('allows only safe HTTPS source links without embedded credentials', () => {
    expect(safeSourceUrl('https://www.sec.gov/Archives/filing.htm')).toBe('https://www.sec.gov/Archives/filing.htm');
    expect(safeSourceUrl('javascript:alert(1)')).toBeUndefined();
    expect(safeSourceUrl('https://secret@example.com/path')).toBeUndefined();
    expect(safeSourceUrl('file:///private/data')).toBeUndefined();
  });
});
