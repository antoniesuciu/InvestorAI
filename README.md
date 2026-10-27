# Investor AI

A private, personal investment research platform, developed incrementally using completely free data. **Phase 2 Milestone 1 — the local SEC EDGAR collector — is complete and approved.** It collects, stores and exposes company financial observations with filing provenance. See [REQUIREMENTS.md](REQUIREMENTS.md) for scope and [Phase 1 research](docs/DATA_SOURCES.md) for source evaluations. The limitations below remain applicable; later platform capabilities are future work.

## Setup

Use Python 3.11+ on Linux or macOS. Runtime and tests use only the Python standard library; no API key, account, payment details, database server, or dependency installation is required. The store lock currently uses POSIX `fcntl`.

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

## Financial interpretation and provenance

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

`SecClient`, `Collector`, `Store` and `extract_facts` separate HTTP policy, watchlist orchestration, persistence and interpretation. Future agents and a web backend can call these interfaces without invoking the CLI. No frontend framework or web transport has been selected.

The client defaults to two requests per second, a 30-second timeout, and at most three retries for transient network/HTTP errors. It honors bounded `Retry-After`, stops on longer cooldowns, rejects redirects and non-SEC endpoints, and reports 403 without trying to bypass it. A shared client coordinates its requests. A same-store lock prevents overlapping scheduled/CLI collections; separate stores or independent external SEC tools do not share that budget. Keep total SEC traffic within the SEC's current limit (10 requests/second when checked).

A local scheduler can invoke the same `collect` command with a configured contact, store and working directory. The cache limits repeated downloads; use an appropriate filings-monitoring cadence rather than frequent forced refreshes. No schedule or background service is installed. `companies` reports data age and refresh status for companies with a successful snapshot.

## Verification

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Tests use invented local fixtures only. They exercise fiscal/calendar distinctions, 52/53-week periods, cumulative cash flows, revisions, concepts/currencies, zero versus missing values, precise JSON decoding, ticker/CIK validation, raw replay, snapshots, failed refreshes, cache integrity, overlap prevention, CLI configuration/filtering, request identity, rate limits and bounded retries.

On 2026-10-27, all 52 offline tests passed on Python 3.12.3, including User-Agent validation, comparative fiscal evidence, quarter/cumulative distinctions, alias resolution, metadata evolution and process-interruption recovery. See [the independent review response](docs/SEC_COLLECTOR_REVIEW_RESPONSE.md) for every Gemini finding, confirmed fixes and deferred work. The final fiscal parser is version 3; old snapshots remain available.

The earlier HTTP 403 was traced to that identity: the response was an SEC/Akamai HTML page titled "Your Request Originates from an Undeclared Automated Tool." Against the identical AAPL submissions URL, host curl returned HTTP 200 with the user's supplied email-form identity; changing only the Python client's User-Agent to that identity also returned HTTP 200. Python's existing `Accept: application/json` and `Accept-Encoding: identity` headers worked unchanged. No proxy or custom certificate configuration was present in the host process. The separate sandbox launcher failed before executing even a network-free command (`bwrap: loopback: Failed RTM_NEWADDR`); escalated host curl and Python requests succeeded, so that launcher issue did not explain the host HTTP 403. No proxy substitution, browser impersonation, access-control bypass or transport replacement was used.

Live validation subsequently succeeded using the user's configured real contact identity: AAPL saved 1,433 observations and MSFT saved 1,625, with all seven selected metrics present. All 28 sampled values matched their original annual/quarterly SEC filings. Raw replay reproduced both snapshots exactly, and cached collection plus CLI inspection made zero network requests with DNS/connections explicitly forbidden. See [the validation record](docs/SEC_COLLECTOR_VALIDATION.md) for source filings, amounts, period/revision checks and remaining limitations. The local `.env` handoff was read only by the one-off validation wrapper; the application still expects `SEC_USER_AGENT` in its environment or `--user-agent`.

Official references checked on 2026-10-27: [SEC EDGAR APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces) and [SEC developer access policy](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data). No paid service or alternate data provider is integrated. Dashboard, AI analysis, forecasts and trading remain future work.
