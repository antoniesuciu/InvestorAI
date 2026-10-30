"""Allowlisted dashboard contracts; financial values remain decimal strings."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

DataKind = Literal['reported', 'synthetic']
Frequency = Literal['annual', 'quarterly']
Adjustment = Literal['raw', 'split', 'dividend']


class Model(BaseModel):
    model_config = ConfigDict(extra='ignore', allow_inf_nan=False)


class Snapshot(Model):
    snapshot_id: str
    retrieved_at: str
    created_at: str


class Freshness(Model):
    status: Literal['available', 'stale', 'missing']
    retrieved_at: str | None = None
    completed_at: str | None = None
    latest_period: str | None = None
    latest_session: str | None = None
    retrieval_age_hours: str | None = None
    coverage_age_days: int | None = None
    last_attempt_at: str | None = None
    last_attempt_status: str | None = None
    last_failed_at: str | None = None
    note: str


class Company(Model):
    ticker: str
    name: str
    cik: str | None = None
    currency: str | None = None
    financial_available: bool
    market_available: bool
    latest_reporting_period: str | None = None
    latest_market_session: str | None = None
    data_kind: DataKind = 'reported'


class CompanyList(Model):
    companies: list[Company]
    data_kind: DataKind = 'reported'


class Evidence(Model):
    record_id: str | None = None
    metric: str | None = None
    concept: str | None = None
    taxonomy: str | None = None
    value: str | None = None
    unit: str | None = None
    start: str | None = None
    end: str | None = None
    period_type: str | None = None
    accession: str | None = None
    form: str | None = None
    filed: str | None = None
    acceptance_datetime: str | None = None
    retrieved_at: str | None = None
    analysis_available_at: str | None = None
    filing_url: str | None = None
    source_url: str | None = None
    fiscal_year: int | None = None
    fiscal_quarter: int | None = None
    quality_flags: list[str] = Field(default_factory=list)


class ReviewedDocument(Model):
    url: str | None = None
    retrieved_at: str | None = None
    sha256: str | None = None


class ReviewedEvidence(Model):
    id: str
    kind: str
    verified_at: str
    basis: str
    checks: list[str] = Field(default_factory=list)
    documents: list[ReviewedDocument] = Field(default_factory=list)


class Metric(Model):
    value: str | None
    unit: str
    status: str
    formula: str
    classification: str
    flags: list[str] = Field(default_factory=list)
    inputs: list[Evidence] = Field(default_factory=list)
    selection_evidence: list[Evidence] = Field(default_factory=list)
    comparability_evidence: list[ReviewedEvidence] = Field(default_factory=list)


class Period(Model):
    start: str
    end: str
    frequency: Frequency
    fiscal_year: int | None = None
    fiscal_quarter: int | None = None
    accession: str | None = None
    selection_status: str
    fiscal_status: str
    balance_accession: str | None = None
    balance_selection_status: str | None = None
    statement_accession: str | None = None


class FinancialPeriod(Model):
    period: Period
    metrics: dict[str, Metric]


class FinancialReport(Model):
    ticker: str
    name: str
    cik: str
    as_of: str
    frequency: Frequency
    unit: str
    data_kind: DataKind
    periods: list[FinancialPeriod]
    freshness: Freshness
    snapshot: Snapshot
    limitations: list[str]
    diagnostics: list[str]


class Coverage(Model):
    start: str
    end: str


class SourceHash(Model):
    sha256: str | None = None


class Bar(Model):
    date: str
    open: str
    high: str
    low: str
    close: str
    volume: str
    observed_at: str
    published_at: str | None = None
    record_id: str
    adjustment: str
    adjustment_as_of: str | None = None
    source: SourceHash


class Action(Model):
    id: str
    kind: Literal['split', 'dividend']
    ex_date: str
    amount: str | None = None
    ratio: str | None = None
    cash_currency: str | None = None
    currency_status: str | None = None
    process_date: str | None = None
    record_date: str | None = None
    payment_date: str | None = None
    observed_at: str
    published_at: str | None = None
    declared_at: str | None = None
    record_id: str | None = None
    source: SourceHash


class ReturnValue(Model):
    value: str | None = None
    unit: Literal['fraction'] = 'fraction'
    status: Literal['ok', 'unavailable']
    reason: str | None = None
    formula: str | None = None
    start: str | None = None
    end: str | None = None


class Session(Model):
    date: str
    open_at: str
    close_at: str


class Calendar(Model):
    start: str
    end: str
    observed_at: str
    sessions: list[Session]


class MarketReport(Model):
    ticker: str
    instrument_id: str
    as_of: str
    provider: str
    feed: str
    currency: str
    mic: str | None = None
    timezone: str | None = None
    calendar: Calendar | None = None
    adjustment: Adjustment
    adjustment_as_of: str | None = None
    data_kind: DataKind
    bars: list[Bar]
    actions: list[Action]
    quality: dict[str, object]
    coverage: Coverage
    freshness: Freshness
    snapshot: Snapshot
    price_return: ReturnValue
    limitations: list[str]


class PerformancePoint(Model):
    date: str
    value: str | None
    status: str


class ComparisonItem(Model):
    company: Company
    financials: FinancialReport | None = None
    market: MarketReport | None = None
    error: str | None = None
    performance: list[PerformancePoint] = Field(default_factory=list)


class ComparisonReport(Model):
    as_of: str
    frequency: Frequency
    data_kind: DataKind
    companies: list[ComparisonItem]
    warnings: list[str]
    financial_alignment: Literal['exact_reporting_intervals_only'] = 'exact_reporting_intervals_only'
    common_market_range: Coverage | None = None
