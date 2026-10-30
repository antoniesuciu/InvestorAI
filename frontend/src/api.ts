import { QueryClient } from '@tanstack/react-query';
import type { Company, ComparisonReport, DataKind, Filters, FinancialReport, MarketReport } from './types';

export class ApiError extends Error {
  constructor(public code: string, message: string, public status: number) {
    super(message);
    this.name = 'ApiError';
  }
}

export const createQueryClient = () => new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: Infinity,
      retry: false,
      refetchOnWindowFocus: false,
      refetchOnReconnect: false,
      refetchInterval: false,
      refetchOnMount: false,
    },
  },
});

async function localGet<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`/api${path}`, { signal, credentials: 'omit', headers: { Accept: 'application/json' } });
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { detail?: { code?: string; message?: string } } | null;
    throw new ApiError(body?.detail?.code ?? 'local_api_error', body?.detail?.message ?? 'The local data service could not complete this request.', response.status);
  }
  return response.json() as Promise<T>;
}

function parameters(filters: Filters) {
  const params = new URLSearchParams({ frequency: filters.frequency });
  if (filters.start) params.set('start', filters.start);
  if (filters.end) params.set('end', filters.end);
  if (filters.asOf) params.set('as_of', filters.asOf);
  return params;
}

export const api = {
  companies: (signal?: AbortSignal) => localGet<{ companies: Company[]; data_kind: DataKind }>('/companies', signal),
  financials: (ticker: string, filters: Filters, signal?: AbortSignal) => localGet<FinancialReport>(`/companies/${encodeURIComponent(ticker)}/financials?${parameters(filters)}`, signal),
  market: (ticker: string, filters: Filters, signal?: AbortSignal) => {
    const params = parameters(filters);
    params.delete('frequency');
    params.set('adjustment', filters.adjustment);
    params.set('feed', 'sip');
    return localGet<MarketReport>(`/companies/${encodeURIComponent(ticker)}/market?${params}`, signal);
  },
  compare: (tickers: string[], filters: Filters, signal?: AbortSignal) => {
    const params = parameters(filters);
    params.set('tickers', tickers.join(','));
    return localGet<ComparisonReport>(`/compare?${params}`, signal);
  },
};
