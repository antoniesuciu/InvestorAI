# Milestone 3B — Offline Alpaca adapter validation

**Historical stage report:** the user subsequently authorized full private historical collection and local persistence subject to provider terms. The temporary/offline-only restrictions recorded below are superseded by that scope. See [current live implementation and validation](ALPACA_LIVE_VALIDATION.md); the original observations and limitations below remain a record of that earlier stage.

Validated **2026-10-29** against finalized Milestone 3A baseline `85c8575`, Python 3.12.3, standard library only. **The authorized offline fallback is implemented and suitable for independent review. Full live Milestone 3B remains incomplete.** No new commit or push was made.

This report preserves the **initial offline replay validation**. The user subsequently configured a paper account and authorized temporary authenticated checks. Their separate implementation/results and the current full suite are recorded in [ALPACA_ACCESS_VALIDATION.md](ALPACA_ACCESS_VALIDATION.md); no real market payload was added to this report or its fixtures. Persistent collection remains disabled.

## Permission decision and delivered scope

[Current official-source research](ALPACA_ACCESS.md) confirms free technical capabilities and personal-use language, but does not establish the applicable account/feed and durable archive permissions. The customer agreement's market-data conditions cannot be replaced by an older local-storage tutorial. No live market request was attempted, no credentials were loaded and no real market data was persisted. The conditional offline fallback in the user's scope applies; this is a rights gate, not a sandbox workaround or a claim that all private caching is prohibited.

Delivered code is `alpaca_replay.AlpacaReplaySource`, the small optional preparation interface in `market_store.collect_market`, and explicit CLI mode `--provider alpaca-replay`. The provider runner describes requests and consumes authored response transcripts; it has **no HTTP transport or authentication implementation**. Shared normalization, market snapshots, historical inspection, caching and financial calculations remain reusable and separate from SEC storage. Finalized SEC collector/analysis source files are unchanged.

## Tests and independent references

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -p test_alpaca_replay.py -v
PYTHONPATH=src python3 -m unittest discover -s tests -q
```

**209 tests pass**: the existing 187 Milestone 1–3A tests plus 22 Alpaca replay tests. Every replay test forbids DNS, socket connections and `connect_ex`. Stores are fresh temporary directories; all versioned provider response bodies and prices are invented. The original 3A scenario still tests two actual process exits, atomic publication/restart and old snapshot integrity.

| Evidence | Validated result |
| --- | --- |
| Separate endpoint streams | Calendar, two bar pages and two action pages; independent cursors, exact query matching and continued traversal after a short page. One canonical envelope preserves all five successful original bodies with SHA-256 hashes and safe endpoint/query/receipt metadata. |
| OHLCV/adjustments | Numeric JSON decimals stay exact (`100.01` remains `100.01`). Raw, supplied split-adjusted and supplied dividend-adjusted series stay separate. Split-adjusted volume and dividend-mode unchanged volume are preserved as authored. No provider adjustment formula or real adjustment accuracy is claimed. |
| Initial financial reference | $100 initial cost becomes two shares × $53 = $106, an exact **6% split-corrected price return**. Cash dividends are excluded. Independent references use `Fraction`, not production Decimal arithmetic. |
| Incremental correction/reversion | Prices refresh from December 2 while action process polling begins November 1. A revised split effective November 29 changes current price return to **62%** ($100 → 3×$54); reverting its rate restores **8%** ($100 → 2×$54). Original-cutoff return stays 6%; A→B→A occurrences and earlier snapshots remain readable. |
| Reverse split | `new_rate=1`, `old_rate=2` gives 0.5, independently yielding **−73.5%** for $100 → half a share × $53. This invented price path is not a real-company claim. |
| Calendar/date handling | New York midnight daily timestamps, DST change (14:30→13:30 UTC regular opening), early close and payment-before-ex-date special dividend. A daily bar is not treated as final at regular close; conservative day-end/feed-delay checks apply. |
| Completeness/PIT | Page exhaustion without an authored synthetic review leaves coverage incomplete and blocks unsupported returns. An old process/effective date never becomes a publication timestamp. Pre-collection queries fail; endpoint receipts later than the collector's clock cannot be backdated. |
| Failure/restart behavior | Missing/incompatible dates/currency/symbols, unknown corporate actions, malformed/looping pages, exhausted budgets, changed prepared identities and HTTP failures do not publish a replacement snapshot. Cache hits skip preparation and keep original snapshot/availability times. Existing corruption/interruption regressions continue to pass. |
| Response policy | Terminal 301/400/401/403 failures do not redirect/retry or fall back to another feed/paid source. Transient failures share bounded budgets/limiting, honor numeric/date `Retry-After` and rate-reset headers, and reject nonfinite/negative/excessive cooldowns. Error bodies are not printed or retained. |

### Existing original SEC filing verifiers

Both existing ignored verifiers were rerun against previously saved original SEC filings with networking forbidden:

- `data/sec/verification/verify_analysis.py`: **52 current-period inputs**, eight comparative growth inputs and **48 independent formula checks** passed.
- `data/sec/verification/verify_analysis_review.py`: **16 cumulative endpoints**, **18 Q2/Q3/Q4 derived values**, four directly reported Microsoft quarter controls and both offline adapters passed.

`investor_ai.storage.write_json` was patched only during these verifier executions to suppress their output writes. All numerical, original-source and document-hash assertions ran. Existing evidence/verification files were hash-checked afterward and unchanged; original review timestamps were not refreshed merely by rerunning validation. These checks validate SEC compatibility, not live Alpaca data.

## Independent adversarial review

A dedicated read-only subagent reviewed the new module, fixture, preparation seam and tests, and ran independent synthetic reproductions. Three confirmed integration defects were reproduced with failing regressions before correction:

| Finding | Correction and verification |
| --- | --- |
| Nested endpoint receipts could follow the published observation time under mismatched/backward clocks. | Prepared evidence returns an upper receipt bound; collection rejects any later nested or canonical-page receipt before normalization/publication. Original reproduction and regression now pass. |
| Preparation requests could exceed the collector's programmatic request limit. | The collector passes its budget into preparation; the adapter uses the stricter bound. `max_requests=1` stops after one endpoint attempt, without publishing. |
| HTTP 429 reset headers were ignored when `Retry-After` was absent. | Rate-reset policy applies to retryable responses as well as successful ones, combining reset, backoff and `Retry-After` conservatively. A three-second reset delays retry; a 61-second reset stops before retry. |

Initial tests also reproduced nonfinite/negative cooldown acceptance and boolean schema-version acceptance; both are now rejected. Two test-authoring mistakes were corrected without weakening production guards: a second incremental request had moved to the next covered end, and an adjusted anchor must fit the **New York local** observation date.

The reviewer independently reconfirmed the three corrections, found no further material defect, and judged the offline fallback suitable for independent review. An additional separate scenario verified that an incomplete process-date-only poll can retain prior actions while blocking a current unsupported return and preserving its earlier complete cutoff. No provider omission/cancellation guarantee follows from these tests.

## Remaining limits and live acceptance gates

- **Live AAPL/MSFT validation: not performed.** Real OHLCV, adjustment factors, corporate actions, price returns and incremental endpoint behavior remain unverified because applicable permissions are unresolved. Fixture tests do not measure provider quality or account entitlement.
- No real HTTP/authentication, durable account-wide quota or durable process-date cursor/overlap coordinator. The source owns its endpoint retry/limiter policy; the outer collector owns canonical-page policy. Both default to two requests/second, and the request budget is shared. This is not live quota compliance.
- One explicitly supplied USD US-equity identity on IEX, with symbol remapping disabled. No inferred historical listings, delisted proceeds, multi-symbol watchlist orchestration or consolidated-feed equivalence.
- Adjusted anchors are authored synthetic snapshot evidence, not a provider historical-adjustment query. `all`, spin-offs/combined adjustments, complex actions, cancellation/retractions, changing event IDs and arbitrary correction horizons remain unsupported. Split ratios retain the existing finite 34-digit Decimal convention; raw numerator/denominator remain auditable.
- Missing/empty dividend currency or incomplete action fields stop collection; they are not guessed. A manual synthetic action review is not a live-provider completeness certification. Current import times do not establish historical availability before collection.
- No shares/market-cap/valuation, total-return, dashboard, AI, forecasts, portfolio or execution functionality was added. Existing hardware power-loss and manifest-tampering limitations remain unchanged.

Before live acceptance, establish applicable free account/feed, automated private-use and durable raw/normalized retention/deletion rights; add secure transport and coordinated quotas/cursors; reconcile actual AAPL/MSFT prices/actions/calendars/adjustments with primary evidence. No paid fallback, access-control bypass or permission toggle is provided. The current deliverable is ready for review **as the explicitly authorized offline fallback**, not a completed live integration.
