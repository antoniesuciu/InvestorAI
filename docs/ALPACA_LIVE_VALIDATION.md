# Milestone 3B — Live Alpaca integration validation

Validated **2026-10-29**, Python 3.12.3, against published Milestone 3A baseline `85c8575` and the existing uncommitted 3B work. **The live adapter is implemented and suitable for independent review. Nothing was committed or pushed.** No new dependency, paid service, account modification, order, forecasting, AI or dashboard functionality was added.

## Authorization, access and actual scope

The latest user prompt explicitly authorizes historical SIP collection and private noncommercial local persistence subject to applicable provider terms. It says not to block solely on unconfirmed indefinite post-termination retention. This supersedes the previous temporary-only scope; it does **not** establish contractual permission, perpetual retention or redistribution rights. [Current access research](ALPACA_ACCESS.md) preserves account/SIP wording and retention uncertainties. There is no permission checkbox or API-success claim masquerading as a provider grant. Explicit access restrictions and HTTP failures are not bypassed.

Credentials were loaded from ignored local configuration and never printed, put in URLs, stored with response evidence or added to Git. Approved host execution was used because sandbox networking is unavailable. Authenticated requests use only fixed GET routes: Market Data `/v2/stocks/bars`, `/v1/corporate-actions`, and paper-host read-only `/v2/assets/{symbol}` and `/v2/calendar`. The user's full-adapter scope authorizes these identity/calendar reads; no order, live-trading host or account-changing route exists. Historical SIP is explicit, with no IEX/provider/paid fallback and no recent paid-feed access.

Four preliminary read-only schema requests checked assets, calendar and action shape. Controlled collection then used **48 requests** for the first six series and **40** for the corrected incremental/adjusted refresh. Each invocation shared a client capped at 100 attempts, two requests/second, bounded retries/cooldowns and no redirect. This respects the controlled invocation budget; independent external clients/stores are not coordinated by an account-wide quota service.

## Real datasets and coverage

All downloaded evidence, snapshots, local verifiers and detailed results live under ignored `data/market/`. Versioned fixtures remain invented. Current data:

| Company | Representation | Daily bars | Stored range | Missing sessions against returned calendar |
| --- | --- | ---: | --- | ---: |
| AAPL | Raw | 2,270 | 2016-01-04–2025-01-10 | 0 |
| AAPL | Split adjusted | 2,270 | Same | 0 |
| AAPL | Dividend adjusted | 2,270 | Same | 0 |
| MSFT | Raw | 2,270 | Same | 0 |
| MSFT | Split adjusted | 2,270 | Same | 0 |
| MSFT | Dividend adjusted | 2,270 | Same | 0 |

This is **13,620 current bars across six separate representations**, not six independent sources. Initial 2016–2024 collections had 2,264 bars each and traversed **five bar pages per series** at `limit=500`, including the short final page. The extension added six sessions. The returned calendar correctly excludes January 9, 2025; Nasdaq's [original closure announcement](https://ir.nasdaq.com/news-releases/news-release-details/nasdaq-announces-closure-its-us-markets-honor-national-day-0) independently confirms that extraordinary closure. No weekday guessing or artificial January 9 bar was used.

Zero missing sessions means agreement with the **returned** calendar for these two selected companies, not proof of complete or accurate global provider data. It establishes neither delisted coverage, historical universe membership nor the earliest possible date. Both current asset UUIDs/listing metadata were checked; historical ticker/venue reconstruction is not inferred.

Initial action process requests cover 2016-01-01 through retrieval day (2026-10-29), independently of the price end date. AAPL retains **44 supported records** (43 dividends and one split); MSFT retains **43 dividends** plus an unsupported cash-merger record in original response evidence/diagnostics. Later process/ex-dates can therefore exceed the price range. The live source does **not** assert exhaustive action coverage. Every sampled dividend omits explicit cash currency; normalized `cash_currency=None` and `currency_status=unknown` preserve that gap. Selected issuer controls below establish currency only for those checks; no wholesale backfill was made.

## Incremental storage, provenance and offline reproduction

- Raw extension requested **2024-12-24–2025-01-10**, one price page per company, preserving earlier values and their original observation times.
- Split/dividend representations refreshed **2016-01-04–2025-01-10**, five pages each, without merging incompatible old/new adjustment bases. Each adjusted acquisition records its own vintage fingerprint; missing previously stored adjusted observations abort publication.
- Actions use separate process-date state with a seven-day correction overlap. A full `--refresh` resets historical process coverage to `--process-start`; late edits outside the overlap still need that periodic reconciliation. Empty windows never certify complete action history.
- Canonical raw envelopes preserve every successful original endpoint body, its SHA-256, safe request/cursor metadata and actual receipt time. Normalized facts retain source references, provider action IDs/dates/rates and revisions. Neither bar `t`, action process date nor symbol `asof` is invented as publication time.
- **All six current and six prior snapshots** were hash-verified and replayed from original provider bodies with DNS/connect access denied. Reparsed canonical data and retained observation IDs/times matched. Earlier prototype snapshots were preserved, not rewritten; unknown adjustment-vintage evidence cannot authorize returns.
- Cached collection for both companies made **zero endpoint requests** and retained snapshot identity. CLI replay and split-adjusted return commands passed with network attempts forbidden. Inspection/replay/calculation require no credentials or network; collection cache checks still use local configuration.
- Pre-collection historical cutoffs were refused despite 2016–2025 market dates. Current adjusted knowledge is not presented as known then. Provider multi-page retrieval is not an atomic provider snapshot; original responses make the actual acquired sequence reproducible.

Ignored reproduction records: `verification/live_collection.json`, `live_final_collection.json`, `live_financial_validation.json` and `verify_live.py`. These are suitable local datasets/reproduction records, not downloaded fixtures to add to Git.

## Independent financial controls

Only selected controls were reconciled; this is not a full independent OHLCV audit. Original issuer/exchange pages were consulted on 2026-10-29. Independent `Fraction` arithmetic operates on decimal-string inputs; production arithmetic uses Decimal at 34 digits.

**Apple 2020 split:** Apple's [July 30, 2020 announcement](https://www.apple.com/newsroom/2020/07/apple-reports-third-quarter-results/) states a 4:1 split, August 24 record date and split-adjusted trading from August 31. The retained action matches ratio, record date, August 31 ex/trading date and August 28 distribution/payable date. August 28 raw/split OHLC comparisons match a factor of four within **$0.005 per adjusted price**; split-adjusted volume is exactly four times raw volume in that sample. August 31 prices match across raw/split modes.

The supplied adjusted prices in this example are rounded to cents. The independent raw four-share endpoint reference yields **3.3912224826%**, whereas the split-adjusted endpoint calculation yields **3.3891515103%**. The difference, about **0.002071 percentage points**, follows from the observed adjusted-price rounding. It was measured and retained, not silently forced to zero or claimed to be a universally guaranteed provider rounding policy.

**Cash dividends:** [Apple's dividend history](https://investor.apple.com/dividend-history/) confirms the selected $0.25 distribution, November 11, 2024 record date and November 14 payment. The retained fields match; Apple's table does not establish the API's November 8 ex-date, which remains separately sourced. Microsoft's [September 16, 2024 issuer release](https://news.microsoft.com/source/2024/09/16/microsoft-announces-quarterly-dividend-increase-and-new-share-repurchase-program-3/) confirms $0.83, November 21 ex/record date and December 12 payment; all selected fields match the API.

For those selected ex-date pairs, an independently constructed multiplicative cash-adjustment control, using issuer cash and source raw/ex-date adjusted prices, agrees with the prior adjusted close within the propagated cent-rounding bound. Absolute differences were approximately **$0.001951 (AAPL)** and **$0.004322 (MSFT)**; both are below their approximately $0.01 two-price rounding bounds. Dividend-mode volume matched raw volume for **all 2,270 dates per company**. These checks are evidence for the selected supplied representations, not proof of a general reinvestment/total-return algorithm.

| Split-adjusted price-return calculation | Independent exact reference | Result |
| --- | --- | ---: |
| AAPL, 2020-08-28→2020-08-31 | 423 / 12,481 | 3.3891515103% |
| AAPL, 2024-11-07→2024-11-08 | −13 / 5,687 | −0.2285915245% |
| MSFT, 2024-11-20→2024-11-21 | −262 / 41,549 | −0.6305807601% |

Production values matched independent Fraction references within `1e-32` in fractional-return units. Dividends are excluded from these **price returns**. Raw-return requests remain unavailable without sufficient complete scoped action evidence; provider page exhaustion is not substituted for that evidence. No investment profitability forecast, total-return claim, fee/tax model or reinvestment assumption is implied.

## Automated tests and independent review

**239 tests pass**: all 219 prior collector/analysis/market/replay/probe tests plus 10 new live-adapter integrations and 10 transport regressions. Tests use synthetic HTTP data, dummy credentials, temporary stores and no real endpoints. They cover authentication/redaction, fixed GET/host/query routes, denied orders/paid/recent access, redirects, unread errors, exact Decimal parsing, pagination/loops, limits/retries/resets, original-response replay, unknown currency/unsupported actions, independent action cursors, full historical refresh, revisions and offline cache behavior. The existing 3A test still exercises actual process exits/restart and immutable historical snapshots.

A dedicated adversarial reviewer reproduced substantive issues before correction:

1. A narrow adjusted update could retain older incompatible prices. Refresh now covers stored/requested history, and mixed acquisition vintages cannot generate returns.
2. An omitted old adjusted bar could survive a refreshed basis. Such updates now abort and preserve the previous pointer.
3. A midnight-crossing acquisition could mislabel its adjustment day. Final receipt/completion dates are checked; the acquisition fails closed.
4. Malformed action categories/rows caused uncontrolled exceptions. They now produce controlled data errors without publication.
5. Arbitrary query fields could admit credentials into metadata. Query allowlists and configured-secret/value checks reject these before URL/history construction.
6. Mutable response metadata aliased internal request provenance. Response/evidence metadata is now detached; the regression passes.

Original failing reproductions and final narrow re-review passed after correction. No remaining critical defect was reproduced. These checks do not establish perfect market data or a full corporate-action/accounting model.

Both original SEC filing verifiers pass again offline: **52 current-period inputs + eight comparatives + 48 independent formulas**, and **16 cumulative endpoints + 18 derived quarterly values + four direct-quarter controls**, including both offline adapters. Verifier writes were suppressed and prior file hashes checked, preserving SEC evidence/review timestamps. Finalized SEC/financial code remains unchanged.

## Remaining limitations

- Accepted account/subscriber agreements, historical SIP wording and duration/deletion duties remain unverified. Follow applicable explicit terms; no perpetual archive/public redistribution/display/ML permission is claimed. Indefinite retention uncertainty alone is not being used to block the user-authorized private implementation.
- Independent issuer controls validate selected actions and arithmetic; exchange controls validate one unusual closure. Broad independent raw OHLCV/volume accuracy was not certified. Feed/session/rounding definitions may differ across vendors; no unreviewed secondary dataset was silently imported as truth.
- Action completeness remains false. Missing cash currency, older missing dates, unsupported cash mergers, retractions/cancellations, changed event IDs and unbounded delayed corrections remain visible. Current UUIDs and disabled symbol remapping do not solve historical security-master/delisting reconstruction.
- Within-acquisition multi-page provider changes cannot be proven absent without a provider snapshot/vintage guarantee. Lookahead/survivorship-free backtesting, arbitrary historical adjustment freezes and verified total returns are not delivered.
- No account-wide quota/scheduler service, hardware power-loss guarantee or hostile-manifest protection is claimed. Other account traffic must be coordinated. Existing share-count/market-cap/valuation, forecasting, AI, portfolio, dashboard and execution exclusions remain unchanged.

**Readiness:** the requested live historical adapter, private versioned datasets, offline replay and verified scoped financial calculations are ready for independent review with these limits. They are not approved for commit/push or promoted to a broader production research/trading platform.
