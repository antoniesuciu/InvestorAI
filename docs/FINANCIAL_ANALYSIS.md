# Milestone 2 — Financial Analysis Engine

Implementation completed and approved by the user on **2026-10-28** (initial implementation 2026-10-27). This is an offline, standard-library Python analysis layer over saved official SEC CompanyFacts responses. It adds no provider, account, paid service, dependency, web frontend, forecast, AI inference or trading function. Milestone 1's seven-metric collection behavior and immutable snapshots remain unchanged.

The complete Gemini Milestone 2 review was received and reconciled on **2026-10-28**; see [the finding-by-finding response](FINANCIAL_ANALYSIS_REVIEW_RESPONSE.md). All eight findings have documented dispositions; the milestone is approved.

## Interfaces and use

- `analysis.analyze(records, as_of=..., frequency="annual", unit="USD", concepts=None, evidence=None)` is a pure interface returning JSON-ready periods, metric values/statuses, formulas and source inputs. It performs no I/O and does not mutate records or the process's Decimal context.
- `analysis_store.analyze_company(store, identifier, ...)` selects a locally observed snapshot, verifies its normalized/raw checksums, and reparses the existing raw responses. Extra income/balance tags already in CompanyFacts are extracted locally; no network request or collector refresh occurs. It also accepts an explicit `snapshot_id`.
- Both CLI commands call this same analysis interface. Future dashboard, valuation and agent callers can reuse it directly.

From the repository root:

```sh
export PYTHONPATH="$PWD/src"
python3 -m investor_ai analyze AAPL --frequency annual --limit 2
python3 -m investor_ai trends MSFT --frequency quarterly --limit 8
python3 -m investor_ai analyze AAPL --as-of 2026-10-27 --json --limit 0
python3 -m investor_ai analyze AAPL --concept revenue=RevenueFromContractWithCustomerExcludingAssessedTax --json
```

Global `--data-dir` and `--config` precede the subcommand. Neither analysis command needs `SEC_USER_AGENT`. `--limit 0` returns all periods. Text displays percentages/ratios to four decimal places, exposes underlying reasons in missing-value cells (such as `NA[unavailable_inputs; ambiguous_concept]`) and appends flags to available results. JSON retains arithmetic precision, full provenance, snapshot/source references and exclusion counts. Values are unscaled money strings, percentage points (`15` means 15%), or dimensionless ratios.

## Historical availability and restatements

`--as-of YYYY-MM-DD` means inclusive UTC day end. A timezone-aware ISO timestamp provides an intraday cutoff; naive timestamps are rejected. The default is the current UTC time.

The default policy is **observed by cutoff**, not filing-date reconstruction. The adapter requires the snapshot's completion time and every raw response's retrieval time to be no later than the cutoff. The pure interface also checks each row's retrieval/analysis-availability time, filing date, available acceptance timestamp, report end and fiscal-label evidence against that cutoff. There is no option to silently bypass these checks. Invalid availability/value metadata is excluded and counted.

Today's aggregate data does not prove what was publicly available years ago. A request before the first saved snapshot returns an explicit error, even if the filings themselves are older. To analyze FY2024 from the current data, use a current/eligible snapshot; that is analysis of FY2024 **using evidence observed by the requested cutoff**, not a certified 2024 backtest. Immutable earlier snapshots can support historical analyses from the collection history onward. Current local ticker associations are not a historical security master; prefer explicit CIKs for identity-sensitive historical work.

**Latest-known financial history** uses the latest eligible snapshot and revisions at a current cutoff. **Historical as-of analysis** restricts that same interface to evidence actually observed by the requested earlier cutoff. They answer different questions; an analysis of 2022 results with revisions observed in 2026 is not a 2022 decision simulation. The returned `as_of`, `availability_policy`, snapshot and input timestamps identify the distinction. The implemented historical policy is observed-evidence history, not a certified reconstruction of first market dissemination.

Selection is per metric and exact financial interval. The latest eligible value constrains every calculation. A complete original focus statement is a preferred representation when its numbers still match the latest known values. Unchanged later comparatives and partial amendments do not erase unaffected history. A calculation must find a common filing basis reproducing **all** latest selected inputs; a known changed value cannot fall back to an obsolete complete statement. A partial amendment may therefore leave reported fields available while making a margin or growth result unavailable (`incompatible_revision_vintages`). Selection histories identify corroborating and revised observations.

Annual balance selection prioritizes 10-K/10-K/A evidence at the exact year end, independently of the income statement's preferred accession. An unchanged interim comparative cannot displace a complete annual statement. A changed later interim value is explicitly `unreconciled_interim_revision`, unless an exact, source-reviewed `balance_revision` link supports it. That link authorizes only the specified metric/end/value; unchanged accompanying terms can corroborate the revised statement, but unrelated changed balances remain blocked. Amendments retain their actual form and provenance; form-based annual priority does not independently certify an audit opinion or make an interim revision audited.

Same-day ordering uses acceptance timestamps, never accession-string order or invented midnight timestamps. An acceptance known by the cutoff applies to the entire accession, including rows without their own timestamp; inconsistent known acceptances exclude that accession. Metadata first observed after the cutoff cannot resolve an earlier view. When acceptance is missing, actual response observation and completed-snapshot times still provide conservative availability bounds: a body received at 09:00 can support a 12:00 analysis, but not an 08:00 analysis. Filing date alone is never sufficient.

Revised values remain separate source observations. Latest eligible restatements can change results only after they are observed; future-filed or future-observed revisions are excluded. Different values in the same concept/unit/interval/cohort produce `conflicting_values`. Multiple accounting concepts produce `ambiguous_concept`, even if their numbers coincide. An explicit `--concept METRIC=CONCEPT` can resolve the definition; it does not fall back when that concept is missing.

## Calculations

All arithmetic uses `Decimal` under a local 34-digit context. No binary float is introduced. Ratios use a strictly positive denominator. Negative net/gross/operating income and negative operating/free cash flow remain valid reported outcomes.

| Metric | Formula | Important convention |
| --- | --- | --- |
| Revenue growth YoY (%) | `(revenue - prior revenue) / prior revenue × 100` | Positive prior revenue required; same concept/unit and comparable fiscal interval. |
| Earnings growth YoY (%) | `(net income - prior net income) / prior net income × 100` | Earnings means net income, not EPS. Prior zero/negative income yields no percentage; the absolute change remains available. A current loss can yield growth below −100%, flagged explicitly. |
| Revenue/earnings change YoY | `current - prior year` | Monetary change; valid with a negative prior net income. |
| Net margin (%) | `net income / revenue × 100` | Revenue must be positive. |
| Gross margin (%) | `GrossProfit / revenue × 100` | Missing gross-profit tag remains missing; no invented cost-of-sales mapping. |
| Operating margin (%) | `OperatingIncomeLoss / revenue × 100` | Reported operating income; no EBITDA adjustment. |
| Free cash flow | `operating cash flow - PP&E payments` | Capex is positive gross `PaymentsToAcquirePropertyPlantAndEquipment`. Negative payments are rejected rather than silently sign-flipped. |
| Liabilities/assets | `Liabilities / Assets` | Broad balance-sheet leverage, not interest-bearing debt/assets. |
| Current ratio | `AssetsCurrent / LiabilitiesCurrent` | Positive current liabilities; source balances must be nonnegative. |
| Cash/current liabilities | `CashAndCashEquivalentsAtCarryingValue / LiabilitiesCurrent` | Cash-only liquidity proxy; excludes separately reported marketable securities. |
| Noncurrent debt/equity | `LongTermDebtNoncurrent / StockholdersEquity` | Selected debt measure, **not total debt/equity**. Excludes current debt/commercial paper and separately classified lease obligations; zero/negative equity yields no ratio. |

FCF is a defined analytical proxy, not a GAAP subtotal or a claim of discretionary cash after debt service and other obligations. See the [SEC financial-statement guide](https://www.sec.gov/about/reports-publications/investorpubsbegfinstmtguide) for statement/operating-margin context and an [SEC staff comment on FCF definition](https://www.sec.gov/Archives/edgar/data/1304280/000130428022000014/filename1.htm). No investment-performance or profitability prediction follows from these metrics.

YoY growth searches available historical statements for a shared filing containing both latest known values. For example, a 2025 filing repeating unchanged 2023 results without 2022 results does not destroy the usable 2023/2022 pair in the 2023 filing. A later changed 2023 value does invalidate that older pair until a coherent comparison is available. Annual comparisons require consecutive known fiscal years; quarterly comparisons require the same known fiscal quarter one year earlier. Exact intervals, a 330–400-day separation and a maximum 14-day duration difference guard the comparison. Different lengths are flagged, not normalized for an extra fiscal week. Missing compatible comparatives and ambiguous calendars remain unavailable.

## Standalone quarters from cumulative data

Reported three-month values are used directly. A derived Q2/Q3/Q4 is permitted only when both cumulative observations:

1. Are observed by the analysis cutoff and use the same company, US-GAAP concept and unit.
2. Have the same fiscal-year start and a known, matching fiscal year.
3. End at consecutive, evidenced fiscal reporting quarters: Q2−Q1, Q3−Q2, or FY−Q3.
4. Have cumulative lengths consistent with those quarter labels (within 20 days of 91 days per quarter) and leave a 70–110-day residual interval.
5. Match the latest known reported values for both exact cumulative intervals, including known revisions whose fiscal labels are unresolved.
6. Come from the same accession, or, for operating cash flow and capex only, have an exact reviewed `cashflow_comparability` link. Across accessions, the shorter-period filing must precede the longer-period filing; same-day ordering requires both acceptance times.

The residual starts the day after the shorter interval ends. Values are subtracted only once. Both leaf observations, accessions, exact periods, formula and review evidence are returned. Agreeing directly reported quarters take precedence when the selected basis supports them; disagreements within a competing latest basis are conflicts. A 53-week year can produce a 14-week Q4 without shifting dates or fiscal years.

These are interval/fiscal-context guards, not a general transition-calendar model. Unknown fiscal years, changed starts, currencies/concepts, conflicting values and unsupported durations block derivation. CompanyFacts and adjacent dates alone cannot establish an unchanged accounting basis. Without reviewed evidence, cross-accession conversions remain unavailable with their specific diagnostic. Later restatements invalidate stale links; reconciled new pairs need their own exact evidence. A later revision to the shorter interval cannot simply be subtracted from an earlier, unreconciled longer interval.

### Reviewed evidence registry

The offline adapter optionally loads ignored `DATA_DIR/analysis_evidence.json`, or an explicit `--evidence PATH`. It never downloads or generates accounting reviews. The registry has `schema_version: 1` and an `entries` list. Each entry contains:

- A unique `id`, `kind` (`cashflow_comparability` or `balance_revision`), timezone-aware `verified_at`, and a substantive accounting `basis` explanation.
- Exactly two `facts`, each an exact `analysis_evidence.fact_reference(row)`: company, metric, taxonomy/concept, unit, interval, value, accession and fiscal year/period.
- `checks`: `original_values_verified`, `relevant_revisions_reviewed`, `accounting_basis_consistent`. These record analyst review, not automatically verified accounting assertions.
- `documents` for both accessions: official SEC original-filing `url`, local store-relative `path`, `sha256` and original `retrieved_at`. The adapter checks containment and actual bytes against hashes before calculation. Missing/corrupt eligible documents fail explicitly.

Reviews and original-document retrievals must all precede the analysis cutoff. Future reviews cannot authorize historical results; duplicate IDs and malformed evidence are rejected with diagnostics. Period/concept/unit/latest-revision checks remain mandatory regardless of an entry's assertions. The pure API validates scope/structure/timing; callers of that API are responsible for verifying source bytes, as the storage adapter does. Hashes establish file identity, not financial comparability. Manual documentary judgment remains a limitation, and generic reclassification language does not by itself prove that every comparative is unchanged.

## Results and missing-data behavior

Each metric contains `value`, `unit`, `status`, `formula`, `classification`, `flags`, `inputs`, `selection_evidence` and `comparability_evidence`. Inputs retain original record IDs, CIKs, concepts, units, periods, filing identifiers/links, filing/acceptance/retrieval times and fiscal evidence. Financial source inputs remain reported observations; ratios, growth, FCF and cumulative differences are derived. The adapter records the raw source references, snapshot ID, evidence-file path and extractor version. Analysis has its own version (`2`); collector parser version remains `3`.

Notable statuses/flags include `missing`, `missing_concept`, `ambiguous_concept`, `conflicting_values`, `nonpositive_denominator`, `negative_balance`, `negative_capex_payment`, `ambiguous_fiscal_period`, `missing_comparable_period`, `incompatible_revision_vintages`, `unreconciled_interim_revision`, `cumulative_revision_not_reconciled` and `cross_filing_comparability_evidence_required`. Derived metrics blocked by inputs return `unavailable_inputs` with underlying reasons. Unresolved fiscal fields and selection statuses are visible. Missing values, including debt, never become zero; an explicitly reported zero remains zero. Analysis does not overwrite or persist altered source observations.

## Hand calculations and regression tests

The invented annual example uses revenue 100→120, net income 10→18, OCF 30, capex 8, gross profit 54, operating income 24, current assets/liabilities 90/45, cash 15 and noncurrent debt/equity 40/100. Expected: revenue growth 20%, net-income growth 80%, net margin 15%, gross margin 45%, operating margin 20%, FCF 22, current ratio 2, cash/current liabilities 1/3 and noncurrent debt/equity 0.4.

Cumulative examples independently calculate Q2 OCF `70−30=40`, capex `15−6=9`, FCF `40−9=31`; Q3 OCF `90−70=20`, capex `30−15=15`, FCF 5; and Q4 OCF `130−90=40`, capex `35−20=15`, FCF 25. Another case verifies a 98-day Q4 in a 371-day fiscal year. Negative/zero bases, restatements, conflicting concepts/values, future observations, availability gaps, same-day ordering, historical snapshot selection and offline CLI paths are covered.

On 2026-10-28, **122 tests passed**: all 52 existing collector regressions, 39 initial analysis tests and 31 review regressions. New cases cover consecutive annual/quarterly filings, partial amendments, audited annual balance priority, reviewed interim revisions, Q2/Q3/Q4 cross-accession conversion, stale/reconciled cumulative revisions, unresolved labels, intraday cutoffs, evidence integrity, distressed/unclassified balances, CLI diagnostic flags and deterministic ordering. Confirmed defects were first reproduced with failing tests. Command: `PYTHONPATH=src python3 -m unittest discover -s tests -v`. Runtime and tests require no extra dependencies.

## Original SEC filing validation

Initial validation reused originals from Milestone 1 for **52 current-period input checks**, plus eight prior-period growth-input checks. A separate inline-XBRL parser verified entity, concept, unit, exact interval and displayed scale/sign. `Fraction` arithmetic independently checked **48 formula results** (12 per report) to an absolute tolerance of `1e-28`; monetary FCF checks also matched exact expected integers. The original verifier was rerun after the review corrections.

Validation selected each named original filing's cohort explicitly, rather than assuming the current default latest-vintage result represents that original report. All input retrieval times remained bounded by the validation cutoff. Derived results are our calculations, not metrics claimed to have been published by the issuers.

| Filing | Revenue growth YoY | Net-income growth YoY | Net margin | FCF, USD millions | Current ratio |
| --- | ---: | ---: | ---: | ---: | ---: |
| [Apple FY2024 10-K](https://www.sec.gov/Archives/edgar/data/320193/000032019324000123/aapl-20240928.htm) | 2.021994% | −3.359967% | 23.971256% | 108,807 | 0.867313 |
| [Apple FY2025 Q1 10-Q](https://www.sec.gov/Archives/edgar/data/320193/000032019325000008/aapl-20241228.htm) | 3.951495% | 7.117585% | 29.227675% | 26,995 | 0.922938 |
| [Microsoft FY2024 10-K](https://www.sec.gov/Archives/edgar/data/789019/000095017024087843/msft-20240630.htm) | 15.669962% | 21.800417% | 35.955973% | 74,071 | 1.274955 |
| [Microsoft FY2025 Q1 10-Q](https://www.sec.gov/Archives/edgar/data/789019/000095017024118967/msft-20240930.htm) | 16.044730% | 10.659010% | 37.610734% | 19,257 | 1.301441 |

Gross/operating margins, balance ratios and monetary growth changes were also checked. Provenance, matched contexts and computed values are saved under ignored `data/sec/verification/analysis_validation.json`; a one-off reproducible verifier is alongside it at `verify_analysis.py`. DNS/connect attempts were forbidden throughout validation. These real-company inputs are not added as downloaded fixtures; versioned tests remain synthetic.

The review validation additionally retrieved eight original FY2024 filings successfully (HTTP 200, configured contact identity, normal SEC client rate limit). It checked **16 original cumulative inputs**, **18 derived quarterly values** and **four directly reported Microsoft quarterly controls**, then exercised both offline adapters with document-hash verification and network attempts forbidden. See [the review response](FINANCIAL_ANALYSIS_REVIEW_RESPONSE.md) for all eight filing links, numerical Q2/Q3/Q4 results, evidence qualifications and unavailable calculations. Local source manifests, HTML, reviewed registry and verifier remain ignored under `data/sec/`; no real downloaded fixtures are versioned.

## Remaining limits

The collector's [existing limitations](SEC_COLLECTOR_REVIEW_RESPONSE.md) remain in force. This is a deliberately narrow US-GAAP concept set, with no custom-tag/segment/IFRS mapping or currency conversion. Ratios are not universally appropriate across industries. Noncurrent debt is a subset, capex is gross PP&E cash payments, and no lease reconciliation is implied. Complete fiscal-transition calendars, certified historical dissemination and cross-filing cumulative comparability are not inferred. A missing, ambiguous or unavailable result remains explicit. No forecast, valuation, investment recommendation, AI thesis, portfolio, execution or dashboard functionality is implemented.
