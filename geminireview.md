Here is an independent technical and financial correctness review of the SEC collector implementation.
──────
Independent Review: SEC EDGAR Financial Data Collector

## 1. Executive Summary

The SEC EDGAR collector implementation exhibits clean modularity, disciplined provenance tracking, defensive network policies, and content-addressed storage.
However, our review identified critical financial interpretation issues, unprotected storage race conditions, and provenance indexing traps that affect financial
correctness and long-term data integrity:

1. Restatements & Comparative Revisions Stripped of Fiscal Labels (Financial Correctness Risk): Subsequent filings reporting revised comparative figures have
   their fiscal_year and fiscal_quarter stripped to None, making restatements invisible to fiscal period queries and introducing severe lookahead bias risks for
   downstream consumers.
2. Instant Facts in 10-K Assigned fiscal_quarter: 4 (Financial Interpretation Bug): Annual balance sheet facts (Assets, Liabilities, Cash) in 10-K filings are
   erroneously assigned fiscal_quarter = 4.
3. Cumulative Cash Flows Assigned Quarter Numbers (Financial Interpretation Bug): 6-month and 9-month cumulative cash flows receive fiscal_quarter values (2 and
   3), creating ambiguity with standalone 3-month quarterly metrics.
4. Non-Atomic Raw Writes in save_response (Data Loss / Store Corruption Bug): Raw responses are written directly to disk via path.open("xb") without atomic rename,
   leaving corrupt/partial files on interruption that permanently brick subsequent runs.
5. Brittle 52/53-Week Date Alignment: Requiring strict string equality between the SEC EDGAR header reportDate and the fact end date drops fiscal year/quarter
   labels whenever an issuer's cover page period differs by 1–2 days from its inline XBRL facts.
   ──────

## 2. Findings by Severity

### Critical & High Severity

#### Finding 1.1: Restatements and comparative revisions are stripped of fiscal_year and fiscal_quarter, creating restatement invisibility and lookahead risks

• Type: Confirmed Financial Correctness & Provenance Risk
• Location: financials.py:113-116
• Detail:
focus_matches = (filing.get("reportDate") == end
and filing.get("form", "").removesuffix("/A") in {"10-K", "10-Q"})
fiscal_year = observation.get("fy") if focus_matches else None
fp = observation.get("fp") if focus_matches else None
When an issuer reports revised or restated comparative figures in a subsequent 10-K (e.g., FY2024 10-K restating FY2023 Revenue), the filing's reportDate is 2024-
09-28 while the fact's end is 2023-09-30. Because reportDate != end, focus_matches evaluates to False. Consequently, fiscal_year and fiscal_quarter are set to
None.
• Financial Impact:
Any downstream query, automated research agent, or valuation model filtering by fiscal_year == 2023 will only see the original, unrevised observation from the
FY2023 10-K. The restated figure from the FY2024 10-K cannot be found by fiscal year. While this design avoids trusting SEC container fy/fp tags on comparative
rows (asserted in test_collector.py:69), it creates a severe financial trap: restatements are orphaned from their fiscal period and become invisible to standard
financial analysis.
──────

#### Finding 1.2: Non-atomic raw file writes in Store.save_response can cause permanent store corruption

• Type: Confirmed Technical Bug
• Location: storage.py:52-58
• Detail:
try:
with path.open("xb") as handle:
handle.write(response.body)
except FileExistsError:
if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
raise SecError(f"Raw object checksum failed: {relative}")
Unlike write_json (which uses a temporary file and os.replace on storage.py:21-24), save_response creates path with path.open("xb") and writes directly. If the
process is interrupted (SIGINT, SIGTERM, power failure, or disk full), a partial file is left at path.
• Technical Impact:
On the next collection, path.open("xb") raises FileExistsError. In the except block, it hashes the truncated file, finds that the hash does not match digest, and
raises SecError("Raw object checksum failed"). The store is permanently broken for that response and cannot self-heal without manual file deletion.
──────

#### Finding 1.3: Annual balance sheet facts in 10-K are assigned fiscal_quarter = 4

• Type: Confirmed Financial Interpretation Bug
• Location: financials.py:117-119
• Detail:
fiscal_quarter = {"Q1": 1, "Q2": 2, "Q3": 3, "Q4": 4, "FY": 4}.get(fp)
if kind == "annual":
fiscal_quarter = None
In a 10-K, fp is "FY". Line 117 maps "FY" to 4. Line 118 only resets fiscal_quarter = None if kind == "annual". However, balance-sheet facts (Assets, Liabilities,
Cash) have kind == "instant", not "annual".
• Financial Impact:
All annual balance sheet facts reported in 10-K filings are labeled with fiscal_quarter: 4. In accounting, a fiscal year-end balance sheet represents the annual
position (FY), not the fourth-quarter interim balance sheet. A query for fiscal_quarter == 4 will return the full fiscal year-end balance sheet as if it were an
interim Q4 snapshot.
──────

### Medium Severity

#### Finding 2.1: Cumulative cash flows and income statement metrics receive quarter numbers

• Type: Confirmed Financial Interpretation Risk
• Location: financials.py:117-119
• Detail:
In a Q2 or Q3 10-Q filing, fp is "Q2" or "Q3". Cumulative 6-month and 9-month cash flows have kind == "year_to_date". Because kind != "annual", fiscal_quarter
remains 2 or 3 (explicitly asserted in test_collector.py:80).
• Financial Impact:
A 9-month cumulative cash flow is not the 3rd quarter flow. Assigning fiscal_quarter = 3 to a 9-month flow causes confusion: any model grouping or filtering by
(fiscal_year, fiscal_quarter) will mix 3-month standalone flows and 9-month cumulative flows, leading to severe double-counting unless filtered by period_type.
──────

#### Finding 2.2: Asymmetric classification of Q1 cash flows vs. Q2/Q3 cash flows prevents uniform querying

• Type: Confirmed Financial Data Modeling Risk
• Location: financials.py:58-64
• Detail:
Under US GAAP, cash flows are reported cumulatively (3M in Q1, 6M in Q2, 9M in Q3, 12M in 10-K).
• Q1 cash flow (~91 days) is classified as period_type: "quarterly".
• Q2 cash flow (~182 days) is classified as period_type: "year_to_date".
• Q3 cash flow (~273 days) is classified as period_type: "year_to_date".
• FY cash flow (~365 days) is classified as period_type: "annual".
• Financial Impact:
Querying --period quarterly returns only Q1 cash flows; querying --period year_to_date returns only Q2 and Q3 cash flows (excluding Q1). Neither query yields a
continuous time series. There is no record metadata indicating that Q1 is both a quarterly period and the initial year-to-date accumulation.
──────

#### Finding 2.3: Brittle string equality reportDate == end drops fiscal labels on 52/53-week filers

• Type: Confirmed Financial Correctness Risk
• Location: financials.py:113
• Detail:
In SEC EDGAR submissions, reportDate is parsed from the cover page header <period>. Many registrants (notably retail, tech, and manufacturing companies using
52/53-week fiscal years) report month-end dates (e.g., 2024-09-30) on their cover page while tagging their floating Saturday period end (e.g., 2024-09-28) in
inline XBRL.
• Financial Impact:
If reportDate is 2024-09-30 and fact end is 2024-09-28, filing.get("reportDate") == end evaluates to False. The current filing's facts are stripped of fiscal_year
and fiscal_quarter, even though they are the primary current-period facts of that 10-K or 10-Q.
──────

#### Finding 2.4: Ticker resolution strictly rejects standard ticker conventions (e.g., BRK.B) and breaks cik_overrides

• Type: Confirmed Functional Bug / Usability Barrier
• Location: collector.py:70, collector.py:103-104
• Detail:
if not is_cik(identifier) and identifier.upper() not in tickers:
raise SecError("Ticker/CIK association does not match official SEC submissions")
If a user specifies a standard ticker containing dots or dashes (such as Berkshire Hathaway BRK.B or Brown-Forman BF.B), identify() resolves the ticker via
cik_overrides or company_tickers.json. However, \_retrieve() checks whether identifier.upper() in submissions["tickers"]. In SEC submissions metadata, Berkshire
Hathaway's tickers are listed as ["BRK-A", "BRK-B"].
• Technical Impact:
Running collect BRK.B fails with SecError, even if an explicit mapping (BRK.B = "0001067983") is set in cik_overrides.
──────

#### Finding 2.5: --filing-history has a hard 20-file limit with no pagination or windowing

• Type: Confirmed Design Limitation
• Location: collector.py:114-115
• Detail:
if len(files) > 20:
raise SecError("More than 20 historical submissions files; narrow this collection")
The error message instructs the user to "narrow this collection", but neither the CLI nor the Collector API provides parameters to limit or paginate historical
files.
• Technical Impact:
Any prolific filer with more than 20 historical submission files (e.g., large financial institutions, debt-issuing conglomerates with >20,000 historical filings)
cannot be collected with --filing-history.
──────

#### Finding 2.6: Submissions metadata join across files in filing_index is fragile to schema evolution

• Type: Potential Reliability Risk
• Location: financials.py:45-47
• Detail:
if accession in result and result[accession] != item:
raise SecError(f"Conflicting submissions metadata for {accession}")
filing_index compares full dictionary equality result[accession] != item across recent and historical submission files. If the SEC adds a new column (such as
isInlineXBRL) to recent filings that does not exist in an older static submissions-001.json file for an accession present in both, the inequality check triggers
and aborts the entire collection.
──────

### Low Severity & Architectural Maintainability

#### Finding 3.1: Double JSON decoding of massive payloads

• Type: Confirmed Performance Redundancy
• Location: sec.py:112 and collector.py:97
• Detail:
SecClient.get() calls decode_json(body) (parsing floating-point numbers into Decimal) on line 112 to validate the response. Subsequently, collector.\_retrieve()
calls response.json(), executing decode_json(self.body) a second time. For large issuers, CompanyFacts files reach 20–50 MB, resulting in redundant CPU and memory
overhead.
──────

#### Finding 3.2: Missing HTTP gzip compression increases bandwidth consumption by 10x–20x

• Type: Confirmed Efficiency Risk
• Location: sec.py:101
• Detail:
SecClient sets "Accept-Encoding": "identity", forcing SEC servers to return uncompressed JSON. While this avoids manual decompression, gzip.decompress is part of
the Python standard library. Transferring 30–50 MB uncompressed payloads over plain HTTP increases network latency and timeout risks.
──────

#### Finding 3.3: Inadequate test coverage for CLI commands and failure modes

• Type: Test Coverage Gap
• Location: test_collector.py
• Detail: 1. investor-ai companies CLI command (cli.py:60-70) is not tested. 2. Tab-delimited table formatting of inspect (cli.py:87-94) is not tested (only --json is exercised). 3. Non-matching reportDate != end (e.g. 52/53-week calendar mismatches) is not tested. 4. Interrupted writes in save_response leading to corrupted raw files are not tested.

──────

## 3. Summary Matrix

ID │ Issue │ Severity │ Status │ File & Line
───────────────┼──────────────────────────────────────────────────────────────────────────────────┼──────────────┼────────────────────────┼───────────────────────
1.1 │ Restatements/comparative revisions stripped of fiscal_year/quarter │ High │ Confirmed Risk │ financials.py:113-116
1.2 │ Non-atomic write in Store.save_response corrupts raw cache on interruption │ High │ Confirmed Bug │ storage.py:52-58
1.3 │ Annual balance sheet facts in 10-K assigned fiscal_quarter = 4 │ Medium │ Confirmed Bug │ financials.py:117-119
2.1 │ Cumulative cash flows assigned misleading fiscal_quarter numbers │ Medium │ Confirmed Risk │ financials.py:117-119
2.2 │ Asymmetric classification of Q1 (quarterly) vs Q2/Q3 (year_to_date) cash flows │ Medium │ Confirmed Risk │ financials.py:58-64
2.3 │ Strict equality reportDate == end drops fiscal labels on 52/53-week filers │ Medium │ Confirmed Risk │ financials.py:113
2.4 │ Ticker verification rejects valid ticker conventions (e.g. BRK.B) │ Medium │ Confirmed Bug │ collector.py:103-104
2.5 │ Hard 20-file limit on --filing-history without pagination breaks prolific filers │ Medium │ Confirmed Limitation │ collector.py:114-115
2.6 │ Submissions column comparison fragile to SEC schema evolution │ Medium │ Potential Risk │ financials.py:45-47
3.1 │ Double JSON decoding on every network request │ Low │ Confirmed Redundancy │ sec.py:112
3.2 │ Accept-Encoding: identity wastes bandwidth (10x–20x payload size) │ Low │ Confirmed Inefficiency │ sec.py:101
3.3 │ Gaps in test coverage (companies, table formatting, date discrepancies) │ Low │ Test Gap │ test_collector.py

This review was read-only; no files, dependencies, or git states were modified.
