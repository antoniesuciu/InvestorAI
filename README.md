# Investor AI

A private, personal investment research platform, developed incrementally using completely free data. **Phase 2 Milestone 1 — the local SEC EDGAR collector — is complete and approved. Milestone 2 — the offline Financial Analysis Engine — is complete and approved on 2026-10-28.** Both preserve filing provenance and reporting periods. See [REQUIREMENTS.md](REQUIREMENTS.md) for scope and [Phase 1 research](docs/DATA_SOURCES.md) for source evaluations. The limitations below remain applicable; later platform capabilities are future work.

**Milestone 3A is approved; Milestone 3B — Live Alpaca Market Data Integration — and Milestone 4 — Interactive Financial Research Dashboard v1 — are implemented for review and remain uncommitted.** The user authorized historical SIP collection and private local persistence subject to provider terms. Account/feed wording and indefinite retention remain unverified conditions, not claims of contractual clearance. See [access research](docs/ALPACA_ACCESS.md), [live validation](docs/ALPACA_LIVE_VALIDATION.md) and [dashboard documentation](docs/DASHBOARD.md). No orders, paid upgrade, forecast, valuation, AI or portfolio functionality is included.

## Local research dashboard

Explore saved AAPL/MSFT data through Overview, Financials, Statements and Compare. Annual/quarterly charts, daily stock candles, corporate actions, source evidence and freshness use the existing Python calculations and local stores. No dashboard interaction collects data or needs API credentials. Missing data remains unavailable; there is no synthetic production fallback.

Use Python 3.11+ and Node 22.22.2 (the complete toolchain supports `^22.22.2 || ^24.15.0 || >=26.0.0`); from the repository root:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dashboard.lock -e .
npm --prefix frontend ci --ignore-scripts

# Terminal 1
.venv/bin/uvicorn investor_ai.dashboard:app \
  --host 127.0.0.1 --port 8000 --no-proxy-headers --no-access-log

# Terminal 2
npm --prefix frontend run dev
```

Open **http://127.0.0.1:5173**. Both servers bind loopback. The UI reads `data/sec` and `data/market`; empty stores produce an empty watchlist. Collect permitted data separately using the CLI below. Financial decimals, exact fiscal periods, statuses, filing provenance and observation cutoffs remain intact. Price returns exclude dividends and are not total returns; saved prices may be historical or incomplete. [DASHBOARD.md](docs/DASHBOARD.md) describes the typed read-only API, local security, tests, checkpoint and remaining limitations.

## Setup

Use Python 3.11+ on Linux or macOS. Core collectors, financial/market engines and their original tests use only the Python standard library. The dashboard/API and new API tests use the optional dependencies above. SEC collection needs a real contact identity; Alpaca collection requires configured free-account credentials. No database server is required. The store lock currently uses POSIX `fcntl`.

Run from the repository root:

```sh
export PYTHONPATH="$PWD/src"
export SEC_USER_AGENT='InvestorAI/0.1 Your Name your-real-contact@example.com'
python3 -m investor_ai collect
python3 -m investor_ai companies
python3 -m investor_ai inspect AAPL --metric revenue --period annual
python3 -m investor_ai inspect MSFT --metric operating_cash_flow --period year_to_date --json
```

Replace the contact placeholder with your real contact identity. An identifiable application name followed by a real contact email is required, following the SEC's declared-agent example. A URL-only identity is rejected locally. Set the identity in your shell; the program does not load `.env` files. Do not commit credentials or personal local configuration. An editable installation (`python3 -m pip install -e .`) optionally provides the equivalent `investor-ai` command; it uses setuptools as its build tool.

`watchlist.toml` initially selects AAPL and MSFT, a 24-hour cache and `data/sec` storage. Explicit CIK mappings are checked against official submissions metadata. Other tickers resolve through the SEC's company ticker directory; common dot/dash share-class aliases such as `BRK.B`/`BRK-B` are recognized, while conflicting/ambiguous associations require an explicit CIK. Quote dotted override keys in TOML (`"BRK.B" = "0001067983"`). Ticker mappings represent current identities, not historical ticker ownership.

```sh
python3 -m investor_ai collect AAPL CIK0000789019 --refresh
python3 -m investor_ai collect AAPL --filing-history
python3 -m investor_ai --config watchlist.toml --data-dir data/sec inspect 320193 --json --limit 0
python3 -m investor_ai inspect AAPL --snapshot SNAPSHOT_ID --json
```

Global `--config` and `--data-dir` options precede the subcommand. Collection is bounded to 20 identifiers per run. JSON collection results report `success`, `partial` (selected metrics missing), `cached` (with any missing metrics), or `failed` per company. Any failed company produces exit status 1; partial collections remain usable and return 0. Missing metrics are absent, never filled with zero. Metric availability is across the snapshot, not a completeness guarantee for every period.

## Offline financial analysis

```sh
python3 -m investor_ai analyze AAPL --frequency annual --limit 2
python3 -m investor_ai trends MSFT --frequency quarterly --limit 8
python3 -m investor_ai analyze AAPL --as-of 2026-10-27 --json --limit 0
```

Analysis needs saved snapshots, not a User-Agent or network connection. It calculates YoY revenue/net-income growth, three profit margins, FCF, liabilities/assets, current ratio, cash/current liabilities and noncurrent debt/equity. The last metric is a debt subset, not total debt/equity. Missing/negative-base/ambiguous results have explicit statuses; text cells include underlying reasons and diagnostic flags. `--json` includes formulas and all source inputs. `--concept METRIC=CONCEPT` can resolve a documented accounting-concept ambiguity.

Historical cutoffs use **known observation times**: snapshots and inputs collected after the cutoff are excluded. Dates before the first saved snapshot are refused; today's aggregate facts are not retroactively certified history. Calculations reconcile latest known values to a shared filing basis; unchanged comparatives and partial amendments preserve usable history. Annual balances prioritize 10-K evidence. Cross-filing cash-flow conversion requires exact reviewed original-filing evidence, aligned periods and reconciled revisions; unsupported results remain unavailable. The optional ignored `data/sec/analysis_evidence.json` registry (or `--evidence PATH`) is verified locally, including source-document hashes and review availability. See [FINANCIAL_ANALYSIS.md](docs/FINANCIAL_ANALYSIS.md) for the evidence schema, formulas and limitations, and [the analysis review response](docs/FINANCIAL_ANALYSIS_REVIEW_RESPONSE.md) for real Q2/Q3/Q4 validation.

## Financial interpretation and provenance

For market-data architecture, usage, adjustment/provenance rules and limits, see [MARKET_DATA.md](docs/MARKET_DATA.md). Its independently stored synthetic evidence does not change the SEC interpretation below.

- Selected entity-wide US-GAAP concepts cover revenue, net income, operating cash flow, capital expenditure, assets, liabilities and cash where present. Alternative concepts and currencies remain separate observations. They are not interchangeable accounting measures. Company-specific tags, dimensional/segment facts and IFRS mapping are outside this collector.
- Every observation retains the original concept, unit, value, start/end, accession, form, filing date, source fiscal focus and frame, plus available submissions acceptance/report dates, source and filing links, quality flags and retrieval time. Values are decimal strings, without binary floating-point conversion. Raw SEC bytes remain available for exact evidence.
- `fy`/`fp` describe the filing's fiscal focus; they do not reliably label older comparative observations. Fiscal labels use exact 10-K/10-Q report-period matches (including amendments), or unambiguous evidence from another observation with the exact same interval/type and a filing date no later than this observation. `fiscal_label_basis` and `fiscal_label_sources` preserve that reasoning. Unsupported/conflicting labels remain unknown; dates are never rounded to align them. The SEC `frame` is preserved without treating its calendar quarter as a fiscal quarter.
- Duration categories are explicitly inferred: annual 330–400 days, quarterly 70–110, cumulative/year-to-date 150–300; other durations remain `other`. These ranges accommodate ordinary 52/53-week calendars but cannot establish the meaning of transition/stub periods. Check start/end, filing context and quality flags before analysis. Balance-sheet facts are instants.
- Six- and nine-month cash flows remain cumulative: their `fiscal_quarter` is null, while `fiscal_period` retains reporting context such as `Q3`. Annual balance instants retain `fiscal_period="FY"` with no implied interim Q4. `is_year_to_date` and its basis identify a known Q1 quarter as both quarterly and cumulative, as well as longer inferred cumulative/full-year intervals; unresolved short-period contexts remain unknown. This metadata does not derive missing quarters or certify stub calendars. The collector does not subtract different filing versions, infer capex signs, select a preferred revenue concept, or aggregate competing facts. Revisions retain their accession/version; only identical records are deduplicated within a snapshot.
- Recent submissions metadata is fetched by default. Older accessions may lack acceptance dates and fiscal labels, with an explicit quality flag. `--filing-history` also fetches the SEC-listed older submissions files (bounded to 20 per company). If a filer exceeds this bound, omit the option to collect without older metadata; windowing is not implemented. Loading those files enriches metadata; it cannot restore facts missing from today's CompanyFacts aggregate.
- `--filed-through YYYY-MM-DD` filters filing dates only. It is **not** a certified point-in-time dataset: today's aggregates may omit earlier evidence, filing dates have day precision, and acceptance timestamps do not establish first public dissemination. `public_available_at` stays unknown. Local snapshots preserve evidence from our collection time onward.

## Local storage and refreshes

`data/sec/` is excluded by `.gitignore`. Keep custom stores in an ignored directory too.

```text
data/sec/
  raw/<sha256>.json                          # Exact responses, deduplicated by content
  resources/company_tickers.json            # Optional directory cache reference
  companies/<CIK>/snapshots/<id>/manifest.json
  companies/<CIK>/snapshots/<id>/facts.json   # Selected observations, all source versions
  companies/<CIK>/latest.json                # Published only after a complete write
  companies/<CIK>/status.json                # Attempt/failure state separate from data age
```

Manifests record response URLs, retrieval times, hashes, parser/collector/schema versions, fiscal year end, record counts and missing metrics. Successful refreshes create new snapshots and retain old ones; unchanged raw content is reused. Raw bytes and JSON are written to temporary siblings before atomic replacement, so interrupted writes do not publish partial final objects. A killed process may leave unused temporaries or unpublished snapshots; they are not treated as successful collections and do not block retry. Existing corrupt raw objects still fail checksum validation and require investigation, rather than silent overwrite. This does not guarantee durability against hardware power loss. SHA-256 checks detect accidental changes to raw responses and normalized records. Cache hits keep the original retrieval time. Failed refreshes preserve the last successful snapshot and its age; status retains the most recent failure even after a later cache hit.

For offline reproduction, use `Store.snapshot(cik, snapshot_id)`, load its source references through `Store.raw`, decode with `sec.decode_json`, and call `financials.extract_facts` with the saved submissions, optional history, source URL and original retrieval time. `Store.records` retrieves checksum-verified saved results. Parser changes require reprocessing saved evidence explicitly or a refresh; a cache entry from another parser version is not reused. The synthetic replay test demonstrates this without network access.

`SecClient`, `Collector`, `Store` and `extract_facts` separate HTTP policy, watchlist orchestration, persistence and interpretation. The read-only dashboard reuses `analyze_company` and `MarketStore.inspect` through a thin FastAPI adapter; collectors remain separate. Future agents can use the same Python interfaces or typed API contracts.

The client defaults to two requests per second, a 30-second timeout, and at most three retries for transient network/HTTP errors. It honors bounded `Retry-After`, stops on longer cooldowns, rejects redirects and non-SEC endpoints, and reports 403 without trying to bypass it. A shared client coordinates its requests. A same-store lock prevents overlapping scheduled/CLI collections; separate stores or independent external SEC tools do not share that budget. Keep total SEC traffic within the SEC's current limit (10 requests/second when checked).

A local scheduler can invoke the same `collect` command with a configured contact, store and working directory. The cache limits repeated downloads; use an appropriate filings-monitoring cadence rather than frequent forced refreshes. No schedule or background service is installed. `companies` reports data age and refresh status for companies with a successful snapshot.

## Verification

Install the optional dashboard environment above before running the complete suite, including the new API tests:

```sh
.venv/bin/python -m pytest -q
npm --prefix frontend test
npm --prefix frontend run build
```

Browser workflows, optional real-local AAPL/MSFT validation and original-filing verifier evidence are in [DASHBOARD.md](docs/DASHBOARD.md). The existing unittest regressions run as part of pytest; API tests require the optional web/test dependencies.

**Milestone 4 validation, 2026-10-29:** 298 Python tests plus 139 subtests, 16 frontend tests, four Chromium workflows, the TypeScript/production build and both original SEC filing verifiers passed. Real API outputs matched 4,450 engine metrics; independent real price-return controls passed. The guarded API made zero outbound attempts and all 125 existing data files remained unchanged. Configured credentials were absent from reviewed source/build/API output. Milestones 3B and 4 remain uncommitted for the requested independent audit and manual review.

Automated unit/API tests use invented local fixtures only. They exercise fiscal/calendar distinctions, 52/53-week periods, cumulative cash flows, revisions, concepts/currencies, zero versus missing values, precise JSON decoding, ticker/CIK validation, raw replay, snapshots, failed refreshes, cache integrity, overlap prevention, CLI configuration/filtering, request identity, rate limits and bounded retries. The separately opted-in browser workflow and ignored original-filing verifiers use existing real local evidence.

On 2026-10-28, all 122 offline tests passed on Python 3.12.3: 52 collector regressions and 70 analysis tests. Coverage includes User-Agent validation, fiscal evidence, cross-filing Q2/Q3/Q4 safeguards, aliases, metadata evolution, interruption recovery, financial formulas, partial amendments, restatements, source integrity, intraday cutoffs, distressed balances and CLI diagnostics. See [the collector review response](docs/SEC_COLLECTOR_REVIEW_RESPONSE.md), [the analysis documentation](docs/FINANCIAL_ANALYSIS.md) and [the complete Milestone 2 review reconciliation](docs/FINANCIAL_ANALYSIS_REVIEW_RESPONSE.md) for validation and outstanding limits. The collector fiscal parser remains version 3; old snapshots remain available.

The earlier HTTP 403 was traced to that identity: the response was an SEC/Akamai HTML page titled "Your Request Originates from an Undeclared Automated Tool." Against the identical AAPL submissions URL, host curl returned HTTP 200 with the user's supplied email-form identity; changing only the Python client's User-Agent to that identity also returned HTTP 200. Python's existing `Accept: application/json` and `Accept-Encoding: identity` headers worked unchanged. No proxy or custom certificate configuration was present in the host process. The separate sandbox launcher failed before executing even a network-free command (`bwrap: loopback: Failed RTM_NEWADDR`); escalated host curl and Python requests succeeded, so that launcher issue did not explain the host HTTP 403. No proxy substitution, browser impersonation, access-control bypass or transport replacement was used.

Live validation subsequently succeeded using the user's configured real contact identity: AAPL saved 1,433 observations and MSFT saved 1,625, with all seven selected metrics present. All 28 sampled values matched their original annual/quarterly SEC filings. Raw replay reproduced both snapshots exactly, and cached collection plus CLI inspection made zero network requests with DNS/connections explicitly forbidden. See [the validation record](docs/SEC_COLLECTOR_VALIDATION.md) for source filings, amounts, period/revision checks and remaining limitations. The local `.env` handoff was read only by the one-off validation wrapper; the application still expects `SEC_USER_AGENT` in its environment or `--user-agent`.

Official references checked on 2026-10-27: [SEC EDGAR APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces) and [SEC developer access policy](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data). SEC collection remains primary-source-only. The separately authorized Alpaca adapter and read-only dashboard are described below/above; AI analysis, forecasts, valuation and trading remain future work.

## Historical market data

Configure `ALPACA_API_KEY_ID`, `ALPACA_SECRET_KEY` and optional `ALPACA_PAPER=true` in ignored `.env`. Credentials are parsed as literals, never executed as shell code or placed in URLs/provenance. Market storage defaults to ignored `data/market`, separate from SEC storage.

```sh
export PYTHONPATH="$PWD/src"
python3 -m investor_ai market collect --provider alpaca AAPL MSFT \
  --start 2016-01-04 --end 2025-01-10 --feed sip --page-size 500
python3 -m investor_ai market collect --provider alpaca AAPL MSFT \
  --start 2016-01-04 --end 2025-01-10 --feed sip --adjustment split --page-size 500
python3 -m investor_ai market collect --provider alpaca AAPL MSFT \
  --start 2016-01-04 --end 2025-01-10 --feed sip --adjustment dividend --page-size 500
python3 -m investor_ai market inspect AAPL --provider alpaca --feed sip --json --limit 0
python3 -m investor_ai market replay AAPL
python3 -m investor_ai market returns AAPL --start 2020-08-28 --end 2020-08-31
```

Use explicit completed historical dates. One read-only client shares a bounded request/retry budget and conservative two-request/second limiter across the watchlist. Routes are restricted to market bars/actions and paper-host read-only asset/calendar metadata; there is no order or live-trading interface. No paid/recent SIP, OTC/overnight feed or automatic provider/feed fallback is enabled. `--max-requests` bounds the invocation; pagination continues even after short pages.

Raw updates recheck a seven-calendar-day price overlap. `--refresh` rechecks the full supplied price range and historical action process range; action process polling has independent state and a seven-day correction overlap. Corrections beyond that overlap need periodic full reconciliation. Adjusted updates refresh the entire stored/requested history and preserve acquisition vintages; omitted old adjusted bars abort publication. `--cache-hours 0` forces retrieval. Separate processes/stores do not share an account-wide quota service; coordinate other account traffic too.

Raw, split-adjusted and dividend-adjusted series remain separate. Adjusted anchors refer to local acquisition day, not historical provider knowledge; multiple days require `--adjustment-as-of YYYY-MM-DD` when inspecting/replaying/returning an ambiguous adjusted series. Source bars may be rounded; do not imply exact economic equivalence across representations. `returns` defaults to compatible split-adjusted prices and excludes dividends. Raw returns require complete scoped action evidence and remain unavailable when it is insufficient. No dividend reinvestment or total-return model is provided.

Inspection, returns and raw-provider replay work offline without credentials. `--as-of` requires a completed local snapshot and eligible observations; current adjusted history is never represented as known before collection. Original response bodies, SHA-256 hashes, source requests, dates/units, revisions and diagnostics remain available locally. Current asset UUIDs do not reconstruct historical symbol changes or listing life. Missing sessions are reported, never filled. Unknown cash currency/payment dates stay unknown; unsupported actions retain original evidence and diagnostics without manufacturing completeness.

**Live validation:** six current series contain 2,270 daily bars each for AAPL/MSFT from 2016-01-04 through 2025-01-10, with no missing sessions against the returned calendar. Pagination, raw incremental extension, adjusted refresh, six current and six prior offline replays, cache identity and independent financial controls pass. **239 tests and both original SEC filing verifiers pass.** See [the report](docs/ALPACA_LIVE_VALIDATION.md) for rounding, data-quality and contract limitations. Changes remain uncommitted for review.

## Offline fixtures and temporary diagnostics

```sh
python3 -m investor_ai market collect --source tests/fixtures/market_data.json
python3 -m investor_ai market collect --provider alpaca-replay --source tests/fixtures/alpaca_replay.json
python3 -m investor_ai market inspect SYNTH --provider alpaca-replay --json
python3 -m investor_ai market probe
```

The fixture sources remain explicitly synthetic and never import downloaded real prices. The optional probe still performs bounded, memory-only AAPL/MSFT access checks; `--feed sip` is explicit and not permission evidence. Earlier [offline replay](docs/ALPACA_REPLAY_VALIDATION.md) and [temporary access](docs/ALPACA_ACCESS_VALIDATION.md) reports preserve the historical development stages. [MARKET_DATA.md](docs/MARKET_DATA.md) describes current interfaces and storage.
