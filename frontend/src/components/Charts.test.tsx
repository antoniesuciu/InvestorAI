import type { ReactNode } from 'react';
import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { createChart } from 'lightweight-charts';
import { FinancialChart, FinancialComparisonChart, PerformanceChart, PriceChart } from './Charts';
import { syntheticComparison, syntheticMarket, syntheticPeriods } from '../test/fixtures';

vi.mock('recharts', () => ({
  ResponsiveContainer: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  BarChart: ({ children, data }: { children: ReactNode; data: unknown }) => <div><output data-testid="bar-data">{JSON.stringify(data)}</output>{children}</div>,
  LineChart: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  Line: ({ data, name, connectNulls }: { data: unknown; name: string; connectNulls: boolean }) => <output data-testid={`line-${name}`} data-connect-nulls={String(connectNulls)}>{JSON.stringify(data)}</output>,
  Bar: () => null, CartesianGrid: () => null, ReferenceLine: () => null, Tooltip: () => null, XAxis: () => null, YAxis: () => null,
}));

describe('financial chart bindings', () => {
  it('preserves true reporting dates and unavailable gaps instead of manufacturing zero', () => {
    render(<FinancialChart periods={syntheticPeriods} metric="revenue"/>);
    const data = JSON.parse(screen.getByTestId('bar-data').textContent!) as { date: string; value: number | null }[];
    expect(data.map(point => point.date)).toEqual(['2022-09-28', '2023-09-28', '2024-09-28']);
    expect(data[1].value).toBeNull();
    expect(data[2].value).toBeGreaterThan(0);
  });
  it('refuses a combined financial plot with incompatible currencies', () => {
    const companies = structuredClone(syntheticComparison.companies);
    companies[1].financials!.periods = companies[1].financials!.periods.map(period => ({ ...period, metrics: { ...period.metrics, revenue: { ...period.metrics.revenue, unit: 'EUR' } } }));
    render(<FinancialComparisonChart companies={companies} metric="revenue"/>);
    expect(screen.getByRole('note')).toHaveTextContent('Incompatible metric units: USD, EUR');
    expect(screen.getByRole('note')).toHaveTextContent('no currency conversion or normalization');
    expect(screen.queryByTestId('line-AAPL')).not.toBeInTheDocument();
    expect(screen.queryByTestId('line-MSFT')).not.toBeInTheDocument();
  });
  it('retains backend baseline zero and null return gaps without recalculating financial values', () => {
    const companies = structuredClone(syntheticComparison.companies);
    companies[0].performance[0].status = 'baseline';
    render(<PerformanceChart companies={companies}/>);
    const line = screen.getByTestId('line-AAPL');
    const data = JSON.parse(line.textContent!) as { time: number; value: number | null }[];
    expect(data[0].value).toBe(0);
    expect(data[1].value).toBeNull();
    expect(data[2].value).toBe(Number(companies[0].performance[2].value));
    expect(line).toHaveAttribute('data-connect-nulls', 'false');
  });
  it('keeps different company financial observations on their own actual dates', () => {
    render(<FinancialComparisonChart companies={syntheticComparison.companies} metric="revenue"/>);
    const apple = JSON.parse(screen.getByTestId('line-AAPL').textContent!) as { time: number }[];
    const microsoft = JSON.parse(screen.getByTestId('line-MSFT').textContent!) as { time: number }[];
    expect(new Date(apple[0].time).toISOString().slice(0, 10)).toBe('2022-09-28');
    expect(new Date(microsoft[0].time).toISOString().slice(0, 10)).toBe('2022-06-30');
  });
  it('recreates and cleans up the price chart across empty and populated ranges', () => {
    vi.mocked(createChart).mockClear();
    const { rerender, unmount } = render(<PriceChart report={{ ...syntheticMarket, bars: [] }}/>);
    expect(createChart).not.toHaveBeenCalled();
    rerender(<PriceChart report={syntheticMarket}/>);
    expect(createChart).toHaveBeenCalledTimes(1);
    const firstChart = vi.mocked(createChart).mock.results[0].value;
    const firstContainer = vi.mocked(createChart).mock.calls[0][0] as HTMLElement;
    expect(firstContainer.isConnected).toBe(true);
    rerender(<PriceChart report={{ ...syntheticMarket, bars: [] }}/>);
    expect(firstChart.remove).toHaveBeenCalledOnce();
    expect(firstContainer.isConnected).toBe(false);
    rerender(<PriceChart report={syntheticMarket}/>);
    expect(createChart).toHaveBeenCalledTimes(2);
    const secondContainer = vi.mocked(createChart).mock.calls[1][0] as HTMLElement;
    expect(secondContainer.isConnected).toBe(true);
    expect(secondContainer).not.toBe(firstContainer);
    const secondChart = vi.mocked(createChart).mock.results[1].value;
    unmount();
    expect(secondChart.remove).toHaveBeenCalledOnce();
  });
});
