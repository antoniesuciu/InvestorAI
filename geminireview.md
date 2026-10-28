Technical and Financial Correctness Review: Milestone 3 Offline Market-Data Infrastructure
Review Date: 2026-10-28
Scope: Offline Market-Data Foundation for InvestorAI Phase 2 Milestone 3
Authority Documents: AGENTS.md, REQUIREMENTS.md, MARKET_DATA.md, MARKET_DATA_PROVIDER_DECISION.md
Implementation Files:

• market_data.py
• market_store.py
• market_cli.py
• storage.py
• test_market_data.py
• test_market_store.py
• market_data.json
──────

## Executive Summary

The offline foundation implemented for Milestone 3 delivers an exceptionally disciplined, audit-grade baseline for daily OHLCV and corporate actions. It adheres strictly to the offline
fallback mandate authorized in REQUIREMENTS.md:83-92:
• Mathematical rigor: Split-adjusted raw price returns are calculated using arbitrary-precision Decimals (prec=34), rigorously validated endpoint compatibility, and correct treatment of
stock splits while strictly excluding dividends.
• Lookahead & Point-in-Time (PIT) integrity: Bar publication and observation times must strictly succeed session close; snapshots are content-addressed and immutable; future revisions
cannot leak into historical cutoff views.
• Data boundaries & licensing: No unauthorized network calls or paid API fallbacks exist. Live provider integration is blocked, and licensing limitations are documented transparently.
However, the review surfaces one material operational limitation in incremental updating, several subtle adapter/schema incompatibilities that will need resolution before an Alpaca adapter
can function, and one domain-coupling defect (SecError leaking into market store concurrency).
──────

## Findings by Severity and Category

ID │ Category │ Sever… │ Description │ Reference
──────┼──────────────────────────────────────────────────┼────────┼─────────────────────────────────────────────────────────────────────────────────────┼───────────────────────────────────
F-01 │ Intentional Conservative Decision / Major │ High │ Incremental collection permanently disables actions_complete for the historical │ collect_market,
│ Operational Limitation │ │ series, blocking split price return calculations over historical ranges unless full │ MarketStore.inspect
│ │ │ --refresh is run. │
F-02 │ Confirmed Implementation Defect │ Medium │ Concurrency lock failure in collect_market raises SEC-specific SecError instead of │ storage.py:47,
│ │ │ MarketDataError, leaking SEC collector abstractions into the market module. │ market_store.py:238
F-03 │ Potential Risk / Synthetic Fixture Defect │ Medium │ Synthetic fixture contains a bar with zero volume but non-zero price fluctuation │ market_data.json:39,
│ │ │ ($50–$54), which is physically impossible in exchange trade data. Normalizer does │ normalize_bundle
│ │ │ not check if volume == 0: high == low. │
F-04 │ Potential Risk / Missing Validation │ Low │ Normalizer parses payment_date on corporate actions but fails to enforce │ normalize_bundle
│ │ │ payment_date >= ex_date, permitting retroactive payment dates. │
F-05 │ Intentional Conservative Decision / Research │ Medium │ Historical knowledge cutoff semantics (as_of) prevent retrospective backtesting │ MarketStore.inspect
│ Limitation │ │ over bulk-ingested historical data. │
F-06 │ Future Adapter Limitation (Unverifiable without │ Medium │ Alpaca API schema mismatches: separate bar/action/calendar endpoints vs. unified │ JsonFileSource, normalize_bundle
│ Live Access) │ │ paginated bundle; corporate action taxonomy (stock_dividend, spinoff, unit_split); │
│ │ │ and filter by process_date vs. ex_date. │
──────

## Detailed Technical & Financial Analysis

### 1. Financial Correctness of OHLCV Data Processing

• Precision & Numerical Integrity: All financial values are required to be decimal strings or integers via \_number. Binary floating-point values are rejected (isinstance(value, (bool,
float))). Fixed-point serialization uses format(number, 'f'), stripping trailing zeros cleanly while preserving exact values down to '0'.
• Bar Relationship Constraints: normalize_bundle enforces:
low ≤ min(open, close) ≤ max(open, close) ≤ high

Prices must be strictly positive (positive=True), preventing division-by-zero or non-economic negative equity prices.
• Volume Integrity: Volume is enforced to be an integer decimal string (integer=True). Negative and fractional share volumes are rejected. Zero volume is permitted and tracked in
quality['zero_volume_sessions'].
• Finding F-03 (Risk in Zero-Volume Sessions): In market_data.json:39, the bar for 2024-12-02 specifies:
{"date": "2024-12-02", "open": "51", "high": "54", "low": "50", "close": "53", "volume": "0"}
If zero shares traded during a session, trades at different prices ($50, $51, $53, $54) cannot occur. In real exchange data, an un-traded session either has no bar or carries over prior
close with open = high = low = close. normalize_bundle does not assert high = low when volume = 0.

### 2. Raw Versus Adjusted Price Representations

• Series Segregation: In dataset_key, the SHA-256 series key incorporates instrument, provider, feed, adjustment, and adjustment_as_of. Raw (adjustment='raw', adjustment_as_of=None) and
adjusted series (adjustment='split'/'dividend'/'all') map to completely independent dataset directories and manifests.
• Adjustment Anchor Rigor: Lines 117–120 strictly require that raw prices have no adjustment_as_of anchor, while adjusted prices require an anchor that is already observed and on or after
each bar's date (day <= basis at line 158). This prevents un-anchored or future-anchored adjusted bars.
• Default Query Behavior: MarketStore.inspect defaults to adjustment='raw'. If multiple feeds or anchors match an identifier, line 193 raises an ambiguity error rather than guessing.

### 3. Stock Splits, Dividends, and Corporate-Action Handling

• Corporate Action Modeling: Supported action kinds are strictly 'split' and 'dividend' in normalize_bundle. Unsupported actions (such as mergers, spin-offs, rights offerings) fail closed
with MarketDataError.
• Split Ratio Orientation: A split ratio is defined as new shares/old shares (ratio > 0). A 2:1 forward split has ratio = 2; a 1:2 reverse split has ratio = 0.5.
• Evidence Binding: Line 207 hashes the complete action coverage and its event IDs into evidence_id. Lines 246–247 verify this hash prior to return calculation.
• Finding F-04 (Missing Payment Date Validation): Lines 196–197 validate that payment_date is a valid ISO date string, but do not assert payment_date >= ex_date.

### 4. Price-Return Calculations and Dividend Exclusion

• Formula Verification: In split_price_return, price return is calculated as:
P × ∏ ratioₐ
end, raw {a∈splits|start<ex_date≤end}
R = ──────────────────────────────────────────────── - 1
price P
start, raw
• Effective Date Interval: Line 263 uses start < ex_date ≤ end.
• An action with ex_date == start took effect at market open on start. The close of start already reflects the post-split price, so it must be excluded.
• An action with ex_date == end took effect at market open on end. The close of start was pre-split, while the close of end is post-split, so it must be included.
• This inequality is financially exact.
• Exclusion of Dividends: In line 268, if row['kind'] == 'split': filters exclusively for splits. Cash dividends are completely excluded from the factor and price return. The output
explicitly states:
'Price return excludes dividends; it is not total return'.
• Double-Adjustment Prevention: Line 241 requires endpoints[0]['adjustment'] == 'raw', rejecting adjusted bars to prevent compounding adjustments.

### 5. Historical Availability, Revisions, and Lookahead Bias

• No Future Data Leakage:
• Bar session close must precede observation time (market_data.py:156).
• Published time cannot be in the future relative to observation (market_data.py:166).
• Revisions are preserved in full via observation timestamps. In MarketStore.inspect, \_latest selects only records where observed_at <= as_of.
• Finding F-05 (Lookahead Cutoff vs. Historical Backtesting):
Lines 189–192 require max(created_at, retrieved_at) <= as_of.
• When historical data (e.g., 2016–2024) is ingested in 2026, created_at and retrieved_at are 2026.
• Querying inspect(identifier, as_of='2020-01-01') fails with MarketDataError: No saved market snapshot was observed by the requested cutoff.
• This is an intentional safeguard preventing researchers from claiming they had access to historical data before the local system actually ingested it. However, because
MarketStore.inspect lacks start_date and end_date parameters for bar filtering, running historical backtests on bulk-ingested history requires querying with as_of = current_time (which
permits lookahead up to the current collection timestamp).

### 6. Trading Calendars, Missing Sessions, and Time Zones

• Explicit Calendar Verification: Calendar sessions are provided as explicit inputs (market_data.py:131-139). Weekday heuristics are forbidden.
• Session Boundaries in Local Time: Lines 135–136 verify that both open_at and close_at, when converted to the instrument's timezone (e.g., America/New_York), match the session's calendar
date YYYY-MM-DD.
• Early Closes & DST: Tested and handled correctly. Early closes (e.g., Black Friday 1:00 PM ET / 18:00 UTC) are distinct explicit sessions (test_market_data.py:72-76). DST changes shift
UTC opening/closing hours while preserving local session dates (test_market_data.py:77-86).
• Missing Session Treatment: An expected session without a bar is flagged in quality['missing_sessions']. No synthetic prices or zero volumes are fabricated.
• Calendar Revision Guard: If an existing calendar session is modified or removed in an incremental run, line 310 fails closed with MarketDataError('Calendar revision requires explicit
reconciliation; prior snapshot retained').

### 7. Incremental Updates, Pagination, Caching, and Storage Integrity

• Content-Addressed Storage: Raw responses are stored under raw/<sha256>.json, and normalized snapshots under normalized/<sha256>.json. Manifests chain backwards via previous_snapshot_id.
• Provenance Verification on Load: MarketStore.load verifies SHA-256 checksums of every raw page in manifest['sources'] and the normalized data payload.
• Deduplication: Unchanged records within the overlap window are deduplicated (market_store.py:325). A→B→A revisions create new observation occurrences rather than resurrecting older
states.
• Atomic Publication: Files are written to temporary paths and replaced atomically via os.replace (storage.py:26). latest.json is replaced only after all snapshot files are persisted.
• Finding F-01 (Material Limitation in Incremental Updates):
In collect_market:
• On an incremental collection, start is shifted forward to old_end - overlap_days.
• header['action_coverage'] is clipped to this incremental window (action_start = max(start, actions['start'])).
• Lines 327–332 expand data['coverage']['start'] = min(old['coverage']['start'], start).
• However, data['action_coverage'] is NOT merged across incremental runs. data['action_coverage']['start'] remains the incremental start date.
• In MarketStore.inspect#L209-L211:
result['quality']['actions_complete'] = (result['action_coverage']['complete'] and
result['action_coverage']['start'] <= result['coverage']['start'] and
result['action_coverage']['end'] >= result['coverage']['end'])
Since action_coverage['start'] > coverage['start'], actions_complete evaluates to False for the entire stored dataset.
• In split_price_return#L228:
if ... \_date(action_coverage['start']) > first:
raise MarketDataError('Complete action evidence was not available by the cutoff')
Any attempt to calculate returns over a historical interval starting before the most recent incremental window fails with MarketDataError.
• While intended as a conservative check (refusing to assume older action ledgers remain complete without re-fetching), this makes long-running daily incremental ingestion unusable for
historical return calculations unless --refresh is passed every time.

### 8. Error Handling, API Reliability, and Concurrency

• Request Budget & Pagination Loop Protection: Lines 263–264 enforce max_requests across pagination and retries. Line 293 detects cyclic or empty pagination tokens.
• Credential Suppression: Lines 279–280 catch generic source errors and raise MarketDataError('Source request failed; sensitive request details suppressed') from None, preventing token
leakage in logs or exceptions.
• Finding F-02 (Domain Coupling Defect in Locking):
In collect_market#L238:
with Store(store.root).collection_lock():
Store.collection_lock() in storage.py:47 raises SecError("Another collection is using this store; skip this invocation") when fcntl.flock encounters BlockingIOError. Raising SecError
during a market collection leaks an SEC-specific exception across module boundaries.

### 9. Realism of Synthetic Fixtures

• Idealized Bundle Structure: market_data.json bundles instrument metadata, exchange calendar, price coverage, action completeness assertions, and unified pages containing both bars and
actions into a single file. Real market data providers (such as Alpaca and Twelve Data) split these across distinct endpoints with divergent schemas and pagination tokens.
• Cryptographic Action Proofs: The fixture includes action_coverage.evidence_id. Real APIs do not provide cryptographic completeness proofs; live adapters must formulate completeness
evidence based on provider-specific coverage parameters.

### 10. Architecture Suitability for Alpaca and Other Providers

• Decoupled Normalization: normalize_bundle is pure and testable.
• Finding F-06 (Alpaca Integration Gaps): 1. Endpoint Multiplexing: An Alpaca adapter will need an intermediate orchestrator that fetches bars from /v2/stocks/bars, actions from /v2/corporate-actions, and hours from
/v2/calendar, synthesizing the bundle format expected by normalize_bundle. 2. Action Taxonomy: Alpaca's corporate action types (stock_dividend, spinoff, unit_split, cash_dividend) must be mapped to the strict 'split' or 'dividend' types expected by
normalize_bundle#L182. Stock dividends (which adjust share counts without cash amounts) are currently unsupported. 3. Filtering Semantics: Alpaca filters corporate actions by process_date, not ex_date. The adapter must maintain an independent cursor for action polling. 4. Multi-Symbol Handling: Alpaca returns multi-symbol dictionaries ({"bars": {"AAPL": [...], "MSFT": [...]}}), whereas Milestone 3 storage is organized strictly per-instrument key. 5. Synthetic Gate: Multiple modules assert bundle['synthetic'] is True. An explicit adapter configuration switch will be needed once live rights are verified.

### 11. Important Missing Regression Tests

While the existing test suite (test_market_data.py and test_market_store.py) is comprehensive (46 market-specific test cases), the following regression tests are recommended:

1. Multiple Compounded Splits: Test return calculation across two or more sequential splits in the same interval (e.g., a 2:1 split followed by a 3:1 split, validating factor = 6).
2. Fractional Split Ratios: Test non-terminating decimal split ratios (e.g., a 3:7 reverse split) to verify precision handling under Decimal context (prec=34).
3. Ex-Date Boundary Cases: Explicitly test split actions occurring exactly on start (must be excluded) and exactly on end (must be included).
4. Zero-Volume Sessions with Constant Price: Test a valid zero-volume session where open = high = low = close.
5. Lock Collision: Test concurrent collection invocations against the same data directory to verify non-blocking lock handling.

### 12. Provider Licensing and Live-Validation Limitations Documentation

• Honesty & Disclosures: MARKET_DATA_PROVIDER_DECISION.md and MARKET_DATA.md document all provider constraints with primary URL citations and access dates.
• Unresolved Gates: The documents candidly acknowledge that Alpaca Basic's historical SIP access contradicts its IEX-only paper guide, and that post-termination retention rights remain
unverified. Twelve Data's corporate action paywall and EOD volume delay are explicitly highlighted.
• Live Status: The documentation clearly states that live market-data integration is incomplete, that no real credentials were used, and that no real AAPL/MSFT data was retrieved.
──────

## Actionable Recommendations

1. Address Incremental Action Completeness (F-01):
   In collect_market, allow contiguous or overlapping complete action windows to be merged (action_start = min(old_action_start, new_action_start)) when both prior and current collections
   asserted completeness and no actions were dropped, preserving historical return capability across incremental updates.
2. Decouple Concurrency Lock (F-02):
   In storage.py:40, extract collection_lock into a generic utility or have it raise a generic StoreLockError (or MarketDataError when invoked from market store) to eliminate SEC coupling.
3. Enforce Zero-Volume Flatness (F-03):
   In normalize_bundle, assert that if volume == 0, then open == high == low == close. Correct the synthetic fixture market_data.json:39 to reflect realistic zero-volume behavior.
4. Validate Action Payment Date (F-04):
   In normalize_bundle, add:
   if row.get('payment_date') and row['payment_date'] < row['ex_date']:
   raise MarketDataError('Payment date cannot precede ex-date')

5. Prepare Adapter Abstraction for Alpaca (F-06):
   When live provider integration is authorized, introduce an explicit provider adapter layer that maps Alpaca's three endpoints, translates stock_dividend and split rates, and manages
   process_date polling independently of raw session dates.
