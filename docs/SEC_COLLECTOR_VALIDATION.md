# SEC collector milestone validation

This records the initial parser-version-1 milestone validation. The subsequent independent review corrections, final parser version 3, 52-test suite and targeted live revalidation are documented in [SEC_COLLECTOR_REVIEW_RESPONSE.md](SEC_COLLECTOR_REVIEW_RESPONSE.md). Initial snapshot IDs and results below remain historical evidence.

Verified on **2026-10-27**, using Python 3.12.3 and the user's configured real SEC contact identity. The identity was passed to the existing CLI in its child environment, without printing it or adding it to versioned configuration. Host execution was used because the sandbox launcher fails even for network-free commands. No application code, transport replacement, proxy workaround or access-control bypass was needed for this validation.

## Live collection and storage

Command: `PYTHONPATH=src python3 -m investor_ai collect AAPL MSFT --filing-history`, with `SEC_USER_AGENT` set in that process.

| Company | CIK | Result | Observations | Retrieved at (UTC) | Snapshot ID |
| --- | --- | --- | ---: | --- | --- |
| AAPL | 0000320193 | success | 1,433 | 2026-10-27 16:32:39.071415 | `157cc2bd3d274d72b57fe338a7ccc677` |
| MSFT | 0000789019 | success | 1,625 | 2026-10-27 16:32:41.858047 | `b285940632df4a5b9ed442fccff3c6fd` |

Both snapshots contain all seven selected metrics, with no missing metrics or emitted quality flags. Apple retained three API response references (CompanyFacts, current submissions and one older submissions file); Microsoft retained four (including two older files). All referenced raw objects and normalized records passed checksum checks. Data and verification evidence are under Git-ignored `data/sec/`.

## Reconciliation against original filings

All **28 comparisons passed**. Each check matched the selected concept, reporting entity, start/end or instant, USD unit and value to an entity-wide inline XBRL fact in the original filing. The verifier converted displayed numbers using the inline `scale` and sign attributes; each selected fact used scale 6. It did not compare CompanyFacts with another CompanyFacts response.

Sources:

1. [Apple FY2024 10-K](https://www.sec.gov/Archives/edgar/data/320193/000032019324000123/aapl-20240928.htm), accession `0000320193-24-000123`, filed 2024-11-01. Annual flows: 2023-10-01 through 2024-09-28; balances at 2024-09-28.
2. [Apple FY2025 Q1 10-Q](https://www.sec.gov/Archives/edgar/data/320193/000032019325000008/aapl-20241228.htm), accession `0000320193-25-000008`, filed 2025-01-31. Quarterly flows: 2024-09-29 through 2024-12-28; balances at 2024-12-28.
3. [Microsoft FY2024 10-K](https://www.sec.gov/Archives/edgar/data/789019/000095017024087843/msft-20240630.htm), accession `0000950170-24-087843`, filed 2024-07-30. Annual flows: 2023-07-01 through 2024-06-30; balances at 2024-06-30.
4. [Microsoft FY2025 Q1 10-Q](https://www.sec.gov/Archives/edgar/data/789019/000095017024118967/msft-20240930.htm), accession `0000950170-24-118967`, filed 2024-10-30. Quarterly flows: 2024-07-01 through 2024-09-30; balances at 2024-09-30.

Values below are **USD millions**, matching the filings' display scale. Stored values are unscaled dollar amounts represented as decimal strings.

| Metric | Apple FY2024 | Apple FY2025 Q1 | Microsoft FY2024 | Microsoft FY2025 Q1 |
| --- | ---: | ---: | ---: | ---: |
| Revenue | 391,035 | 124,300 | 245,122 | 65,585 |
| Net income | 93,736 | 36,330 | 88,136 | 24,667 |
| Operating cash flow | 118,254 | 29,935 | 118,548 | 34,180 |
| Capital expenditure | 9,447 | 2,940 | 44,477 | 14,923 |
| Assets | 364,980 | 344,085 | 512,163 | 523,013 |
| Liabilities | 308,030 | 277,327 | 243,686 | 235,290 |
| Cash and cash equivalents | 29,943 | 30,299 | 18,315 | 20,840 |

Capex is the reported `PaymentsToAcquirePropertyPlantAndEquipment` amount, retained as a positive payment magnitude. Cash is `CashAndCashEquivalentsAtCarryingValue`, not cash plus marketable securities. Balance-sheet amounts are instants, rather than annual or quarterly flows.

The downloaded filings, per-document SHA-256 hashes, matched inline contexts/scales, normalized record IDs and reconciliation results are retained in `data/sec/verification/filing_reconciliation.json`. The one-off helper is saved at `data/sec/verification/verify_filings.py`; it can reuse the saved HTML evidence. It is a bounded verification tool, not part of the collector's application API.

## Periods, provenance and revisions

| Company | Annual flows | Quarterly flows | Cumulative/year-to-date flows | Instants | Groups with differing reported values |
| --- | ---: | ---: | ---: | ---: | ---: |
| AAPL | 201 | 468 | 246 | 518 | 20 |
| MSFT | 227 | 600 | 256 | 542 | 22 |

The final column groups the same concept, unit and start/end across filing observations; different values retain separate accessions, filing dates and record IDs. For example, Microsoft's assets at 2015-06-30 remain USD 176,223 million in accession `0001193125-15-272806` (filed 2015-07-31), and USD 174,472 million in `0001193125-16-662209` (filed 2016-07-28). Those two observations were checked against the saved CompanyFacts evidence, not independently reconciled against both original historical filings. Their coexistence does not itself establish the accounting reason for the change.

Every stored observation matched its original raw source observation, with unique record IDs and preserved filing/acceptance/retrieval metadata. Replaying each snapshot's permitted raw responses through the parser reproduced the normalized records exactly. Assigned fiscal labels always matched the filing's report-end context; comparative periods were not relabeled using a later filing's fiscal focus. Apple's quarter ending in calendar December 2024 correctly carries fiscal year 2025, quarter 1. Cumulative flows remain separate from standalone quarters; no quarter was derived by subtraction.

## Cache and automated checks

- Cached collection for both companies returned `cached`, with the snapshot IDs and original retrieval timestamps unchanged.
- CLI `inspect --metric revenue --period annual --json --limit 0` returned 60 saved observations for each company.
- During those operations, `SecClient.get` was replaced with a failing guard and DNS/socket connection audit events were forbidden. There were **zero SEC request calls and zero DNS/connect events**.
- The full automated suite passed: **29 tests**, using only synthetic local fixtures. Command: `PYTHONPATH=src python3 -m unittest discover -s tests -v`.
- Local verification summaries are in `data/sec/verification/offline_checks.json`. Downloaded evidence, `.env`, caches and generated build artifacts remain ignored. No commit or push was performed.

## Limits

This is a sample reconciliation, not an exhaustive accounting or historical-availability certification. Duration labels remain heuristic; custom taxonomy/dimensional facts and IFRS mapping remain unsupported. Today's CompanyFacts may omit earlier evidence, and acceptance dates do not certify first public availability. Snapshots preserve what was retrieved from now onward. Source access can change, and independent processes using different stores do not share a request budget. The POSIX store lock currently limits native Windows support. Phase 2 remains current; no dashboard or further feature implementation is included.
