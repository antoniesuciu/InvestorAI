export type DataKind = 'reported' | 'synthetic';
export type Frequency = 'annual' | 'quarterly';
export type Adjustment = 'raw' | 'split' | 'dividend';

export interface Company {
  ticker: string;
  name: string;
  cik: string | null;
  currency: string | null;
  financial_available: boolean;
  market_available: boolean;
  latest_reporting_period: string | null;
  latest_market_session: string | null;
  data_kind: DataKind;
}

export interface Freshness {
  status: 'available' | 'stale' | 'missing';
  retrieved_at: string | null;
  completed_at: string | null;
  latest_period: string | null;
  latest_session: string | null;
  retrieval_age_hours: string | null;
  coverage_age_days: number | null;
  note: string;
  last_attempt_at?: string | null;
  last_attempt_status?: string | null;
  last_failed_at?: string | null;
}

export interface Snapshot {
  snapshot_id: string;
  retrieved_at: string | null;
  created_at: string | null;
}

export interface Evidence {
  record_id: string;
  metric: string;
  concept: string;
  taxonomy: string;
  value: string | null;
  unit: string;
  start: string | null;
  end: string;
  period_type: string;
  accession: string;
  form: string;
  filed: string;
  acceptance_datetime: string | null;
  retrieved_at: string;
  analysis_available_at: string | null;
  filing_url: string | null;
  source_url: string | null;
  fiscal_year: number | null;
  fiscal_quarter: number | null;
  quality_flags: string[];
}

export interface ReviewedEvidence {
  id: string;
  kind: string;
  verified_at: string;
  basis: string;
  checks: string[];
  documents: { url: string; retrieved_at: string; sha256: string }[];
}

export interface Metric {
  value: string | null;
  unit: string;
  status: string;
  formula: string | null;
  classification: string;
  flags: string[];
  inputs: Evidence[];
  selection_evidence: Evidence[];
  comparability_evidence: ReviewedEvidence[];
}

export interface FinancialPeriod {
  period: {
    start: string;
    end: string;
    frequency: Frequency;
    fiscal_year: number | null;
    fiscal_quarter: number | null;
    accession: string | null;
    selection_status: string;
    fiscal_status: string;
    balance_accession: string | null;
    balance_selection_status: string;
    statement_accession: string | null;
  };
  metrics: Record<string, Metric>;
}

export interface FinancialReport {
  ticker: string;
  name: string;
  cik: string | null;
  as_of: string;
  frequency: Frequency;
  unit: string;
  data_kind: DataKind;
  periods: FinancialPeriod[];
  freshness: Freshness;
  snapshot: Snapshot;
  limitations: string[];
  diagnostics: string[];
}

export interface Bar {
  date: string;
  open: string;
  high: string;
  low: string;
  close: string;
  volume: string;
  observed_at: string;
  record_id: string;
  adjustment: Adjustment;
  adjustment_as_of: string | null;
  source: { sha256: string };
  published_at?: string | null;
}

export interface Action {
  id: string;
  kind: 'split' | 'dividend';
  ex_date: string;
  amount: string | null;
  ratio: string | null;
  cash_currency: string | null;
  currency_status: string | null;
  process_date: string | null;
  record_date: string | null;
  payment_date: string | null;
  observed_at: string;
  source: { sha256: string };
  published_at?: string | null;
  declared_at?: string | null;
  record_id?: string | null;
}

export interface ReturnValue {
  value: string | null;
  unit: 'fraction';
  status: 'ok' | 'unavailable';
  reason: string | null;
  formula: string | null;
  start: string | null;
  end: string | null;
}

export interface MarketReport {
  ticker: string;
  instrument_id: string;
  as_of: string;
  provider: string;
  feed: string;
  currency: string;
  adjustment: Adjustment;
  adjustment_as_of: string | null;
  data_kind: DataKind;
  bars: Bar[];
  actions: Action[];
  quality: Record<string, unknown>;
  coverage: { start: string | null; end: string | null };
  freshness: Freshness;
  snapshot: Snapshot;
  price_return: ReturnValue;
  limitations: string[];
  mic?: string | null;
  timezone?: string | null;
  calendar?: { start: string; end: string; observed_at: string; sessions: { date: string; open_at: string; close_at: string }[] } | null;
}

export interface ComparisonItem {
  company: Company;
  financials: FinancialReport | null;
  market: MarketReport | null;
  error: string | null;
  performance: { date: string; value: string | null; status: string }[];
}

export interface ComparisonReport {
  as_of: string;
  frequency: Frequency;
  data_kind: DataKind;
  companies: ComparisonItem[];
  warnings: string[];
  financial_alignment: 'exact_reporting_intervals_only';
  common_market_range: { start: string; end: string } | null;
}

export interface Filters {
  frequency: Frequency;
  metric: string;
  start: string;
  end: string;
  asOf: string;
  adjustment: Adjustment;
}
