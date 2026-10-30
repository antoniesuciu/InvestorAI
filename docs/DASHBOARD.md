# Interactive Financial Research Dashboard v1

Milestone 4 is implemented and committed locally (`b33cf40`) on explicit user authorization, but **not audited or approved**; its independent audit and manual review are not confirmed complete, and push status is not recorded here. It adds a private, local-first interface to the existing SEC collector, Financial Analysis Engine and saved Alpaca evidence. It does not change the financial engines or collect data. Milestone 3B was committed locally as `f379dd7`; the validation records below describe the 2026-10-29 state, when both milestones were uncommitted.

## Architecture

```text
React / TypeScript / TanStack Query
                |
       same-origin /api (Vite proxy)
                |
     FastAPI: dashboard.py
                |
    DashboardService (read-only adapter)
       /                         \
analyze_company               MarketStore.inspect
       |                         |
SEC Store + original facts    local market snapshots
```

`dashboard_models.py` defines allowlisted Pydantic response contracts. `dashboard_service.py` calls the existing Python engines, selects display ranges, projects evidence and computes comparison price-return series through existing return functions. `dashboard.py` validates requests and enforces the HTTP boundary. No frontend calculation replaces an engine calculation. There is no collector endpoint, provider client, credential loader, write endpoint or background collection task.

The React application has four views: **Overview**, **Financials**, **Statements**, and **Compare**. Lightweight Charts renders daily candles, volume and corporate-action markers; Recharts renders financial histories and comparisons. TanStack Query caches local API responses, disables polling/focus/reconnect refresh and supports an explicit **Reload local data** action. Changing filters requests only local API data. Production has no synthetic fallback.

## Dependencies and installation

Use Python 3.11+ on POSIX (validated on 3.12.3), Node **22.22.2** and npm **10.9.7** (validated). The complete JavaScript toolchain requires `^22.22.2 || ^24.15.0 || >=26.0.0`, including jsdom's requirement. Core collectors/engines still use the standard library. Dashboard and test dependencies are optional:

```sh
# Repository root
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dashboard.lock -e .
npm --prefix frontend ci --ignore-scripts
```

`pyproject.toml` also exposes `.[dashboard,test]` for resolving a new environment. The lockfile records the tested Python environment; `frontend/package-lock.json` records the JavaScript dependency graph. Direct dependencies use exact versions. The checked environment uses FastAPI 0.162.0, Uvicorn 0.54.0, React 19.3.1, TypeScript 7.0.2, Vite 8.3.5, Tailwind 4.3.3, Lightweight Charts 5.2.1, Recharts 3.10.1 and TanStack Query 5.105.0. Dependencies are installed locally; no paid service, cloud runtime or microservice infrastructure is required.

## Start locally

Run from the repository root in two terminals:

```sh
.venv/bin/uvicorn investor_ai.dashboard:app \
  --host 127.0.0.1 --port 8000 --no-proxy-headers --no-access-log
```

```sh
npm --prefix frontend run dev
```

Open **http://127.0.0.1:5173**. Both commands bind loopback. Port 5173 is fixed; an occupied port fails rather than silently changing the permitted origin. Stop each process with Ctrl+C. No SEC identity or Alpaca credentials are needed to browse already saved data, and the API does not read the project's `.env` file.

Defaults are `data/sec` and `data/market`. Server-side `INVESTOR_SEC_DATA_DIR` and `INVESTOR_MARKET_DATA_DIR` can point to other permitted local stores; HTTP clients cannot select paths. Missing stores display an empty watchlist. Missing financial or market data is explicit and does not erase the other dataset. Collection remains a separate, explicitly invoked CLI workflow described in [README](../README.md) and [market data documentation](MARKET_DATA.md).

`npm --prefix frontend run build` typechecks and creates ignored `frontend/dist`. This milestone supplies a local development application, not a deployment/static-file hosting service; Vite preview alone has no API proxy. Do not expose either server to a LAN or the public Internet.

## API

| GET endpoint | Purpose / query parameters |
| --- | --- |
| `/api/health` | Read-only/offline capability status; no credentials or store paths. |
| `/api/companies` | Current local company catalogue, CIK, currency, financial/market availability and coverage dates. |
| `/api/companies/{ticker}/financials` | `frequency=annual\|quarterly`, optional `start`, `end`, `as_of`. Exact intervals, metrics, statuses, source/revision evidence and snapshot freshness. |
| `/api/companies/{ticker}/market` | `adjustment=raw\|split\|dividend` (default split), `feed=sip`, optional `start`, `end`, `as_of`, `adjustment_as_of`. Prices, sessions, actions, quality, source identity, return and snapshot evidence. |
| `/api/compare` | `tickers=AAPL,MSFT` (two to four distinct local symbols), `frequency`, optional `start`, `end`, `as_of`. Own-period financials and common-session Python price-return series. |
| `/openapi.json` | Local machine-readable typed API contract; interactive CDN-backed docs are disabled. |

`start`/`end` are inclusive ISO dates. Financial ranges filter **reporting-period ends** after full-snapshot analysis, preserving prior-year inputs needed for growth. Market ranges filter actual saved sessions, not interpolated calendar dates. `as_of` accepts a timezone-aware ISO timestamp or a date interpreted as UTC end-of-day. No eligible snapshot means unavailable, not a modern snapshot backdated by its reporting/bar dates. Adjustment-vintage ambiguity also remains unavailable until an explicit saved anchor is selected through the API; the first UI deliberately does not select ambiguous vintages automatically.

Errors use `{ "detail": { "code": "...", "message": "..." } }`: 404 unknown company, 409 unavailable/ambiguous eligible evidence, 422 invalid parameters, 503 unreadable/corrupt local data. Raw exceptions, invalid input values and filesystem paths are not returned. Non-read methods are rejected. There is no arbitrary URL, SQL, file, shell or configuration endpoint.

## Financial interpretation

- **Precision:** All monetary values, ratios and returns cross the API as exact decimal strings or `null`. The Python engines retain their Decimal arithmetic. React converts values only for chart geometry and rounded display; evidence retains the exact original decimal. No ratios, growth rates, cumulative cash-flow subtraction, price returns or currency conversions are calculated in React.
- **Units:** Engine percentage metrics already use percentage points (15 means 15%); price-return fractions use 0.15 for 15%. Missing, conflicting, ambiguous or invalid results are N/A with their original statuses and flags, never zero. Zero-valued observations remain zero.
- **Currency selection:** A single evidenced three-letter monetary unit is retained, including non-USD units. Unknown or multiple reporting currencies produce an explicit availability error; the adapter never silently falls back to USD. No currency-conversion facility is added.
- **Reporting basis:** Annual and standalone quarterly metrics reuse the existing evidence-based fiscal/cohort/revision selection. Cross-filing cash-flow derivation still requires reviewed original-source comparability. UI filters do not relax those rules. Statement tables are the existing focused US-GAAP concept set, not complete standardized financial statements.
- **Evidence:** Every metric can open a keyboard-accessible evidence drawer showing formulas, status, exact period, input concepts, units, filing accession/date, acceptance time when available, retrieval/analysis availability, SEC links, selection evidence and accounting reviews. Original-document hashes are retained; private local paths and response headers are removed from API projections.
- **Prices:** Raw, provider split-adjusted and dividend-adjusted bars are separate series. Return calculation on split-adjusted bars requires compatible acquisition vintages. A raw return requires the existing scoped complete-action evidence; incomplete real Alpaca ledgers remain unavailable. Dividend-adjusted bars are viewable, but no price-return/total-return result is inferred from them. Price return excludes dividends, reinvestment, fees and tax. Provider rounding and adjustment limitations remain in force.
- **Comparisons:** Financial charts use each company's exact reporting dates and display their period differences; they do not merge fiscal-year labels. Mixed-unit monetary plots are suppressed instead of assuming an exchange rate. Each company's metrics retain its own currency. Comparative price returns use only shared observed sessions and a common initial session. No shared two-session window means unavailable returns for every company. Baseline zero is explicitly identified; missing values break lines. Lines connect observations for display and are not interpolated financial observations.
- **Freshness:** Historical period/session date, source publication/acceptance, local observation/retrieval and snapshot completion are separate fields. Source publication is unknown when absent. The interface flags retrieval older than 24 hours or market coverage older than seven calendar days as stale; these are display heuristics, not feed guarantees or trading-calendar predictions. Failed attempts are shown separately without replacing last-successful timestamps. Operational status files retain only their latest marker; older attempt histories are not reconstructed for a historical cutoff.
- **Universe:** Selection uses the current locally saved ticker catalogue and observed market instrument UUIDs. Ambiguous SEC ticker aliases fail closed. Neither historical security-master membership, delisted coverage nor historical ticker ownership is established. The dashboard does not certify pre-collection decision-time history.

## Local security and offline behavior

The API accepts loopback clients, trusted localhost/loopback Host headers, and only the exact local Vite origins. CORS allows GET/OPTIONS with no credential support. Other origins, remote clients and mutation methods are rejected. Responses are `no-store` and use `nosniff`/no-referrer headers. Uvicorn commands disable forwarded-header trust and access logging.

Vite binds loopback, proxies only `/api`, does not load a root environment file, exposes only explicitly prefixed public variables and restricts filesystem serving to `frontend`. Secret/environment files, `.git`, local datasets, credentials and agent configuration are denied. There are no external fonts, scripts, images or automatic provider calls. TradingView chart attribution is retained. Browser/source links open only on user action.

These boundaries support a single-user local workstation, not multi-user authentication or Internet deployment. The workstation, local stores and installed dependencies remain trusted. Source links require credential-free HTTPS and open only on user action; they are not fetched by the dashboard. A deployed service would require a separate security and licensing review.

## Tests and validation

```sh
.venv/bin/python -m pytest -q
npm --prefix frontend test
npm --prefix frontend run build

# Optional one-time browser download, into ignored local artifacts
PLAYWRIGHT_BROWSERS_PATH="$PWD/artifacts/playwright-browsers" \
  npm --prefix frontend exec -- playwright install chromium

# Start the API first; Playwright starts/reuses the Vite server.
cd frontend
PLAYWRIGHT_BROWSERS_PATH="$PWD/../artifacts/playwright-browsers" \
  DASHBOARD_TEST_LOCAL_API=1 npm run test:e2e
```

Backend integration tests use fresh, explicitly synthetic SEC/market stores and prohibit collectors, outgoing connections and credential-file reads. They exercise values above JavaScript's safe integer limit, hand-calculated formulas, revisions/cutoffs, source timestamps, raw/split/dividend handling, incomplete action coverage, missing sessions, fiscal/currency differences, partial comparisons, safe errors and local access controls. Frontend fixtures are explicitly labeled synthetic and are excluded from production imports. RTL/Vitest covers formatting, loading/error/missing states, navigation, selection, dates, annual/quarterly views, evidence and chart bindings. Playwright exercises actual chart libraries, evidence keyboard behavior, mobile layout and real local AAPL/MSFT workflows.

### Executed validation — 2026-10-29

| Check | Observed result |
| --- | --- |
| Complete Python suite | **298 passed**, plus **139 subtests**. Includes the 239 pre-dashboard regressions and 59 new API integration cases. |
| Frontend Vitest / React Testing Library | **16 passed** across three test files. |
| TypeScript / production build | Passed, with separate React, financial-chart and price-chart chunks. |
| Chromium Playwright | **4 passed**: three explicitly synthetic fixture workflows and one real-local AAPL/MSFT workflow covering all four views. No external browser requests or page errors in the real-local test. |
| Real browser smoke / visual inspection | Actual candles, financial charts, statements and comparison rendered at 1440px; 390px layout had no document overflow. Evidence opening, Escape/focus return, company/metric/date/frequency filters and error states exercised. Screenshots are ignored under `artifacts/dashboard-*.png`. |
| Original SEC verifier | Passed: 52 original-filing inputs, eight comparative controls and 48 independent formula checks. |
| Original SEC review verifier | Passed: 16 cumulative inputs, 18 derived results, four direct-quarter controls and both offline adapters. Reviewed source registry and validation-record hashes unchanged. |
| Real API / engine equality | **4,450 metric projections** matched existing Python outputs, including value/unit/status/formula/flags, over AAPL 19 annual/68 quarterly and MSFT 19 annual/72 quarterly periods. Values remained decimal strings. |
| Independent real price-return control | AAPL/MSFT over the three shared 2020-08-28 through 2020-09-01 sessions matched independent `Fraction` arithmetic within `1e-32`, including the Apple split window. Raw incomplete-action and dividend-adjusted unsupported returns remained unavailable. |
| Offline / data integrity | API started with outbound DNS/connections denied; **zero outbound attempts** recorded. All **125 existing local data files** remained byte-for-byte unchanged. |
| Credential / access checks | Configured credential values absent from eligible source, built bundles, API projections/responses and validation logs. Host/origin/write/cutoff controls and private-file denial exercised. No data, secrets, dependency directories or generated files tracked. |
| Recovery / scope | Checkpoint archive and every included file hash verified; all pre-dashboard source and tests unchanged. HEAD remains `85c8575`; index empty; no commit/push. |

Independent implementation/testing/review agents had separate file ownership. Confirmed findings were corrected with regressions: incomparable return windows, ambiguous ticker/currency selection, mixed-unit chart labels, dropped zero baseline, empty-range chart recovery and failed-refresh visibility. Mobile overflow and the Metric selector's explicit accessible label were corrected after browser failures. Existing financial engines were preserved.

The Python suite emits one upstream Starlette warning that its current httpx-based TestClient transport is deprecated; it does not fail tests or affect the dashboard runtime. Sandbox TestClient startup hung while identical host execution passed; validation used approved host execution, without changing application networking to bypass a restriction. This is Chromium validation, not a claim of cross-browser/device or production-deployment certification.

The original SEC filing verifiers use their existing ignored source controls; writes were suppressed during reruns to preserve existing reviewed-evidence timestamps. Browser downloads, screenshots, traces, builds, environments, caches and datasets remain ignored. The generated offline/security hash records are in `artifacts/dashboard-*-validation.json`; they are local validation artifacts, not versioned datasets or implementation dependencies.

## Recoverable pre-dashboard checkpoint

Before any Milestone 4 edits, the complete eligible working tree (including uncommitted Milestone 3B) was archived to ignored `artifacts/checkpoints/milestone-3b-20261029T162009Z.tar.gz`. SHA-256:

```text
4067d1a274095e4459b7263a9d6d32a0d121419b7b516b342f60ffdf596021bf
```

Its `CHECKPOINT.json` records the HEAD and every included file's hash; `WORKTREE.patch` and `GIT_STATUS.txt` preserve the pre-dashboard diff/status. Every archived file was hash-verified. It excludes credentials, datasets, caches and Git internals. To inspect/recover, verify the archive hash and extract into a **separate empty directory**; compare/copy only intended files. Do not extract over current work or reset Git. This is a local recovery checkpoint, not a new commit or substitute for reviewed version control.

Milestone 4 additions are the three `dashboard*.py` modules, `tests/test_dashboard.py`, `frontend/`, `requirements-dashboard.lock`, this document, optional dependency/ignore entries and documentation updates. Pre-existing Milestone 3B source/test changes remain distinguishable against this checkpoint. No existing collector, calculation or storage module is rewritten.

## Remaining scope

No AI agents, forecasts, valuation/share-price ratios, portfolio, trading, news, watchlist editing, background scheduler, new provider or live refresh UI is implemented. Retention and account/feed conditions in [ALPACA_ACCESS.md](ALPACA_ACCESS.md), incomplete corporate actions, unknown dividend cash currency, adjustment-vintage and SEC comparability limitations remain unchanged. Local snapshots may be old, incomplete or absent; the UI displays those conditions.

Future interfaces can reuse the typed read-only API or Python adapter. New collection controls, valuation models, agents, deployment/authentication or scheduled jobs need separately authorized scope and tests. The recommended next step is the requested independent repository audit followed by manual review; Milestone 4 was committed (`b33cf40`) before these, and they are not confirmed complete.
