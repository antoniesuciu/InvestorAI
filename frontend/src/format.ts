import type { FinancialPeriod, Metric } from './types';

export const metricLabels: Record<string, string> = {
  revenue: 'Revenue', net_income: 'Net income', operating_cash_flow: 'Operating cash flow',
  capital_expenditure: 'Capital expenditure', capex: 'Capital expenditure', free_cash_flow: 'Free cash flow',
  gross_profit: 'Gross profit', operating_income: 'Operating income', assets: 'Total assets',
  liabilities: 'Total liabilities', cash: 'Cash & equivalents', current_assets: 'Current assets',
  current_liabilities: 'Current liabilities', noncurrent_debt: 'Noncurrent debt', equity: 'Stockholders’ equity',
  revenue_growth_yoy_pct: 'Revenue growth YoY', net_income_growth_yoy_pct: 'Net income growth YoY',
  revenue_change_yoy: 'Revenue change YoY', net_income_change_yoy: 'Net income change YoY',
  net_margin_pct: 'Net margin', gross_margin_pct: 'Gross margin', operating_margin_pct: 'Operating margin',
  liabilities_to_assets: 'Liabilities / assets', current_ratio: 'Current ratio',
  cash_to_current_liabilities: 'Cash / current liabilities', noncurrent_debt_to_equity: 'Noncurrent debt / equity',
};

export const metricLabel = (key: string) => metricLabels[key] ?? key.replaceAll('_', ' ');
export const statusLabel = (value: string) => value.replaceAll('_', ' ');

/** Conversion is for chart geometry and presentation only. Financial arithmetic belongs to the API. */
export function displayNumber(value: string | null | undefined): number | null {
  if (value === null || value === undefined || value.trim() === '') return null;
  const result = Number(value);
  return Number.isFinite(result) ? result : null;
}

export function formatValue(value: string | null | undefined, unit: string, compact = true): string {
  const number = displayNumber(value);
  if (number === null) return 'N/A';
  if (unit === 'fraction') return new Intl.NumberFormat('en-US', { style: 'percent', maximumFractionDigits: 2 }).format(number);
  if (unit === '%' || unit === 'percent' || unit === 'percentage_points' || unit === 'pct') return `${new Intl.NumberFormat('en-US', { maximumFractionDigits: 2 }).format(number)}%`;
  if (unit === 'ratio' || unit === 'dimensionless') return `${new Intl.NumberFormat('en-US', { maximumFractionDigits: 2 }).format(number)}×`;
  const options: Intl.NumberFormatOptions = { maximumFractionDigits: 2, ...(compact ? { notation: 'compact', minimumFractionDigits: 0 } : {}) };
  if (/^[A-Z]{3}$/.test(unit)) return new Intl.NumberFormat('en-US', { ...options, style: 'currency', currency: unit, currencyDisplay: 'narrowSymbol' }).format(number);
  return `${new Intl.NumberFormat('en-US', options).format(number)}${unit && unit !== 'shares' ? ` ${unit}` : ''}`;
}

export function metricValue(metric: Metric | undefined, compact = true) {
  return metric ? formatValue(metric.value, metric.unit, compact) : 'N/A';
}

export function shortDate(date: string | null | undefined) {
  if (!date) return 'Not available';
  const parsed = new Date(date.length === 10 ? `${date}T00:00:00Z` : date);
  return Number.isNaN(parsed.getTime()) ? date : new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' }).format(parsed);
}

export function timestamp(value: string | null | undefined) {
  return value ? `${value} (UTC)` : 'Not available';
}

export function periodLabel(item: FinancialPeriod) {
  const { fiscal_year, fiscal_quarter, end } = item.period;
  const fiscal = fiscal_year ? `FY ${fiscal_year}${fiscal_quarter ? ` · Q${fiscal_quarter}` : ''}` : 'Fiscal period unresolved';
  return `${fiscal} · ${shortDate(end)}`;
}

export function safeSourceUrl(url: string | null | undefined): string | undefined {
  if (!url) return undefined;
  try { const parsed = new URL(url); return parsed.protocol === 'https:' && !parsed.username && !parsed.password ? parsed.href : undefined; }
  catch { return undefined; }
}
