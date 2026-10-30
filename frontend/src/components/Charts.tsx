import { useEffect, useRef, useState } from 'react';
import {
  CandlestickSeries, ColorType, createChart, createSeriesMarkers, HistogramSeries,
  type CandlestickData, type IChartApi, type ISeriesApi, type SeriesMarker, type Time,
} from 'lightweight-charts';
import { Bar, BarChart, CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { displayNumber, formatValue, metricLabel, shortDate } from '../format';
import type { ComparisonItem, FinancialPeriod, MarketReport } from '../types';
import { EmptyState } from './ui';

const palette = ['#087f78', '#5e68a8', '#bc743c', '#8b557a'];
const chartMargin = { top: 16, right: 16, bottom: 14, left: 4 };

export function PriceChart({ report }: { report: MarketReport }) {
  const container = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candlesRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const volumeRef = useRef<ISeriesApi<'Histogram'> | null>(null);
  const markersRef = useRef<ReturnType<typeof createSeriesMarkers<Time>> | null>(null);
  const [chartError, setChartError] = useState(false);
  const hasBars = report.bars.length > 0;

  useEffect(() => {
    if (!hasBars || !container.current) return;
    let chart: IChartApi | undefined;
    let observer: ResizeObserver | undefined;
    try {
      chart = createChart(container.current, {
        height: 390, width: container.current.clientWidth || 600,
        layout: { background: { type: ColorType.Solid, color: '#ffffff' }, textColor: '#697780', fontFamily: 'system-ui, sans-serif', fontSize: 11, attributionLogo: true },
        grid: { vertLines: { visible: false }, horzLines: { color: '#edf0f0' } },
        rightPriceScale: { borderVisible: false },
        timeScale: { borderVisible: false, timeVisible: false, rightOffset: 3 },
        crosshair: { vertLine: { color: '#8eaaa7', labelBackgroundColor: '#164840' }, horzLine: { color: '#8eaaa7', labelBackgroundColor: '#164840' } },
        localization: { locale: 'en-US' },
      });
      chartRef.current = chart;
      setChartError(false);
      candlesRef.current = chart.addSeries(CandlestickSeries, { upColor: '#12887d', downColor: '#cc756a', borderUpColor: '#12887d', borderDownColor: '#cc756a', wickUpColor: '#12887d', wickDownColor: '#cc756a' });
      volumeRef.current = chart.addSeries(HistogramSeries, { priceFormat: { type: 'volume' }, priceScaleId: '', priceLineVisible: false, lastValueVisible: false }, 1);
      chart.panes()[0].setStretchFactor(4);
      chart.panes()[1].setStretchFactor(1);
      markersRef.current = createSeriesMarkers(candlesRef.current, []);
      observer = new ResizeObserver(entries => {
        const width = entries[0]?.contentRect.width;
        if (width && chart) chart.resize(width, 390);
      });
      observer.observe(container.current);
    } catch { setChartError(true); }
    return () => { observer?.disconnect(); chart?.remove(); chartRef.current = null; candlesRef.current = null; volumeRef.current = null; markersRef.current = null; };
  }, [hasBars]);

  useEffect(() => {
    if (!candlesRef.current || !volumeRef.current) return;
    const bars = report.bars.toSorted((a, b) => a.date.localeCompare(b.date));
    const candles: CandlestickData<Time>[] = [];
    const volume: { time: Time; value: number; color: string }[] = [];
    for (const bar of bars) {
      const open = displayNumber(bar.open), high = displayNumber(bar.high), low = displayNumber(bar.low), close = displayNumber(bar.close), quantity = displayNumber(bar.volume);
      if (open === null || high === null || low === null || close === null) continue;
      candles.push({ time: bar.date, open, high, low, close });
      if (quantity !== null) volume.push({ time: bar.date, value: quantity, color: close >= open ? '#b7d8d1' : '#ecd0ca' });
    }
    candlesRef.current.setData(candles);
    volumeRef.current.setData(volume);
    const dates = new Set(candles.map(bar => bar.time));
    const markers: SeriesMarker<Time>[] = report.actions.filter(action => dates.has(action.ex_date)).toSorted((a, b) => a.ex_date.localeCompare(b.ex_date)).map(action => ({
      time: action.ex_date, position: 'aboveBar', shape: 'circle', color: action.kind === 'split' ? '#6a66aa' : '#b68539', text: action.kind === 'split' ? 'S' : 'D',
    }));
    markersRef.current?.setMarkers(markers);
    chartRef.current?.timeScale().fitContent();
  }, [report]);

  if (!report.bars.length) return <EmptyState>No saved market sessions in this date range.</EmptyState>;
  return <><div data-testid="price-chart" role="img" aria-label={`${report.ticker} daily ${report.adjustment === 'raw' ? 'raw' : `${report.adjustment}-adjusted`} OHLC prices and volume, ${report.bars.length} observed sessions`} className="price-chart" ref={container}/>{chartError && <p role="status" className="chart-error">Chart rendering is unavailable in this browser. Exact session values remain in market evidence.</p>}<div className="chart-footnote"><span><i className="key-dot teal"/> Daily OHLC</span><span><i className="key-dot pale"/> Volume</span><span><i className="key-dot amber"/> D · dividend</span><span><i className="key-dot purple"/> S · split</span><span className="chart-attribution">Charts by <a href="https://www.tradingview.com/" target="_blank" rel="noreferrer">TradingView</a></span></div></>;
}

export function FinancialChart({ periods, metric }: { periods: FinancialPeriod[]; metric: string }) {
  if (!periods.length) return <EmptyState>No reporting periods in the selected range.</EmptyState>;
  const data = periods.toSorted((a, b) => a.period.end.localeCompare(b.period.end)).map(item => ({
    date: item.period.end, value: displayNumber(item.metrics[metric]?.value), original: item.metrics[metric]?.value ?? null,
    unit: item.metrics[metric]?.unit ?? '', start: item.period.start, status: item.metrics[metric]?.status ?? 'missing',
  }));
  const unit = data.find(item => item.unit)?.unit ?? '';
  return <div data-testid="financial-chart" className="financial-chart" role="img" aria-label={`${metricLabel(metric)} by exact reporting-period end date; unavailable values are gaps`}>
    <ResponsiveContainer width="100%" height="100%" minWidth={0} initialDimension={{ width: 700, height: 300 }}>
      <BarChart data={data} margin={chartMargin} accessibilityLayer>
        <CartesianGrid vertical={false} stroke="#edf0ef"/>
        <XAxis dataKey="date" tickFormatter={value => shortDate(String(value))} tickLine={false} axisLine={false} minTickGap={24} tick={{ fill: '#728078', fontSize: 11 }}/>
        <YAxis tickFormatter={value => formatValue(String(value), unit)} tickLine={false} axisLine={false} width={78} tick={{ fill: '#728078', fontSize: 11 }}/>
        <ReferenceLine y={0} stroke="#d5ddda"/>
        <Tooltip cursor={{ fill: '#f0f6f3' }} content={({ active, payload }) => {
          const point = payload?.[0]?.payload as typeof data[number] | undefined;
          return active && point ? <div className="chart-tooltip"><strong>{shortDate(point.date)}</strong><p>{point.start} → {point.date}</p><b>{formatValue(point.original, point.unit, false)}</b><p>{point.status}</p></div> : null;
        }}/>
        <Bar dataKey="value" name={metricLabel(metric)} fill="#16867b" maxBarSize={48} radius={[3, 3, 0, 0]} isAnimationActive={false}/>
      </BarChart>
    </ResponsiveContainer>
  </div>;
}

export function FinancialComparisonChart({ companies, metric }: { companies: ComparisonItem[]; metric: string }) {
  // Each series retains its own real reporting dates. No fiscal-year remapping or interpolation.
  const units = [...new Set(companies.flatMap(item => item.financials?.periods ?? []).map(period => period.metrics[metric]?.unit).filter((unit): unit is string => !!unit))];
  const unit = units[0] ?? '';
  const hasData = companies.some(item => item.financials?.periods.length);
  if (!hasData) return <EmptyState>No comparable financial observations in this range.</EmptyState>;
  if (units.length > 1) return <div className="notice" role="note"><strong>Incompatible metric units: {units.join(', ')}.</strong> This combined financial chart is unavailable. Company values retain their own units above; no currency conversion or normalization is applied.</div>;
  return <div className="financial-chart" data-testid="comparison-financial-chart" role="img" aria-label={`${metricLabel(metric)} comparison on each company's actual reporting dates`}>
    <ResponsiveContainer width="100%" height="100%" minWidth={0} initialDimension={{ width: 700, height: 300 }}>
      <LineChart margin={chartMargin} accessibilityLayer>
        <CartesianGrid vertical={false} stroke="#edf0ef"/>
        <XAxis dataKey="time" type="number" scale="time" domain={['dataMin', 'dataMax']} tickFormatter={value => shortDate(new Date(Number(value)).toISOString().slice(0, 10))} tickLine={false} axisLine={false} tick={{ fill: '#728078', fontSize: 11 }}/>
        <YAxis tickFormatter={value => formatValue(String(value), unit)} tickLine={false} axisLine={false} width={78} tick={{ fill: '#728078', fontSize: 11 }}/>
        <Tooltip labelFormatter={value => shortDate(new Date(Number(value)).toISOString().slice(0, 10))} formatter={(value, name) => [value === null || value === undefined ? 'N/A' : formatValue(String(value), unit), String(name)]}/>
        {companies.map((item, index) => <Line key={item.company.ticker} name={item.company.ticker} data={item.financials?.periods.toSorted((a, b) => a.period.end.localeCompare(b.period.end)).map(period => ({ time: Date.parse(`${period.period.end}T00:00:00Z`), value: displayNumber(period.metrics[metric]?.value) })) ?? []} dataKey="value" type="linear" connectNulls={false} stroke={palette[index % palette.length]} strokeWidth={2} dot={{ r: 3 }} activeDot={{ r: 5 }} isAnimationActive={false}/>)}
      </LineChart>
    </ResponsiveContainer>
  </div>;
}

export function PerformanceChart({ companies }: { companies: ComparisonItem[] }) {
  if (!companies.some(item => item.performance.length)) return <EmptyState>No shared market window is available for price performance.</EmptyState>;
  return <div className="financial-chart" data-testid="comparison-performance-chart" role="img" aria-label="Backend-calculated split-adjusted cumulative price return; cash dividends excluded">
    <ResponsiveContainer width="100%" height="100%" minWidth={0} initialDimension={{ width: 700, height: 300 }}>
      <LineChart margin={chartMargin} accessibilityLayer>
        <CartesianGrid vertical={false} stroke="#edf0ef"/>
        <XAxis dataKey="time" type="number" scale="time" domain={['dataMin', 'dataMax']} tickFormatter={value => new Date(Number(value)).getUTCFullYear().toString()} tickLine={false} axisLine={false} tick={{ fill: '#728078', fontSize: 11 }}/>
        <YAxis tickFormatter={value => formatValue(String(value), 'fraction')} tickLine={false} axisLine={false} width={70} tick={{ fill: '#728078', fontSize: 11 }}/>
        <ReferenceLine y={0} stroke="#cdd9d4"/>
        <Tooltip labelFormatter={value => shortDate(new Date(Number(value)).toISOString().slice(0, 10))} formatter={(value, name) => [value === null || value === undefined ? 'N/A' : formatValue(String(value), 'fraction'), String(name)]}/>
        {companies.map((item, index) => <Line key={item.company.ticker} name={item.company.ticker} data={item.performance.map(point => ({ time: Date.parse(`${point.date}T00:00:00Z`), value: point.status === 'ok' || point.status === 'baseline' ? displayNumber(point.value) : null }))} dataKey="value" type="linear" connectNulls={false} stroke={palette[index % palette.length]} strokeWidth={2} dot={false} isAnimationActive={false}/>)}
      </LineChart>
    </ResponsiveContainer>
  </div>;
}
