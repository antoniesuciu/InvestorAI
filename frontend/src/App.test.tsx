import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import App from './App';
import { createQueryClient } from './api';
import { syntheticCompanies, syntheticComparison, syntheticFinancials, syntheticMarket } from './test/fixtures';

const fetchMock = vi.fn<typeof fetch>();
const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
function mockLocalApi(marketStatus = 200) {
  fetchMock.mockImplementation(async input => {
    const path = String(input);
    if (path === '/api/companies') return response({ companies: syntheticCompanies, data_kind: 'synthetic' });
    if (path.startsWith('/api/compare?')) return response(syntheticComparison);
    if (path.includes('/financials?')) return response(syntheticFinancials);
    if (path.includes('/market?')) return marketStatus === 200 ? response(syntheticMarket) : response({ detail: { code: 'market_not_found', message: 'No saved market series matches these filters.' } }, marketStatus);
    return response({ detail: { code: 'not_found', message: 'Unknown local route' } }, 404);
  });
}
function mount() { const client = createQueryClient(); return { ...render(<QueryClientProvider client={client}><App/></QueryClientProvider>), client }; }
beforeEach(() => { vi.stubGlobal('fetch', fetchMock); fetchMock.mockReset(); mockLocalApi(); });

describe('local research workspace', () => {
  it('shows explicit synthetic labelling, actual source coverage, zero and missing states', async () => {
    mount();
    expect(await screen.findByTestId('price-chart')).toBeInTheDocument();
    expect(screen.getByText('Synthetic test data')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Free cash flow: \$0/ })).toBeInTheDocument();
    expect(screen.getByText(/Market history ends Jan 10, 2025 despite retrieval/)).toBeInTheDocument();
    expect(screen.getByText('9.52%')).toBeInTheDocument();
    expect(screen.getByRole('img', { name: /daily split-adjusted OHLC/ })).toBeInTheDocument();
  });
  it('navigates all four views and keeps fractional price returns separate from percent-point margins', async () => {
    const user = userEvent.setup(); mount();
    await screen.findByTestId('price-chart');
    expect(screen.getByRole('button', { name: /Net margin: 15%/ })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Financials' }));
    expect(await screen.findByText('Recent observations')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Current ratio: N\/A/ })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Statements' }));
    await user.click(screen.getByRole('button', { name: 'Cash flow' }));
    expect(screen.getByRole('table')).toBeInTheDocument();
    expect(screen.getAllByText('$0.00').length).toBeGreaterThan(0);
    await user.click(screen.getByRole('button', { name: 'Compare' }));
    expect(await screen.findByTestId('comparison-performance-chart')).toBeInTheDocument();
    expect(screen.getByText('Fiscal intervals differ.')).toBeInTheDocument();
    expect(screen.getByTestId('comparison-financial-chart')).toBeInTheDocument();
    expect(screen.getByText(/this is not total return/)).toBeInTheDocument();
  });
  it('passes native filter values to the local API without upstream calls', async () => {
    const user = userEvent.setup(); mount(); await screen.findByTestId('price-chart');
    await user.selectOptions(screen.getByLabelText('Frequency'), 'quarterly');
    await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url).includes('frequency=quarterly'))).toBe(true));
    fireEvent.change(screen.getByLabelText('Start date'), { target: { value: '2020-01-01' } });
    fireEvent.change(screen.getByLabelText('End date'), { target: { value: '2024-12-31' } });
    fireEvent.change(screen.getByLabelText(/Observed by/), { target: { value: '2026-10-28' } });
    await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url).includes('as_of=2026-10-28'))).toBe(true));
    await user.selectOptions(screen.getByLabelText('Metric'), 'net_margin_pct');
    expect(screen.getByRole('img', { name: /^Net margin by exact/ })).toBeInTheDocument();
    expect(fetchMock.mock.calls.every(([url]) => String(url).startsWith('/api/'))).toBe(true);
    const count = fetchMock.mock.calls.length;
    window.dispatchEvent(new Event('focus'));
    expect(fetchMock.mock.calls.length).toBe(count);
    await user.click(screen.getByRole('button', { name: 'Reload local data' }));
    await waitFor(() => expect(fetchMock.mock.calls.length).toBeGreaterThan(count));
  });
  it('shows market errors while keeping usable company financials visible', async () => {
    mockLocalApi(404); mount();
    expect(await screen.findByText('Market evidence unavailable')).toBeInTheDocument();
    expect(screen.getByText('No saved market series matches these filters.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Revenue: \$120B/ })).toBeInTheDocument();
    expect(screen.getByTestId('financial-chart')).toBeInTheDocument();
  });
  it('opens original decimal values, formula, accession and safe filing links in the evidence dialog', async () => {
    const user = userEvent.setup(); mount(); await screen.findByTestId('price-chart');
    await user.click(screen.getByRole('button', { name: /Net margin: 15%/ }));
    const dialog = screen.getByRole('dialog', { name: 'Evidence & provenance' });
    expect(within(dialog).getByText('net_income / revenue * 100')).toBeInTheDocument();
    expect(within(dialog).getAllByText('120000000000.123456789').length).toBeGreaterThan(0);
    expect(within(dialog).getAllByText('SyntheticRevenueConcept').length).toBeGreaterThan(0);
    expect(within(dialog).getAllByText(/0000000001-24-000001/).length).toBeGreaterThan(0);
    expect(within(dialog).getAllByRole('link', { name: 'Original SEC filing' })[0]).toHaveAttribute('href', 'https://www.sec.gov/Archives/synthetic-test-only.htm');
    await user.click(within(dialog).getByRole('button', { name: 'Close evidence' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
  it('shows session decimals and unknown action currency in market provenance', async () => {
    const user = userEvent.setup(); mount(); await screen.findByTestId('price-chart');
    await user.click(screen.getByRole('button', { name: 'Market evidence' }));
    const dialog = screen.getByRole('dialog');
    expect(within(dialog).getByText(/Amount 0.25 · currency unknown/)).toBeInTheDocument();
    await user.selectOptions(within(dialog).getByLabelText('Market session'), '2024-01-02');
    expect(within(dialog).getByText('100.123456789 / 110 / 99 / 105 USD')).toBeInTheDocument();
    expect(within(dialog).getByText('synthetic-hash-1')).toBeInTheDocument();
    expect(within(dialog).getByText('America/New_York')).toBeInTheDocument();
    expect(within(dialog).getByText('XNAS')).toBeInTheDocument();
    await user.selectOptions(within(dialog).getByLabelText('Calendar session'), '2024-01-02');
    expect(within(dialog).getByText('2024-01-02T09:30:00-05:00')).toBeInTheDocument();
  });
  it('renders loading and local service failures without a fabricated fallback', async () => {
    fetchMock.mockImplementation(() => new Promise(() => {}));
    const { unmount } = mount();
    expect(screen.getByRole('status')).toHaveTextContent('Opening the local watchlist');
    expect(screen.queryByText('$120B')).not.toBeInTheDocument();
    unmount();
    fetchMock.mockResolvedValue(response({ detail: { code: 'store_unavailable', message: 'Saved evidence could not be read.' } }, 503));
    mount();
    expect(await screen.findByRole('alert')).toHaveTextContent('Local service unavailable');
    expect(screen.getByText(/store_unavailable/)).toBeInTheDocument();
    expect(screen.queryByTestId('price-chart')).not.toBeInTheDocument();
  });
  it('rejects reversed date ranges visibly', async () => {
    mount(); await screen.findByTestId('price-chart');
    fireEvent.change(screen.getByLabelText('Start date'), { target: { value: '2025-01-01' } });
    fireEvent.change(screen.getByLabelText('End date'), { target: { value: '2020-01-01' } });
    expect(screen.getByRole('alert')).toHaveTextContent('Start date must be on or before end date.');
  });
});
