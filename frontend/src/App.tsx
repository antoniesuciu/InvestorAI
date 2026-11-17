import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from './api';
import { displayNumber, formatValue, metricLabel, metricLabels, metricValue, periodLabel, shortDate, statusLabel } from './format';
import type { Company, ComparisonReport, Filters, FinancialPeriod, FinancialReport, MarketReport } from './types';
import { EvidenceDrawer, type EvidenceSelection } from './components/EvidenceDrawer';
import { FinancialChart, FinancialComparisonChart, PerformanceChart, PriceChart } from './components/Charts';
import { DataKindBanner, EmptyState, ErrorState, FreshnessStrip, Icon, Limitations, Loading, Panel } from './components/ui';

type View = 'Overview' | 'Financials' | 'Statements' | 'Compare';
const views: { label: View; icon: 'overview' | 'financials' | 'statements' | 'compare'; description: string }[] = [
  { label: 'Overview', icon: 'overview', description: 'The company, at a glance.' },
  { label: 'Financials', icon: 'financials', description: 'Follow the numbers through time.' },
  { label: 'Statements', icon: 'statements', description: 'Reported facts. Traceable to the source.' },
  { label: 'Compare', icon: 'compare', description: 'See the differences. Preserve the context.' },
];
const defaultFilters: Filters = { frequency: 'annual', metric: 'revenue', start: '2016-01-01', end: '', asOf: '', adjustment: 'split' };
const statementGroups = {
  'Income statement': ['revenue', 'gross_profit', 'operating_income', 'net_income'],
  'Balance sheet': ['assets', 'liabilities', 'equity', 'current_assets', 'current_liabilities', 'cash', 'noncurrent_debt'],
  'Cash flow': ['operating_cash_flow', 'capital_expenditure', 'free_cash_flow'],
  'Ratios & growth': ['revenue_growth_yoy_pct', 'net_income_growth_yoy_pct', 'revenue_change_yoy', 'net_income_change_yoy', 'net_margin_pct', 'gross_margin_pct', 'operating_margin_pct', 'liabilities_to_assets', 'current_ratio', 'cash_to_current_liabilities', 'noncurrent_debt_to_equity'],
};

function latestPeriod(report: FinancialReport | undefined) {
  return report?.periods.toSorted((a, b) => a.period.end.localeCompare(b.period.end)).at(-1);
}

function SourceButton({ onClick, label = 'View evidence' }: { onClick: () => void; label?: string }) {
  return <button type="button" className="text-button" onClick={onClick}>{label}<Icon name="source" size={13}/></button>;
}

function MetricCard({ label, period, metricKey, onOpen }: { label: string; period?: FinancialPeriod; metricKey: string; onOpen: () => void }) {
  const metric = period?.metrics[metricKey];
  const value = displayNumber(metric?.value);
  return <button type="button" className="metric-card" onClick={onOpen} disabled={!period} aria-label={`${label}: ${metricValue(metric)}. View evidence`}><span className="metric-card-label">{label}<Icon name="source" size={13}/></span><strong className={value !== null && value < 0 ? 'negative' : ''}>{metricValue(metric)}</strong><span className="metric-card-period">{period ? `Period ended ${shortDate(period.period.end)}` : 'No selected reporting period'}</span><span className={`metric-card-status ${metric?.value === null || !metric ? 'unavailable' : ''}`}>{metric ? `${statusLabel(metric.classification)} · ${statusLabel(metric.status)}` : 'Missing local evidence'}</span></button>;
}

function FilterBar({ filters, setFilters, disabled }: { filters: Filters; setFilters: (next: Filters) => void; disabled: boolean }) {
  const update = (key: keyof Filters, value: string) => setFilters({ ...filters, [key]: value });
  return <section aria-label="Research filters" className="filter-bar">
    <label className="field">Frequency<select value={filters.frequency} onChange={event => update('frequency', event.target.value)} disabled={disabled}><option value="annual">Annual</option><option value="quarterly">Quarterly</option></select></label>
    <label className="field metric-filter">Metric<select aria-label="Metric" value={filters.metric} onChange={event => update('metric', event.target.value)} disabled={disabled}>{Object.entries(metricLabels).filter(([key]) => key !== 'capex').map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
    <label className="field">Start date<input type="date" value={filters.start} onChange={event => update('start', event.target.value)}/></label>
    <label className="field">End date<input type="date" aria-label="End date" value={filters.end} onChange={event => update('end', event.target.value)} aria-describedby="end-date-help"/><span id="end-date-help" className="sr-only">Leave empty for all saved dates.</span></label>
    <label className="field cutoff-field">Observed by <span className="optional">optional</span><input type="date" aria-label="Observed by" value={filters.asOf} onChange={event => update('asOf', event.target.value)} aria-describedby="cutoff-help"/></label>
    <span id="cutoff-help" className="sr-only">Only locally observed evidence available by the end of this UTC date. Historical dates before collection may be unavailable.</span>
  </section>;
}

function PeriodTable({ report, keys, openEvidence, limit }: { report: FinancialReport; keys: string[]; openEvidence: (period: FinancialPeriod, key: string) => void; limit?: number }) {
  const periods = report.periods.toSorted((a, b) => b.period.end.localeCompare(a.period.end)).slice(0, limit);
  if (!periods.length) return <EmptyState>No saved reporting periods match these filters.</EmptyState>;
  return <div className="table-scroll"><table className="statement-table"><caption className="sr-only">{report.ticker} {report.frequency} financial observations; select any value for evidence</caption><thead><tr><th className="sticky-col">Metric <span className="table-unit">original unit</span></th>{periods.map(period => <th key={`${period.period.start}-${period.period.end}`}><span>{period.period.fiscal_year ? `FY ${period.period.fiscal_year}${period.period.fiscal_quarter ? ` · Q${period.period.fiscal_quarter}` : ''}` : 'Fiscal year unknown'}</span><span className="table-date">{period.period.end}</span><span className="table-interval">{period.period.start} → {period.period.end}</span></th>)}</tr></thead><tbody>{keys.map(key => <tr key={key}><th scope="row" className="sticky-col">{metricLabel(key)}<span className="table-unit">{periods.find(period => period.metrics[key])?.metrics[key].unit ?? 'N/A'}</span></th>{periods.map(period => { const metric = period.metrics[key]; return <td key={`${period.period.start}-${period.period.end}`}><button type="button" className={`table-value ${metric?.value === null || !metric ? 'missing-value' : ''}`} onClick={() => openEvidence(period, key)} aria-label={`${metricLabel(key)}, period ended ${period.period.end}: ${metricValue(metric, false)}. View evidence`} title={metric ? `${metric.value ?? 'N/A'} ${metric.unit} · ${metric.status}` : 'Missing evidence'}><strong>{metricValue(metric, false)}</strong><span>{metric?.value === null ? statusLabel(metric.status) : metric?.classification ? statusLabel(metric.classification) : 'missing'}</span></button></td>; })}</tr>)}</tbody></table></div>;
}

function Overview({ financials, market, financialError, marketError, financialLoading, marketLoading, filters, setFilters, openFinancial, openMarket }: {
  financials?: FinancialReport; market?: MarketReport; financialError: Error | null; marketError: Error | null;
  financialLoading: boolean; marketLoading: boolean; filters: Filters; setFilters: (next: Filters) => void;
  openFinancial: (period: FinancialPeriod, key: string) => void; openMarket: () => void;
}) {
  const period = latestPeriod(financials);
  const close = market?.bars.at(-1);
  return <>
    {financialLoading ? <Loading label="Reading local company financials"/> : financialError ? <ErrorState error={financialError} title="Financial evidence unavailable"/> : <div className="metric-grid">{['revenue', 'net_income', 'free_cash_flow', 'net_margin_pct'].map(key => <MetricCard key={key} label={metricLabel(key)} period={period} metricKey={key} onOpen={() => period && openFinancial(period, key)}/>)}</div>}
    {financials && <FreshnessStrip label="SEC financials" freshness={financials.freshness}/>}
    <Panel title="Price history" eyebrow="Market evidence" action={<div className="panel-actions"><label className="inline-select"><span className="sr-only">Price adjustment</span><select aria-label="Price adjustment" value={filters.adjustment} onChange={event => setFilters({ ...filters, adjustment: event.target.value as Filters['adjustment'] })}><option value="split">Split adjusted</option><option value="raw">Raw prices</option><option value="dividend">Dividend adjusted</option></select></label>{market && <SourceButton onClick={openMarket} label="Market evidence"/>}</div>}>
      {marketLoading ? <Loading label="Reading saved market observations"/> : marketError ? <ErrorState error={marketError} title="Market evidence unavailable"/> : market ? <>
        <div className="price-summary"><div><strong>{close ? formatValue(close.close, market.currency, false) : 'N/A'}</strong><span>{close ? `Last saved close · ${shortDate(close.date)}` : 'No sessions in selected range'}</span></div><div className="price-return"><span>Price return · selected window</span><b className={(displayNumber(market.price_return.value) ?? 0) < 0 ? 'negative' : 'teal-text'}>{market.price_return.status === 'ok' ? formatValue(market.price_return.value, 'fraction') : 'N/A'}</b><span>{market.price_return.start && market.price_return.end ? `${shortDate(market.price_return.start)} → ${shortDate(market.price_return.end)}` : market.price_return.reason ?? 'Unavailable'}</span></div><div className="price-source"><span className="badge">{market.feed.toUpperCase()} daily bars</span><span>{market.provider} · {market.currency}</span></div></div>
        <PriceChart report={market}/><div className="price-disclaimer">{market.adjustment === 'dividend' ? 'Provider dividend-adjusted prices; total-return methodology is unverified.' : 'Price return excludes cash dividends, reinvestment, fees and taxes.'} Corporate-action markers refer to saved ex-dates.</div><FreshnessStrip label="Market coverage" freshness={market.freshness}/>
      </> : <EmptyState>No local market data is available.</EmptyState>}
    </Panel>
    {financials && <Panel title={`${metricLabel(filters.metric)} through time`} eyebrow={`${financials.frequency} reporting intervals`} action={period && <SourceButton onClick={() => openFinancial(period, filters.metric)}/>}><FinancialChart periods={financials.periods} metric={filters.metric}/><div className="chart-caption">Each bar uses its actual reporting-period end. Missing observations remain gaps; no periods are invented.</div></Panel>}
    <Limitations items={[...(financials?.limitations ?? []), ...(market?.limitations ?? [])]}/>
  </>;
}

function Financials({ report, metric, openEvidence }: { report: FinancialReport; metric: string; openEvidence: (period: FinancialPeriod, key: string) => void }) {
  const period = latestPeriod(report);
  return <><FreshnessStrip label="SEC financials" freshness={report.freshness}/><Panel title={metricLabel(metric)} eyebrow={`${report.frequency} financial history`} action={period && <SourceButton onClick={() => openEvidence(period, metric)}/>}><div className="trend-summary"><strong>{metricValue(period?.metrics[metric])}</strong><span>{period ? periodLabel(period) : 'No reporting period selected'}</span></div><FinancialChart periods={report.periods} metric={metric}/><div className="chart-caption">Actual reporting dates · {period?.metrics[metric]?.unit ?? report.unit} · Missing values are unavailable, never zero.</div></Panel><div className="metric-grid">{['revenue_growth_yoy_pct', 'net_income_growth_yoy_pct', 'current_ratio', 'noncurrent_debt_to_equity'].map(key => <MetricCard key={key} label={metricLabel(key)} period={period} metricKey={key} onOpen={() => period && openEvidence(period, key)}/>)}</div><Panel title="Recent observations" eyebrow="Exact values & source evidence"><PeriodTable report={report} keys={[metric]} openEvidence={openEvidence} limit={8}/><p className="panel-note">Select a value to inspect its formula, source inputs and revision evidence. All available periods are in Statements.</p></Panel><Limitations items={[...report.limitations, ...report.diagnostics]}/></>;
}

function Statements({ report, openEvidence }: { report: FinancialReport; openEvidence: (period: FinancialPeriod, key: string) => void }) {
  const [group, setGroup] = useState<keyof typeof statementGroups>('Income statement');
  return <><FreshnessStrip label="SEC financials" freshness={report.freshness}/><Panel title="Financial statements" eyebrow="Selected reported facts & defined analytical metrics"><div className="statement-tabs" role="group" aria-label="Statement category">{Object.keys(statementGroups).map(name => <button key={name} type="button" aria-pressed={group === name} onClick={() => setGroup(name as keyof typeof statementGroups)}>{name}</button>)}</div><div className="statement-context"><span>{report.frequency === 'annual' ? 'Annual' : 'Standalone quarterly'} · Exact source units</span><span>{report.periods.length} reporting periods · Most recent first</span></div><PeriodTable report={report} keys={statementGroups[group]} openEvidence={openEvidence}/><p className="panel-note">This focused concept set is not a complete standardized statement. N/A includes missing concepts, unresolved fiscal context and incompatible revision vintages. Select a cell for the precise reason.</p></Panel><Limitations items={[...report.limitations, ...report.diagnostics]}/></>;
}

function Compare({ companies, selected, setSelected, report, error, loading, metric, openEvidence }: { companies: Company[]; selected: string[]; setSelected: (tickers: string[]) => void; report?: ComparisonReport; error: Error | null; loading: boolean; metric: string; openEvidence: (selection: EvidenceSelection) => void }) {
  return <><div className="compare-selector"><div><div className="eyebrow">Company selection</div><p>Choose companies from the local watchlist.</p></div><div className="company-checks">{companies.map(company => <label key={company.ticker} className={selected.includes(company.ticker) ? 'selected' : ''}><input type="checkbox" checked={selected.includes(company.ticker)} onChange={event => setSelected(event.target.checked ? [...selected, company.ticker] : selected.filter(ticker => ticker !== company.ticker))}/><strong>{company.ticker}</strong><span>{company.name}</span></label>)}</div></div>
    {selected.length < 2 ? <EmptyState>Select at least two companies to compare.</EmptyState> : loading ? <Loading label="Comparing local financial and market evidence"/> : error ? <ErrorState error={error}/> : report ? <>
      <DataKindBanner kind={report.data_kind}/><div className="notice"><strong>Fiscal intervals differ.</strong> Financial observations retain each company’s actual start and end dates. Fiscal-year labels are not normalized or treated as matching calendar periods.</div>
      {report.warnings.length > 0 && <Limitations title="Comparison warnings" items={report.warnings}/>}
      <div className="comparison-cards">{report.companies.map((item, index) => { const period = latestPeriod(item.financials ?? undefined); return <section key={item.company.ticker} className={`comparison-card company-color-${index % 4}`}><div className="comparison-card-title"><span className="company-mark">{item.company.ticker.slice(0, 1)}</span><div><h2>{item.company.ticker}</h2><p>{item.company.name}</p></div></div>{item.error && <p className="error-text">{item.error}</p>}<div className="comparison-stat"><span>{metricLabel(metric)}</span><strong>{metricValue(period?.metrics[metric])}</strong><small>{period ? `${period.period.start} → ${period.period.end}` : 'No local financial period'}</small>{period && item.financials && <SourceButton label="Financial evidence" onClick={() => openEvidence({ kind: 'financial', report: item.financials!, period, key: metric, metric: period.metrics[metric] })}/>}</div><div className="comparison-stat"><span>Split-adjusted price return</span><strong>{item.market?.price_return.status === 'ok' ? formatValue(item.market.price_return.value, 'fraction') : 'N/A'}</strong><small>{item.market?.price_return.start ? `${item.market.price_return.start} → ${item.market.price_return.end}` : item.market?.price_return.reason ?? 'No shared market window'}</small>{item.market && <SourceButton label="Market evidence" onClick={() => openEvidence({ kind: 'market', report: item.market! })}/>}</div>{item.market && <div className="comparison-coverage"><span className="status-dot"/>Market coverage ends {shortDate(item.market.freshness.latest_session)}</div>}</section>; })}</div>
      <Panel title={`${metricLabel(metric)} comparison`} eyebrow="Actual reporting-period dates" action={<div className="series-legend">{report.companies.map((item, index) => <span key={item.company.ticker}><i className={`key-dot color-${index % 4}`}/>{item.company.ticker}</span>)}</div>}><FinancialComparisonChart companies={report.companies} metric={metric}/><p className="chart-caption">Lines trace each company’s own observations. Values between observations are not estimates. Null metrics break the line.</p></Panel>
      <Panel title="Price performance" eyebrow="Backend-calculated · split adjusted" action={<span className="badge">Price return only</span>}><PerformanceChart companies={report.companies}/><p className="chart-caption">{report.common_market_range ? `${report.common_market_range.start} → ${report.common_market_range.end} · Common saved market window. ` : ''}Zero is the backend-selected base. Cash dividends and reinvestment are excluded; this is not total return.</p></Panel>
    </> : null}</>;
}

export default function App() {
  const [view, setView] = useState<View>('Overview');
  const [ticker, setTicker] = useState('AAPL');
  const [filters, setFilters] = useState<Filters>(defaultFilters);
  const [comparisonTickers, setComparisonTickers] = useState<string[] | null>(null);
  const [evidence, setEvidence] = useState<EvidenceSelection | null>(null);
  const queryClient = useQueryClient();
  const companiesQuery = useQuery({ queryKey: ['companies'], queryFn: ({ signal }) => api.companies(signal) });
  const companies = companiesQuery.data?.companies ?? [];
  const company = companies.find(item => item.ticker === ticker) ?? companies[0];
  const activeTicker = company?.ticker ?? ticker;
  const selected = comparisonTickers ?? companies.slice(0, 2).map(item => item.ticker);
  const validRange = !filters.start || !filters.end || filters.start <= filters.end;
  const financialKey = [activeTicker, filters.frequency, filters.start, filters.end, filters.asOf];
  const financialQuery = useQuery({ queryKey: ['financials', ...financialKey], queryFn: ({ signal }) => api.financials(activeTicker, filters, signal), enabled: !!company && validRange && view !== 'Compare' });
  const marketQuery = useQuery({ queryKey: ['market', activeTicker, filters.start, filters.end, filters.asOf, filters.adjustment], queryFn: ({ signal }) => api.market(activeTicker, filters, signal), enabled: !!company && validRange && view === 'Overview' });
  const compareQuery = useQuery({ queryKey: ['compare', selected, filters.frequency, filters.start, filters.end, filters.asOf], queryFn: ({ signal }) => api.compare(selected, filters, signal), enabled: view === 'Compare' && selected.length >= 2 && validRange });
  const openFinancial = (period: FinancialPeriod, key: string) => { if (financialQuery.data) setEvidence({ kind: 'financial', report: financialQuery.data, period, key, metric: period.metrics[key] }); };
  const busy = companiesQuery.isFetching || financialQuery.isFetching || marketQuery.isFetching || compareQuery.isFetching;
  const activeView = views.find(item => item.label === view)!;
  const reload = () => { void queryClient.invalidateQueries(); };
  const kind = financialQuery.data?.data_kind === 'synthetic' || marketQuery.data?.data_kind === 'synthetic' ? 'synthetic' : companiesQuery.data?.data_kind;

  return <div className="app-shell"><a className="skip-link" href="#main-content">Skip to research</a><aside className="sidebar"><a className="brand" href="#main-content" onClick={() => setView('Overview')} aria-label="Investor AI home"><span className="brand-symbol"><svg viewBox="0 0 32 32" fill="none" aria-hidden="true"><path d="M5 24V14l7 4V8l7 5V4l8 5v15" stroke="currentColor" strokeWidth="2.2"/><path d="M5 27h22" stroke="currentColor" strokeWidth="2.2"/></svg></span><span>Investor<span className="brand-ai">AI</span><small>Saved evidence, read only</small></span></a><div className="sidebar-label">Views</div><nav aria-label="Main navigation">{views.map(item => <button type="button" key={item.label} onClick={() => setView(item.label)} className={view === item.label ? 'active' : ''} aria-current={view === item.label ? 'page' : undefined}><Icon name={item.icon}/>{item.label}{view === item.label && <span className="nav-active-dot"/>}</button>)}</nav><div className="sidebar-watchlist"><div className="sidebar-label">Watchlist <span>{companies.length || '—'}</span></div>{companies.map(item => <button type="button" key={item.ticker} onClick={() => { setTicker(item.ticker); if (view === 'Compare') setView('Overview'); }} className={activeTicker === item.ticker && view !== 'Compare' ? 'selected' : ''}><span className="watchlist-mark">{item.ticker.slice(0, 1)}</span><span><strong>{item.ticker}</strong><small>{item.name}</small></span></button>)}</div><div className="sidebar-bottom"><div><span className="status-dot"/>Local evidence only</div><p>Private research.<br/>Every number has a source.</p></div></aside>
    <div className="workspace"><header className="topbar"><div className="breadcrumb">Workspace <span>/</span> <strong>{view}</strong></div><div className="topbar-right"><span className="read-only"><Icon name="check" size={13}/>Read only</span></div></header><main id="main-content" tabIndex={-1}>
      <div className="page-heading"><div><h1>{view === 'Compare' ? 'Company comparison' : view === 'Overview' ? 'Company overview' : view === 'Financials' ? 'Financial analysis' : 'Financial statements'}</h1><p>{activeView.description}</p></div><button type="button" className="reload-button" onClick={reload} disabled={busy}><Icon name="reload" size={16}/>{busy ? 'Reading local data…' : 'Reload local data'}</button></div>
      {view !== 'Compare' && <section className="company-heading" aria-label="Selected company"><div className="company-emblem">{activeTicker.slice(0, 1)}</div><div className="company-title"><label className="company-select"><span className="sr-only">Company</span><select value={activeTicker} onChange={event => setTicker(event.target.value)} disabled={!companies.length} aria-label="Company">{companies.length ? companies.map(item => <option key={item.ticker} value={item.ticker}>{item.name}</option>) : <option value={activeTicker}>Loading local companies…</option>}</select></label><div className="company-identifiers"><strong>{activeTicker}</strong><span>{company?.currency ?? 'Currency unavailable'}</span><span>CIK {company?.cik ?? 'unavailable'}</span></div></div><div className="company-source-badges"><span className="badge">SEC EDGAR</span><span className="badge neutral">Local snapshots</span></div></section>}
      <FilterBar filters={filters} setFilters={setFilters} disabled={!companies.length}/><p className="filter-help">Dates filter reporting-period ends and market sessions. Empty end date includes all saved observations. “Observed by” limits local evidence availability (UTC), not filing dates alone.</p>
      {!validRange && <div role="alert" className="error-state">Start date must be on or before end date.</div>}
      <DataKindBanner kind={kind}/>
      <div key={view} className="view-enter">{companiesQuery.isLoading ? <Loading label="Opening the local watchlist"/> : companiesQuery.error ? <ErrorState error={companiesQuery.error} title="Local service unavailable"/> : !companies.length ? <EmptyState>No saved companies are available. Add evidence through the existing backend collection workflow.</EmptyState> : validRange ? view === 'Compare' ? <Compare companies={companies} selected={selected} setSelected={setComparisonTickers} report={compareQuery.data} error={compareQuery.error} loading={compareQuery.isFetching} metric={filters.metric} openEvidence={setEvidence}/> : view === 'Overview' ? <Overview financials={financialQuery.data} market={marketQuery.data} financialError={financialQuery.error} marketError={marketQuery.error} financialLoading={financialQuery.isFetching} marketLoading={marketQuery.isFetching} filters={filters} setFilters={setFilters} openFinancial={openFinancial} openMarket={() => marketQuery.data && setEvidence({ kind: 'market', report: marketQuery.data })}/> : financialQuery.isFetching ? <Loading/> : financialQuery.error ? <ErrorState error={financialQuery.error}/> : financialQuery.data ? view === 'Financials' ? <Financials report={financialQuery.data} metric={filters.metric} openEvidence={openFinancial}/> : <Statements report={financialQuery.data} openEvidence={openFinancial}/> : null : null}</div>
      <footer className="workspace-footer"><span><span className="status-dot"/>A private view of saved evidence</span><span>Opening or reloading this page never collects upstream data.</span></footer>
    </main></div><EvidenceDrawer selection={evidence} onClose={() => setEvidence(null)}/></div>;
}
