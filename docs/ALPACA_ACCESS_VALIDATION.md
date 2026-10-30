# Milestone 3B — Temporary Alpaca access validation

**Historical stage report:** the user subsequently authorized full private historical collection and local persistence subject to provider terms. The temporary/offline-only restrictions recorded below are superseded by that scope. See [current live implementation and validation](ALPACA_LIVE_VALIDATION.md); the original observations and limitations below remain a record of that earlier stage.

Checked **2026-10-29**, using the user's locally configured Basic paper credentials. The user explicitly authorized small read-only AAPL/MSFT authentication/feed requests. **Authentication and sampled historical access succeeded. Durable market collection remains disabled** because applicable account/feed and retention conditions are unresolved; see [the focused access review](ALPACA_ACCESS.md).

## Observed results

Only `GET https://data.alpaca.markets/v2/stocks/bars` was called. Each request explicitly specified both symbols, `timeframe=1Day`, a single historical New York calendar day, `limit=2`, `adjustment=raw`, `asof=-` and the named feed. No Trading endpoint, account read, order route, upgrade or automatic feed fallback was used.

| UTC verification time | Feed | Market date | HTTP | AAPL bars | MSFT bars | Interpretation |
| --- | --- | --- | ---: | ---: | ---: | --- |
| 10:26:12 | IEX | 2024-11-01 | 200 | 1 | 1 | Authentication and this sample available; OHLCV consistency passed. |
| 10:26:12 | SIP | 2024-11-01 | 200 | 1 | 1 | Explicit historical SIP diagnostic succeeded; use permission remains unresolved. |
| 10:31:17 | IEX | 2016-01-04 | 200 | 0 | 0 | Empty sample, not authentication failure or proof that prices never existed. |
| 10:31:17 | IEX | 2024-11-01 | 200 | 1 | 1 | Reusable application probe succeeded. |
| 10:31:46 | SIP | 2016-01-04 | 200 | 1 | 1 | Explicit SIP diagnostic found both historical samples. |

These were **five successful HTTP requests**, not a coverage survey. No further page token was returned. An advertised provider-wide history start must not be applied indiscriminately to every feed. The empty IEX sample does not establish an exact start boundary, missing-session reason or a survivorship-free dataset. SIP's HTTP success does not override the paper-only documentation's IEX entitlement or resolve account terms.

The sandbox attempts could not reach the network; approved host execution succeeded using the same identity and request scope. No transport, proxy or access-control bypass was introduced to overcome an HTTP refusal. Successful bodies were parsed in process memory and never written to local market storage, fixtures or logs. This report records only non-sensitive status/count/method observations, with no prices, raw payloads, credentials, account identifiers or response headers. Without retained payloads, these observations are not an independently replayable real-data archive; synthetic regressions supply deterministic replay evidence.

The first two requests used an inspected one-off diagnostic. The final three used the reusable application command:

```sh
PYTHONPATH=src python3 -m investor_ai market probe --date 2016-01-04 --date 2024-11-01
PYTHONPATH=src python3 -m investor_ai market probe --feed sip --date 2016-01-04
```

The first command intentionally exited **1** because its 2016 IEX sample was empty; its 2024 sample passed. The SIP command exited **0**. Exit 1 here is conservative coverage reporting, not an application defect. Fresh invocations repeat small network checks and do not reuse or create a market-data cache. Default `market probe` checks only IEX on 2024-11-01. SIP is always an explicit diagnostic choice.

## Implemented and tested

`alpaca_probe.probe_access` is a separate memory-only diagnostic module, not a live source for `MarketStore`. It loads selected local credential literals, rejects malformed/conflicting aliases and an explicitly non-paper configuration, places secrets only in request headers, and accepts no arbitrary route/method or storage handle. It stops on authentication failure and bounded-policy/network failures; redirects are disabled. HTTP error bodies are closed without reading. Only a sanitized summary is public output.

The probe reuses the established Decimal and New York daily-bar validators. The rate-reset and Retry-After policy calculations were extracted from replay for reuse without changing its behavior. One shared budget includes retries; default two requests/second, one retry, 20-second timeout, at most five seconds per cooldown. Up to three dates/two feeds are accepted, with no automatic pagination or bulk acquisition. Both symbols and no remaining page token are required for a complete diagnostic sample.

**219 automated tests pass**: 209 prior collector/analysis/market/replay tests plus 10 new synthetic probe tests. New checks deny real networking, use dummy credentials, and cover exact GET host/path/query scope, header-only authentication, credential/exception redaction, dotenv literal parsing, no shell expansion, redirects, 401 stopping, incomplete samples, body bounds, OHLC validation, quota reset/retry budgets and CLI dispatch without constructing a market store. No downloaded real-price fixture or dependency was added.

Both existing original SEC filing verifiers pass again offline: 52 current-period inputs plus eight comparatives and 48 independent formulas; 16 cumulative endpoints, 18 derived Q2/Q3/Q4 values, four direct-quarter controls and both offline adapters. Output writes were suppressed and existing evidence/report hashes checked to preserve original review timestamps. SEC collector/financial logic remains unchanged. These compatibility results do not validate live Alpaca price accuracy.

A dedicated independent read-only reviewer additionally forbade market storage and file-writing operations while running multiple explicit synthetic feed/date checks. The temporary filesystem stayed unchanged. No credential leak, Trading route, persistence path or automatic fallback defect was reproduced. Existing offline adapter functionality remains available for further work within unresolved permission constraints.

## Remaining gates

- **Retention is unresolved.** Account creation and configured credentials are user-reported facts; actual accepted signup/subscriber agreements were not supplied or inspected. Public personal-use language is positive evidence, not a durable raw/normalized archive grant. The brokerage agreement must not automatically be asserted to apply to a paper-only account.
- **SIP use is unresolved.** General delayed-historical access documentation and observed responses are broader than the paper-only guide's IEX entitlement. Keep SIP as an explicit bounded diagnostic until the applicable rights are clear; do not silently substitute it to fill IEX gaps.
- Actual adjustment factors, corporate actions, calendars, returns, incremental provider corrections and wide historical coverage were **not** live-validated by these checks. They remain offline-tested where implemented. No Trading calendar request was made because the user's current Trading scope permits only optional read-only authentication checks.
- Persistent provider adapter, secure archival authorization, durable account-wide quota/process-date state, original-source price/action reconciliation and prior documented complex-action/PIT limits remain live acceptance gates. A successful sample cannot certify historical market accuracy or information availability before local retrieval.

No commit or push was made. Source, tests and reports remain for review; `.env`, real local datasets, caches and generated artifacts remain outside Git.
