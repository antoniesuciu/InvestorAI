import type { ReactNode } from 'react';
import { ApiError } from '../api';
import { shortDate, statusLabel } from '../format';
import type { DataKind, Freshness } from '../types';

export function Icon({ name, size = 19 }: { name: 'overview' | 'financials' | 'statements' | 'compare' | 'arrow' | 'reload' | 'close' | 'source' | 'check'; size?: number }) {
  const paths = {
    overview: <><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/></>,
    financials: <><path d="M4 20V11m8 9V4m8 16v-7M2 21h20"/></>,
    statements: <><path d="M6 3h9l4 4v14H6zM14 3v5h5M9 12h7M9 16h7"/></>,
    compare: <><path d="M4 17 10 11l4 3 6-9M4 6l5 1 5 12 6-3"/><path d="M16 5h4v4"/></>,
    arrow: <><path d="M5 12h14m-5-5 5 5-5 5"/></>,
    reload: <><path d="M20 10a8 8 0 1 0-1 7M20 4v6h-6"/></>,
    close: <path d="m6 6 12 12M6 18 18 6"/>,
    source: <><path d="M14 3h7v7m0-7L10 14M10 5H4v16h16v-6"/></>,
    check: <path d="m5 12 4 4L19 6"/>,
  };
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>;
}

export function DataKindBanner({ kind }: { kind?: DataKind }) {
  return kind === 'synthetic' ? <div className="synthetic-banner" role="note"><strong>Synthetic test data</strong> · Invented fixtures for validation. These values are not reported company or market evidence.</div> : null;
}

export function Panel({ title, eyebrow, action, children, className = '' }: { title: string; eyebrow?: string; action?: ReactNode; children: ReactNode; className?: string }) {
  return <section className={`panel ${className}`}><div className="panel-heading"><div>{eyebrow && <div className="eyebrow">{eyebrow}</div>}<h2>{title}</h2></div>{action}</div>{children}</section>;
}

export function Loading({ label = 'Reading local evidence' }: { label?: string }) {
  return <div role="status" className="loading"><span className="spinner"/>{label}<span className="loading-line"/><span className="loading-line short"/></div>;
}

export function ErrorState({ error, title = 'Data unavailable' }: { error: Error; title?: string }) {
  return <div role="alert" className="error-state"><span className="error-symbol">!</span><div><strong>{title}</strong><p>{error.message}</p>{error instanceof ApiError && <code>{error.code} · HTTP {error.status}</code>}<p className="muted">Check the selected dates and observation cutoff, then reload local data.</p></div></div>;
}

export function EmptyState({ children }: { children: ReactNode }) {
  return <div className="empty-state"><span aria-hidden="true">—</span><p>{children}</p></div>;
}

export function FreshnessStrip({ label, freshness }: { label: string; freshness: Freshness }) {
  const coverage = freshness.latest_session ?? freshness.latest_period;
  return <div className={`freshness-strip ${freshness.status}`}>
    <span className="status-dot"/><strong>{label}</strong>
    <span>{freshness.status === 'stale' ? 'Historical coverage' : statusLabel(freshness.status)}</span>
    <span className="freshness-detail">{freshness.latest_session ? 'Latest session' : 'Latest reporting period'} <b>{shortDate(coverage)}</b></span>
    <span className="freshness-detail">Retrieved <b>{shortDate(freshness.retrieved_at)}</b></span>
    {freshness.note && <p>{freshness.note}</p>}
    {freshness.last_attempt_status === 'failed' && <p className="error-text"><strong>Last collection attempt failed</strong>{freshness.last_attempt_at ? ` · ${shortDate(freshness.last_attempt_at)}` : ''}. Saved evidence retains its original retrieval time.</p>}
    {freshness.last_failed_at && freshness.last_attempt_status !== 'failed' && <p>Last failed collection {shortDate(freshness.last_failed_at)} · Latest attempt {freshness.last_attempt_status ?? 'unknown'}</p>}
  </div>;
}

export function Limitations({ items, title = 'Coverage & methodology' }: { items: string[]; title?: string }) {
  if (!items.length) return null;
  return <details className="limitations"><summary>{title}<span>{items.length} notes</span></summary><ul>{items.map((item, index) => <li key={`${index}-${item}`}>{item}</li>)}</ul></details>;
}
