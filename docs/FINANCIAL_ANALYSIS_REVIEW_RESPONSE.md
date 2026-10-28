# Financial analysis review and correction record

Original-filing validation performed **2026-10-27**; complete Gemini review reconciled and checks rerun **2026-10-28**. Milestone 2 was approved for finalization by the user on **2026-10-28**.

## Review source and assessment

The complete **Independent Technical and Financial Correctness Review: Milestone 2**, dated 2026-10-27, was supplied in the user's message on **2026-10-28**. All eight numbered findings, the detailed analysis and all five remediation recommendations were checked independently. Its line numbers refer to the earlier 91-test implementation, before the corrections below. The repository's `geminireview.md` remains the earlier Milestone 1 collector review; it and the collector review response have not been replaced.

| Gemini finding | Independent finding and disposition |
| --- | --- |
| **1. Historical YoY growth lost** | **Accepted, reproduced and fixed.** `_pick`, `_statement`, `_align` and `_growth` now preserve older complete comparisons when later repeats are unchanged. Annual and quarterly multi-filing regressions pass. A later changed value still invalidates stale fallback. The exact loss pattern depends on comparative coverage; the review's universal wording is stronger than the evidence. |
| **2. Same-accession CF prohibition** | **Accepted as a real coverage limitation; categorical claims rejected.** `_derive_quarters` now accepts exact reviewed cross-accession CFO/capex pairs with compatible periods, concepts/units, reconciled revisions and cutoffs. Both sources and review evidence are returned. Microsoft directly reports Q2/Q3 cash flows, disproving the claim that real filers never report standalone interim cash flows. No universal 75% missing-data rate is established. |
| **3. Sparse comparative filings replace full statements** | **Accepted, reproduced and fixed.** Selection is per metric; unaffected reported fields survive partial disclosures. A changed partial amendment cannot silently combine with older terms for a ratio. Such calculations stay `incompatible_revision_vintages` until a shared current basis is evidenced. |
| **4. Interim balances supersede annual 10-K evidence** | **Accepted, reproduced and fixed.** `_pick` prioritizes annual 10-K/10-K/A evidence. Unchanged interim repeats preserve annual statements; changed interim values need exact reviewed revision links or return `unreconciled_interim_revision`. Legitimate revisions are not silently discarded. Form priority does not independently certify every fact's audit coverage. |
| **5. Missing acceptance permits intraday leakage** | **Not reproduced as stated with genuine observation times.** An afternoon filing cannot be in a response actually retrieved before a morning cutoff. Both raw observation and completed snapshot must already be eligible. A separate confirmed issue was fixed: an acceptance known on one row now governs every row of that accession. Future-observed metadata cannot resolve an earlier view. Blanket rejection of already-observed data without acceptance is unnecessary; no midnight assumption is used. |
| **6. Missing debt should yield zero** | **Rejected as an inference; coverage limit acknowledged.** Missing `LongTermDebtNoncurrent` does not prove zero debt, even with positive equity and reported assets/liabilities. The existing conservative behavior remains regression-tested. The ratio is a noncurrent-debt subset, not total debt. No configurable zero-debt assumption was added. |
| **7. Negative-equity/distress and Q2–Q4 verification gaps** | **Partly accepted.** Original real-filing checks covered annual/Q1 only. Real Q2/Q3/Q4 checks now pass. Synthetic losses and negative-equity tests already existed (`test_loss_margins_are_valid_but_negative_growth_base_is_not`, `test_zero_missing_and_negative_balance_are_distinct`), so their claimed absence is incorrect. Added a combined distressed/unclassified-balance scenario and consecutive quarterly history. Real distressed/restated-company validation and independent direct Q4 controls remain deferred. |
| **8. Text CLI hides diagnostic flags** | **Accepted, reproduced and fixed on 2026-10-28.** Summary diagnostics alone did not expose reasons in individual cells. `_display_amount` now renders, for example, `NA[unavailable_inputs; ambiguous_concept]`, and appends flags to available values, including loss transitions and different interval lengths. Integration tests reproduce the failure through both `analyze` and `trends` before correction. JSON remains unchanged. |

### Remediation recommendations and other observations

1. **Primary/complete statement preference and coherent growth fallback:** adopted with a latest-known-value guard. Choosing the primary filing alone would incorrectly ignore later restatements.
2. **Form-aware annual balances:** adopted, while allowing specifically reviewed interim revisions rather than unconditionally discarding them.
3. **Cross-filing conversion when neither filing is amended/restated:** amendment absence alone is insufficient evidence of accounting compatibility. Exact documentary review is required. Conversely, a reconciled revised pair can be usable; an amendment is not a blanket prohibition.
4. **Optional missing-debt-as-zero flag:** rejected without affirmative financial evidence. Generic balance totals do not establish zero interest-bearing debt.
5. **Require acceptance for every intraday cutoff:** rejected as an unnecessary restriction when actual observation already provides a conservative bound. Known acceptance metadata is still enforced across the accession.

The review's positive formula, Decimal-context, immutability, provenance and snapshot-cutoff observations are supported by the existing independent formula checks and regression tests. Its suggested need for diluted shares/per-share metrics concerns a later valuation capability, not a defect in the approved Milestone 2 scope; those features were not added. Automatic interpretation of policy/reclassification text, certified market point-in-time history and full transition-calendar modeling remain deferred.

Additional defects independently reproduced before correction: a changed interim balance filed later on the **same day** could silently leave the old annual amount; a cumulative revision with unresolved fiscal labels could be ignored by derivation. Both are now guarded. Evidence validation also rejects duplicate IDs independently of input order and handles malformed timestamps with diagnostics.

## Original SEC filing validation

Eight original filing HTML responses returned **HTTP 200**, using the configured real contact identity, ordinary SEC requests and the existing two-request/second budget. Raw originals, SHA-256 hashes, retrieval times, inline contexts, exact endpoint references and review records are retained only in ignored local storage. No account, paid source or access-control workaround was used.

All values below are **USD millions**. A separate inline-XBRL parser checked original consolidated, nondimensional contexts, entity CIK, exact periods, concepts, units, displayed scale/sign and values. The selected endpoints also match the latest saved reported values for those intervals.

| Company / FY2024 endpoint | Original filing | Cumulative CFO | Cumulative cash PP&E payments |
| --- | --- | ---: | ---: |
| AAPL Q1, 2023-12-30 | [10-Q, 0000320193-24-000006](https://www.sec.gov/Archives/edgar/data/320193/000032019324000006/aapl-20231230.htm) | 39,895 | 2,392 |
| AAPL Q2, 2024-03-30 | [10-Q, 0000320193-24-000069](https://www.sec.gov/Archives/edgar/data/320193/000032019324000069/aapl-20240330.htm) | 62,585 | 4,388 |
| AAPL Q3, 2024-06-29 | [10-Q, 0000320193-24-000081](https://www.sec.gov/Archives/edgar/data/320193/000032019324000081/aapl-20240629.htm) | 91,443 | 6,539 |
| AAPL FY, 2024-09-28 | [10-K, 0000320193-24-000123](https://www.sec.gov/Archives/edgar/data/320193/000032019324000123/aapl-20240928.htm) | 118,254 | 9,447 |
| MSFT Q1, 2023-09-30 | [10-Q, 0000950170-23-054855](https://www.sec.gov/Archives/edgar/data/789019/000095017023054855/msft-20230930.htm) | 30,583 | 9,917 |
| MSFT Q2, 2023-12-31 | [10-Q, 0000950170-24-008814](https://www.sec.gov/Archives/edgar/data/789019/000095017024008814/msft-20231231.htm) | 49,436 | 19,652 |
| MSFT Q3, 2024-03-31 | [10-Q, 0000950170-24-048288](https://www.sec.gov/Archives/edgar/data/789019/000095017024048288/msft-20240331.htm) | 81,353 | 30,604 |
| MSFT FY, 2024-06-30 | [10-K, 0000950170-24-087843](https://www.sec.gov/Archives/edgar/data/789019/000095017024087843/msft-20240630.htm) | 118,548 | 44,477 |

Apple Note 1 describes a 52-week FY2024 and GAAP consolidated statements. Its Q1/annual notes contain generic prior-period reclassification language; this was **not** treated as proof that every comparative is unchanged. Review was limited to the specified current-FY cash-flow totals, consistent concepts, exact intervals, subsequent saved versions and accompanying disclosures. Microsoft's Note 1 expressly states its presentation recasts did not affect consolidated cash-flow statements. These are scoped analyst judgments, not an automated or certified restatement audit.

| Company / quarter | CFO difference | Capex difference | Derived FCF | Independent direct-quarter control |
| --- | ---: | ---: | ---: | --- |
| AAPL Q2 | 22,690 | 1,996 | **20,694** | Unavailable in the selected YTD cash-flow statement |
| AAPL Q3 | 28,858 | 2,151 | **26,707** | Unavailable in the selected YTD cash-flow statement |
| AAPL Q4 | 26,811 | 2,908 | **23,903** | Derived FY minus Q3; no direct annual-statement quarter control |
| MSFT Q2 | 18,853 | 9,735 | **9,118** | Both differences match reported three-month inputs |
| MSFT Q3 | 31,917 | 10,952 | **20,965** | Both differences match reported three-month inputs |
| MSFT Q4 | 37,195 | 13,873 | **23,322** | Derived FY minus Q3; no direct annual-statement quarter control |

For example, AAPL Q2 CFO is `62,585−39,895=22,690`; capex is `4,388−2,392=1,996`; FCF is `22,690−1,996=20,694`. All four quarters reconcile to annual FCF of **108,807** for AAPL and **74,071** for MSFT. This reconciliation is an arithmetic consistency check, not independent proof of quarterly accounting comparability. Microsoft direct Q2/Q3 observations were removed only from temporary in-memory validation input to exercise conversion, then compared with separately verified original three-month controls. Default analysis continues to use directly reported quarters when appropriate.

Local reproduction records: `data/sec/verification/analysis_review_sources.json`, `analysis_review_validation.json`, `verify_analysis_review.py` and `data/sec/analysis_evidence.json`. Re-run with `PYTHONPATH=src python3 data/sec/verification/verify_analysis_review.py`; it forbids DNS/connect operations. The original four-report verifier `verify_analysis.py` also passes. Neither verifier changes collector snapshots. Review times are current observation times, not backdated to the original filing dates.

## Tests, diagnostics and unresolved limits

`PYTHONPATH=src python3 -m unittest discover -s tests -v`: **122 tests pass** (52 collector, 70 analysis; 31 review regressions). The final four tests add consecutive quarterly history, distressed/unclassified balances and diagnostic rendering through both CLI commands. Original-file checks: 52 initial current inputs plus eight growth comparatives, 48 independent formula checks; additional checks: 16 cumulative endpoints, 18 derived values and four direct-quarter controls. Both original-filing verifiers were rerun offline on 2026-10-28 with persistence disabled to preserve the earlier review timestamps and saved evidence. Both companies' offline adapters verify source hashes with network access forbidden. Text CLI exposes reasons and flags in each relevant cell; JSON preserves complete inputs, selection history, evidence and cutoff exclusions.

Without an eligible review registry, cross-filing AAPL Q2/Q3 and both companies' Q4 cash-flow derivations remain unavailable. A review cannot authorize a cutoff before that review was observed. Unreconciled restatements, changed concepts/starts, unresolved fiscal calendars and missing debt remain explicit gaps. The reviewed Q4 cash-flow pairs do not authorize Q4 revenue/net-income derivation across filings; those values may remain missing. Real distressed/revised-company examples and independent direct Q4 controls were not validated here; synthetic regressions exercise those guards. All eight findings in the supplied complete Gemini review are reconciled above; Milestone 2 is complete and approved; these limitations remain in force.
