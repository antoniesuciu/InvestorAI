# Local research dashboard

React and TypeScript client for the repository's read-only local API. Use Node 22.22.2 and npm 10.9.7, the validated environment. The full development/test toolchain requires Node `^22.22.2 || ^24.15.0 || >=26.0.0` (the jsdom requirement); Vite alone supports earlier runtimes. Exact package versions and transitive dependencies are recorded in `package-lock.json`.

Start the Python dashboard API on `127.0.0.1:8000` using the repository README, then run from this directory:

```sh
npm ci --ignore-scripts
npm run dev
```

Open `http://127.0.0.1:5173`. The development server binds to loopback, requires that exact port, proxies `/api` to the local Python server and restricts file serving to this frontend. There is no public asset directory. Environment files, Git metadata, local data, keys and credential paths are denied. Only the `DASHBOARD_PUBLIC_` environment prefix can be exposed by Vite; the application needs no browser environment variables or credentials.

The dashboard never calls a provider or collector. Initial navigation, changed filters and **Reload local data** read saved evidence through the local API. TanStack Query caches responses without polling, focus/reconnect refresh or automatic retries. Reload invalidates cached local queries. API errors remain visible; there is no demonstration-data fallback.

## Views and interpretation

- **Overview:** selected-period metrics, saved daily OHLCV, split/dividend markers, backend price return, coverage and retrieval times.
- **Financials:** financial trends on actual reporting dates and recent observations.
- **Statements:** focused reported income, balance and cash-flow facts, plus defined backend ratios/growth. Select any cell for evidence and unavailable reasons.
- **Compare:** each company's actual financial intervals and backend-computed split-adjusted cumulative price returns over the common saved market window. Monetary series with incompatible units are not combined. No fiscal-year alignment, currency conversion or financial calculations run in React.

Money, percentages, ratios and returns retain their backend units. `percent` already contains percentage points; `fraction` uses percentage formatting. Missing values remain N/A and chart gaps; explicit zero remains zero. Decimal strings are retained unchanged in API/query state and evidence; conversion to JavaScript numbers is limited to chart geometry and display formatting. Stock performance is price return, excluding dividends/reinvestment, rather than total return.

Evidence drawers expose original decimals, formulas, statuses, exact intervals, accessions, original HTTPS source links, selection/review evidence, observation/publication times where known and sanitized snapshots. Market evidence includes source action fields/hashes and returned trading-calendar sessions. Historical coverage stays visible even after a recent retrieval. Dates before the first eligible local observation can correctly return an availability error.

## Design

The interface is styled as an analyst's working papers: ledger ink (`#14233b`) on cool slate paper, ruled sheets instead of floating cards, and accounting red for negative values. Headline figures carry the accounting double underline used for totals. A highlighter (`#ffe36e`) marks the figure you are tracing: hover or focus a metric or statement cell before opening its evidence. All tokens live at the top of `src/styles.css`.

Type is self-hosted from npm with no network font requests: Source Serif 4 (`@fontsource-variable/source-serif-4`, OFL) for headings and figures, with tabular lining numerals; IBM Plex Sans (`@fontsource/ibm-plex-sans`, OFL, Latin 400/500/600) for interface text.

Motion is limited to one load sequence and responses to your actions:

- On load, the double underlines under the headline figures draw in one after another, and each chart wipes in left to right.
- Switching views crossfades the new view in; the active navigation and statement-tab markers grow in.
- The evidence drawer slides in from the right, and the traced value is highlighted.
- Loading placeholders shimmer, and the reload icon spins while reading.

`prefers-reduced-motion: reduce` disables all animation and transitions. Chart animation is CSS-only (`clip-path`); Recharts' own animation stays off, so chart geometry and tests are unaffected.

## Checks

```sh
npm run typecheck
npm test
npm run build
```

Vitest and React Testing Library cover filters/navigation, local query behavior, independent errors, loading, unavailable/zero values, safe source links, original decimals, calendar evidence and financial chart/lifecycle regressions. Canvas chart APIs and ResizeObserver are mocked only in component tests.

Playwright uses the real chart libraries in Chromium. Install its browser once if unavailable (`npx playwright install chromium`), then run:

```sh
npm run test:e2e
DASHBOARD_TEST_LOCAL_API=1 npm run test:e2e
```

The default browser scenarios intercept the API with explicitly labelled invented fixtures. The opt-in scenario requires the existing local AAPL/MSFT evidence and Python API, checks all four views and rejects external browser requests. Test fixtures live under `src/test/` and are never imported by production code. No screenshots or downloaded company data are versioned.

## Library references

Current stable versions were checked against the npm registry on 2026-10-29. Official documentation and installed TypeScript definitions verified the integration:

- [Lightweight Charts v5](https://tradingview.github.io/lightweight-charts/docs): `addSeries(CandlestickSeries)`, `createSeriesMarkers`, separate volume pane, ResizeObserver and chart teardown. The visible TradingView attribution is retained.
- [Recharts](https://recharts.github.io/en-US/api/): exact-date bar/line series, responsive containers and explicit null gaps; no smoothing.
- [TanStack Query](https://tanstack.com/query/latest/docs/framework/react/guides/important-defaults): explicit caching and disabled background refetch.
- [Vite](https://vite.dev/config/server-options) and [Tailwind CSS](https://tailwindcss.com/docs/installation/using-vite): official React/Tailwind plugins, loopback proxy and filesystem boundaries.

The production build is a local frontend artifact. `npm run preview` only serves that artifact and is not the supported API proxy/deployment path; use the documented Vite development server with the local API for this milestone.
