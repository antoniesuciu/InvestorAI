import { useEffect, useRef, useState } from 'react';
import { metricLabel, periodLabel, safeSourceUrl, shortDate, statusLabel, timestamp } from '../format';
import type { Evidence, FinancialPeriod, FinancialReport, Freshness, MarketReport, Metric, Snapshot } from '../types';
import { Icon, Limitations } from './ui';

export type EvidenceSelection = { kind: 'financial'; report: FinancialReport; period: FinancialPeriod; key: string; metric: Metric | undefined } | { kind: 'market'; report: MarketReport };

function SourceLink({ url, children = 'Original source' }: { url: string | null | undefined; children?: string }) {
  const href = safeSourceUrl(url);
  return href ? <a className="source-link" href={href} target="_blank" rel="noreferrer">{children}<Icon name="source" size={12}/></a> : <span className="muted">Source link unavailable</span>;
}

function SnapshotDetails({ snapshot }: { snapshot: Snapshot }) {
  return <dl className="evidence-fields"><div><dt>Snapshot</dt><dd className="mono">{snapshot.snapshot_id}</dd></div><div><dt>Source retrieval</dt><dd>{timestamp(snapshot.retrieved_at)}</dd></div><div><dt>Snapshot created</dt><dd>{timestamp(snapshot.created_at)}</dd></div></dl>;
}

function RefreshDetails({ freshness }: { freshness: Freshness }) {
  return <dl className="evidence-fields"><div><dt>Latest collection attempt</dt><dd>{timestamp(freshness.last_attempt_at)} · {freshness.last_attempt_status ?? 'Status unavailable'}</dd></div><div><dt>Last failed collection</dt><dd>{timestamp(freshness.last_failed_at)}</dd></div><div><dt>Snapshot completed</dt><dd>{timestamp(freshness.completed_at)}</dd></div></dl>;
}

function CalendarEvidence({ report }: { report: MarketReport }) {
  const [date, setDate] = useState(report.calendar?.sessions.at(-1)?.date ?? '');
  const session = report.calendar?.sessions.find(item => item.date === date);
  return <><h3 className="drawer-section-title">Trading calendar evidence</h3><dl className="evidence-fields"><div><dt>Listing MIC</dt><dd>{report.mic ?? 'Unknown'}</dd></div><div><dt>Exchange timezone</dt><dd>{report.timezone ?? 'Unknown'}</dd></div><div><dt>Calendar coverage</dt><dd>{report.calendar ? `${report.calendar.start} → ${report.calendar.end}` : 'Not available'}</dd></div><div><dt>Calendar observed</dt><dd>{timestamp(report.calendar?.observed_at)}</dd></div><div><dt>Returned sessions</dt><dd>{report.calendar?.sessions.length ?? 'Not available'}</dd></div></dl>{report.calendar?.sessions.length ? <><label className="field session-picker">Calendar session<select value={date} onChange={event => setDate(event.target.value)}>{report.calendar.sessions.toReversed().map(item => <option key={item.date} value={item.date}>{item.date}</option>)}</select></label><dl className="evidence-fields"><div><dt>Source market open</dt><dd>{session?.open_at ?? 'Not available'}</dd></div><div><dt>Source market close</dt><dd>{session?.close_at ?? 'Not available'}</dd></div></dl></> : <p className="muted">No returned calendar sessions in this selected range. Sessions are never inferred from weekdays.</p>}</>;
}

function SourceEvidence({ evidence, index }: { evidence: Evidence; index: number }) {
  return <article className="source-record"><div className="source-record-heading"><span className="source-index">{String(index + 1).padStart(2, '0')}</span><div><h4>{evidence.concept || evidence.metric}</h4><span className="muted">{evidence.taxonomy} · {evidence.form} · {evidence.accession}</span></div></div>
    <dl className="evidence-fields">
      <div><dt>Original decimal value</dt><dd className="mono">{evidence.value ?? 'N/A'} <span className="muted">{evidence.unit}</span></dd></div>
      <div><dt>Reporting interval</dt><dd>{evidence.start ? `${evidence.start} → ` : 'Instant at '}{evidence.end} · {evidence.period_type}</dd></div>
      <div><dt>Fiscal labels</dt><dd>{evidence.fiscal_year ?? 'Unknown'} / {evidence.fiscal_quarter ? `Q${evidence.fiscal_quarter}` : 'Quarter unknown'}</dd></div>
      <div><dt>Filed</dt><dd>{evidence.filed || 'Not available'}</dd></div>
      <div><dt>SEC acceptance</dt><dd>{timestamp(evidence.acceptance_datetime)}</dd></div>
      <div><dt>Observed / retrieved</dt><dd>{timestamp(evidence.retrieved_at)}</dd></div>
      <div><dt>Analysis available</dt><dd>{timestamp(evidence.analysis_available_at)}</dd></div>
      <div><dt>Record identifier</dt><dd className="mono">{evidence.record_id}</dd></div>
      <div><dt>Quality flags</dt><dd>{evidence.quality_flags?.length ? evidence.quality_flags.map(statusLabel).join(' · ') : 'None recorded'}</dd></div>
    </dl><div className="source-links"><SourceLink url={evidence.filing_url}>Original SEC filing</SourceLink><SourceLink url={evidence.source_url}>Source response</SourceLink></div>
  </article>;
}

function FinancialEvidence({ selection }: { selection: Extract<EvidenceSelection, { kind: 'financial' }> }) {
  const { report, period, key, metric } = selection;
  return <>
    <div className="drawer-value"><div className="eyebrow">{report.ticker} · {metricLabel(key)}</div><strong>{metric?.value ?? 'N/A'} <small>{metric?.unit ?? report.unit}</small></strong><span>{periodLabel(period)}</span></div>
    <dl className="evidence-fields">
      <div><dt>Classification</dt><dd>{statusLabel(metric?.classification ?? 'unavailable')}</dd></div>
      <div><dt>Metric status</dt><dd>{statusLabel(metric?.status ?? 'missing')}</dd></div>
      <div><dt>Exact interval</dt><dd>{period.period.start} → {period.period.end}</dd></div>
      <div><dt>Formula from backend</dt><dd className="mono">{metric?.formula ?? 'Reported value; no analytical formula'}</dd></div>
      <div><dt>Observation cutoff</dt><dd>{timestamp(report.as_of)}</dd></div>
      <div><dt>Statement accession</dt><dd className="mono">{period.period.statement_accession ?? 'Not available'}</dd></div>
      <div><dt>Balance accession</dt><dd className="mono">{period.period.balance_accession ?? 'Not available'}</dd></div>
      <div><dt>Statement selection</dt><dd>{statusLabel(period.period.selection_status)}</dd></div>
      <div><dt>Balance selection</dt><dd>{statusLabel(period.period.balance_selection_status)}</dd></div>
      <div><dt>Fiscal status</dt><dd>{statusLabel(period.period.fiscal_status)}</dd></div>
      <div><dt>Metric flags</dt><dd>{metric?.flags.length ? metric.flags.map(statusLabel).join(' · ') : 'None recorded'}</dd></div>
    </dl>
    <h3 className="drawer-section-title">Calculation inputs <span>{metric?.inputs.length ?? 0}</span></h3>
    {metric?.inputs.length ? metric.inputs.map((input, index) => <SourceEvidence key={`${input.record_id}-${index}`} evidence={input} index={index}/>) : <p className="muted">No usable inputs are available for this metric. Missing values remain N/A.</p>}
    {!!metric?.selection_evidence.length && <details className="evidence-disclosure"><summary>Selection and revision evidence ({metric.selection_evidence.length})</summary>{metric.selection_evidence.map((input, index) => <SourceEvidence key={`${input.record_id}-${index}`} evidence={input} index={index}/>)}</details>}
    {!!metric?.comparability_evidence.length && <><h3 className="drawer-section-title">Reviewed accounting evidence</h3>{metric.comparability_evidence.map(review => <article key={review.id} className="source-record"><h4>{statusLabel(review.kind)}</h4><p>{review.basis}</p><dl className="evidence-fields"><div><dt>Review ID</dt><dd>{review.id}</dd></div><div><dt>Verified at</dt><dd>{timestamp(review.verified_at)}</dd></div><div><dt>Checks</dt><dd>{review.checks.map(statusLabel).join(' · ')}</dd></div></dl>{review.documents.map(document => <div key={document.sha256} className="review-document"><SourceLink url={document.url}>Reviewed original document</SourceLink><p>Retrieved {timestamp(document.retrieved_at)}</p><code>SHA-256 {document.sha256}</code></div>)}</article>)}</>}
    <h3 className="drawer-section-title">Local evidence snapshot</h3><SnapshotDetails snapshot={report.snapshot}/><RefreshDetails freshness={report.freshness}/><Limitations items={[...report.limitations, ...report.diagnostics]} />
  </>;
}

function MarketEvidence({ report }: { report: MarketReport }) {
  const [date, setDate] = useState(report.bars.at(-1)?.date ?? '');
  const selected = report.bars.find(bar => bar.date === date) ?? report.bars.at(-1);
  return <>
    <div className="drawer-value"><div className="eyebrow">{report.ticker} · Market evidence</div><strong>{report.adjustment === 'raw' ? 'Raw' : `${statusLabel(report.adjustment)} adjusted`} <small>{report.currency}</small></strong><span>{report.provider} · {report.feed.toUpperCase()} · Daily observations</span></div>
    <dl className="evidence-fields"><div><dt>Observed instrument ID</dt><dd className="mono">{report.instrument_id ?? 'Not available'}</dd></div></dl>
    <dl className="evidence-fields"><div><dt>Coverage</dt><dd>{report.coverage.start ?? 'N/A'} → {report.coverage.end ?? 'N/A'}</dd></div><div><dt>Latest session</dt><dd>{shortDate(report.freshness.latest_session)}</dd></div><div><dt>Source retrieval</dt><dd>{timestamp(report.freshness.retrieved_at)}</dd></div><div><dt>Coverage status</dt><dd>{statusLabel(report.freshness.status)} · {report.freshness.coverage_age_days ?? 'Unknown'} days since coverage</dd></div><div><dt>Adjustment anchor</dt><dd>{report.adjustment_as_of ?? 'Not applicable / not available'}</dd></div><div><dt>Observation cutoff</dt><dd>{timestamp(report.as_of)}</dd></div><div><dt>Price return status</dt><dd>{report.price_return.status}{report.price_return.reason ? ` · ${report.price_return.reason}` : ''}</dd></div><div><dt>Return decimal (fraction)</dt><dd className="mono">{report.price_return.value ?? 'N/A'}</dd></div><div><dt>Backend return formula</dt><dd className="mono">{report.price_return.formula ?? 'Unavailable'}</dd></div><div><dt>Actual return window</dt><dd>{report.price_return.start ?? 'N/A'} → {report.price_return.end ?? 'N/A'}</dd></div></dl>
    <p className="notice">Price returns exclude cash dividends, reinvestment, fees and tax. Dividend-adjusted price history is not a verified total return series.</p>
    <h3 className="drawer-section-title">Session evidence</h3>
    <label className="field session-picker">Market session<select value={selected?.date ?? ''} onChange={event => setDate(event.target.value)}>{report.bars.toReversed().map(bar => <option key={bar.record_id} value={bar.date}>{bar.date}</option>)}</select></label>
    {selected && <dl className="evidence-fields"><div><dt>Open / high / low / close</dt><dd className="mono">{selected.open} / {selected.high} / {selected.low} / {selected.close} {report.currency}</dd></div><div><dt>Volume</dt><dd className="mono">{selected.volume}</dd></div><div><dt>Observed at</dt><dd>{timestamp(selected.observed_at)}</dd></div><div><dt>Source publication</dt><dd>{timestamp(selected.published_at)}</dd></div><div><dt>Record identifier</dt><dd className="mono">{selected.record_id}</dd></div><div><dt>Adjustment / anchor</dt><dd>{selected.adjustment} / {selected.adjustment_as_of ?? 'N/A'}</dd></div><div><dt>Source SHA-256</dt><dd className="mono">{selected.source.sha256}</dd></div></dl>}
    <CalendarEvidence report={report}/>
    <h3 className="drawer-section-title">Corporate actions <span>{report.actions.length}</span></h3>
    <p className="muted">An exhausted provider response does not establish complete action history. Unknown cash currency remains unknown.</p>
    <div className="table-scroll"><table className="evidence-table"><caption className="sr-only">Saved corporate-action provenance</caption><thead><tr><th>Action / ex-date</th><th>Reported terms</th><th>Source dates</th></tr></thead><tbody>{report.actions.map(action => <tr key={action.id}><td><strong>{statusLabel(action.kind)}</strong><br/>{action.ex_date}<details><summary>Identifiers</summary><code>Provider ID {action.id}</code><code>Record ID {action.record_id ?? 'unknown'}</code><p>Observed {action.observed_at}</p><p>Published {action.published_at ?? 'unknown'}</p><p>Declared {action.declared_at ?? 'unknown'}</p><code>SHA-256 {action.source.sha256}</code></details></td><td>{action.kind === 'split' ? `Ratio ${action.ratio ?? 'unknown'}` : `Amount ${action.amount ?? 'unknown'} · ${action.cash_currency ?? 'currency unknown'}`}<br/><span className="muted">{action.currency_status ?? 'Currency status unknown'}</span></td><td>Process {action.process_date ?? 'unknown'}<br/>Record {action.record_date ?? 'unknown'}<br/>Payment {action.payment_date ?? 'unknown'}</td></tr>)}</tbody></table></div>
    <details className="evidence-disclosure"><summary>Data quality diagnostics</summary><pre className="quality-json">{JSON.stringify(report.quality, null, 2)}</pre></details>
    <h3 className="drawer-section-title">Local evidence snapshot</h3><SnapshotDetails snapshot={report.snapshot}/><RefreshDetails freshness={report.freshness}/><Limitations items={report.limitations}/>
  </>;
}

export function EvidenceDrawer({ selection, onClose }: { selection: EvidenceSelection | null; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    if (selection) ref.current?.showModal();
    else ref.current?.close();
  }, [selection]);
  return <dialog className="evidence-drawer" ref={ref} aria-labelledby="evidence-title" onCancel={onClose} onClick={event => { if (event.target === event.currentTarget) onClose(); }}><div className="drawer-header"><div><h2 id="evidence-title">Evidence &amp; provenance</h2><div className="eyebrow">Where this number came from</div></div><button type="button" className="icon-button" aria-label="Close evidence" onClick={onClose}><Icon name="close"/></button></div><div className="drawer-body">{selection?.kind === 'financial' ? <FinancialEvidence selection={selection}/> : selection?.kind === 'market' ? <MarketEvidence key={selection.report.snapshot.snapshot_id} report={selection.report}/> : null}</div></dialog>;
}
