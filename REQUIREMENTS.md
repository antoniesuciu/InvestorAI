# Investor AI — Project Requirements

## 1. Purpose and direction

Build an intelligent investment research and speculation platform that can analyze markets, discover opportunities, develop and challenge investment theses, and eventually support automated investment decisions. Develop it incrementally, prioritizing financial correctness, scientific rigor, maintainability, and extensibility.

Research outputs must distinguish evidence from interpretation and uncertainty from fact. Neither an AI-generated thesis nor a backtest establishes that an investment will be profitable.

This document is the source of truth for project scope and constraints. The capabilities below describe the long-term direction; they do not authorize immediate implementation.

## 2. Project phases and current scope

### Foundation — complete

The foundation deliverables, `REQUIREMENTS.md` and `AGENTS.md`, have been reviewed and approved. They establish the project direction, separate immediate work from future capabilities, and define data-cost constraints, financial correctness, reproducibility, engineering, security, and trading safeguards. Existing repository files and Git history have been preserved.

### Phase 1: Financial Data & News Research — complete

**Status:** The five Phase 1 research documents and the focused personal-use stock-price follow-up have been reviewed and approved. Phase 1 is complete.

**Immediate use:** A private, personal, noncommercial investment research application. Commercial deployment is a separate future concern. The focused stock-price follow-up is appended to `docs/DATA_SOURCES.md`; it evaluates automated personal use and local storage without creating accounts or implementing application code.

**Objective:** Investigate and compare completely free financial-data providers, news sources, and relevant financial analysis frameworks. Verify current access conditions and limitations before making evidence-backed recommendations.

Research coverage must include:

- Historical stock prices and related corporate-action data.
- Company fundamentals and financial statements.
- Regulatory filings and company disclosures.
- Earnings releases and earnings data.
- Financial news.
- Analyst estimates, with explicit coverage gaps where suitable free access is unavailable.
- Economic indicators and their historical revisions.
- Financial analysis frameworks and libraries relevant to acquiring, normalizing, analyzing, or valuing this data.

**Evaluation criteria:** Compare coverage, historical depth, data quality, rate limits, licensing, reliability, update frequency, and point-in-time availability. For each source, verify that access meets the completely free data constraints in section 3.1, including key or account requirements and restrictions on storage or reuse. Distinguish original releases and historical vintages from revised data, and identify whether timestamps support reconstructing information available at a past decision time.

For frameworks, assess applicable criteria and document maintenance, open-source licensing, data-source dependencies, and whether relevant functionality relies on paid data. Mark inapplicable or unverified criteria explicitly. Distinguish documented reliability from any observed behavior; do not imply testing that has not occurred.

**Expected deliverable:** `docs/DATA_SOURCES.md`, containing evidence-backed comparisons and recommendations. It must:

- Cover each research category and compare candidates against the evaluation criteria.
- Cite supporting sources, preferably official documentation, terms, and primary disclosures, and record access dates for time-sensitive claims.
- Distinguish verified facts, observations, assumptions, and unresolved questions; disclose missing evidence and free-access limitations.
- Recommend sources and frameworks for suitable use cases, explain tradeoffs, and identify alternatives and gaps, especially for point-in-time research.

Completing Phase 1 means delivering this report for review. Recommendations do not authorize integration or implementation. Application code, dependency installation, infrastructure configuration, and execution or trading functionality remain outside Phase 1. No provider, framework, database, broker, deployment platform, or detailed implementation design is selected by this scope definition.

### Current phase — Phase 2: Core Financial Data Infrastructure

**Status:** Milestone 1 — the SEC EDGAR collector — is complete, live-validated, approved and published in commit `5694877`. Its AAPL/MSFT validation and limitations remain documented in `docs/SEC_COLLECTOR_VALIDATION.md` and `docs/SEC_COLLECTOR_REVIEW_RESPONSE.md`. Milestone 2 — the Financial Analysis Engine — is complete, approved and published in commit `a0a48f1`. Its 122-test baseline, original-filing verifiers, eight independent-review dispositions and financial limitations remain in force; see `docs/FINANCIAL_ANALYSIS.md` and `docs/FINANCIAL_ANALYSIS_REVIEW_RESPONSE.md`. Milestone 3A — Offline Market Data Infrastructure — is complete and approved by the user on 2026-10-28. Live provider integration is incomplete pending material access/retention conditions. Phase 2 remains current; the user has authorized committing and pushing the completed Milestone 3A implementation, tests and documentation. This finalization adds no functionality.

**Objective:** Establish a small, modular and reproducible foundation for acquiring and retaining financial data locally. Start with official SEC EDGAR company disclosures. Preserve all constraints in sections 3, 5 and 6; completing research does not relax data-cost, historical-accuracy, reproducibility or security requirements.

#### First milestone — working SEC EDGAR financial-data collector — complete and approved

Deliver a working collector for an explicit, configurable small watchlist of US reporting companies. Collect company/submission metadata and reported financial facts through official SEC EDGAR public interfaces, with links to the original filings. Keep the initial fact set focused on reported income-statement, balance-sheet and cash-flow information; a complete standardized accounting database is outside this milestone.

Acceptance criteria:

- A documented local command collects data for the configured watchlist and reports success, partial results and failures per company. Expose collection and retrieval through clear programmatic interfaces so the CLI, agents and a later web frontend can use the same backend functionality. Preserve CIK and source identifiers rather than treating ticker symbols as permanent identities.
- Use free official access with declared contact identity, a shared request budget within the current SEC policy, bounded retries/backoff and explicit error handling. Recheck access policy when implementation begins; do not silently fall back to another provider.
- Save permitted raw responses and a retrieval manifest locally with source URLs, retrieval times, source identifiers, schema/parser versions and available filing/accession references. Keep downloaded data and databases in ignored storage paths.
- Preserve reported periods, units, currencies, taxonomy/context and filing/version information. Distinguish reported values from derived transformations and keep missing values distinct from zero. Expose custom-tag, dimensional and unavailable-field limitations.
- Keep reporting-period end, filing/acceptance dates, evidenced public availability and our retrieval time separate. Preserve revisions and amendments; do not silently overwrite earlier evidence or claim a certified historical reconstruction from today's aggregate facts alone.
- Repeated runs and restarts avoid duplicate records and preserve changed source versions. Caching and incremental retrieval reduce unnecessary requests without disguising input age or provenance.
- Support scheduled invocations for the watchlist with source-specific update cadence, a shared provider request budget and no overlapping duplicate work. Record the last successful refresh and failed attempts separately; a failed refresh or cache read must not make old data appear newly published or collected. A local schedule is sufficient; no scheduler service is selected here.
- Keep acquisition, local persistence and financial interpretation independently testable through simple module boundaries; select concrete storage/tooling only when justified by the implementation task.
- Reconcile a small sample of collected values against original filings. Meaningful offline checks with reviewed synthetic fixtures cover parsing, units/periods, missing/error cases, revision preservation and repeatable ingestion. Document reproduction from permitted saved inputs and any live-access verification gaps.

**Milestone exclusions:** stock-price-provider integration, news or macro ingestion, valuation/forecasting models, portfolio features, autonomous investment decisions, and execution or trading. The price-source recommendation remains research context, not an integration prerequisite. No broker account, cloud infrastructure or distributed service is required for this milestone.

#### Milestone 2 — Financial Analysis Engine — complete and approved

Provide an independently testable, reusable analysis interface over existing local SEC evidence. Calculate annual/quarterly revenue and net-income growth, net/gross/operating margins, free cash flow and selected, explicitly defined debt/liquidity ratios. Extend the CLI with metric and historical-trend output; return formulas, statuses, financial intervals, units and source filing provenance to all callers.

Use conservative historical cutoffs based on known observation/snapshot availability. Do not treat current aggregate facts or filing dates alone as certified historical evidence. Never use a later-observed revision or review in an earlier analysis. Use coherent inputs matching latest known values for each calculation; preserve usable historical statements when later comparatives are unchanged, and keep partial amendments from erasing unaffected reported fields. Prioritize appropriate annual 10-K balance evidence while explicitly reconciling interim revisions. Preserve missing, negative-base, conflicting-concept and unresolved-fiscal-period states; missing debt is not zero. Derive standalone quarters from cumulative cash flows only when period alignment, historical availability and accounting comparability are evidenced. Across filings, require exact reviewed original-source links, both source accessions and reconciled revisions; otherwise report the gap rather than assume unchanged earlier-quarter accounting.

Reparse additional required tags from saved CompanyFacts locally; preserve original snapshots and the collector's default output. Validate financial formulas with hand calculations, original filings and synthetic temporal/accounting regressions. Keep interpretation/calculation separate from local-storage access so dashboard, valuation and agent callers can reuse the same interface.

This milestone excludes forecasting, valuation models, AI analysis, portfolio/trading features, provider integrations and the dashboard. It adds no dependency or infrastructure service. Existing free-data, historical-accuracy, reproducibility, security and engineering requirements remain unchanged.

#### Milestone 3A — Offline Market Data Infrastructure — complete and approved

Research and compare completely free providers using current official sources; preserve exact plan/feed, storage/retention and permitted-use limitations. Alpaca Basic is the conditional preferred technical candidate; it is not enabled. See `docs/MARKET_DATA_PROVIDER_DECISION.md`. A key or a permissive marketing page does not override account, exchange or retention restrictions. Public API access is not a license to archive, redistribute or train models.

The user expressly authorized provider-independent infrastructure and deterministic offline fixtures if material rights remain unresolved. The delivered foundation validates explicitly synthetic daily OHLCV, distinct raw/adjusted representations, splits/dividends and supplied instrument/calendar evidence; supports incremental paginated collection, bounded throttling/retries, local content-addressed storage, revision occurrences, cache integrity and offline CLI inspection; and preserves provenance and observed-by-cutoff availability. It does not fetch real market data. Missing sessions/actions are not fabricated. Return calculations distinguish split-corrected price return from total return; unsupported financial transformations fail explicitly.

Acceptance for this fallback: document the provider decision and blocker; meaningful regressions cover financial/temporal, session, pagination, retry, storage, revision and offline behavior; all Milestone 1/2 tests and original-filing verifiers continue to pass; the CLI and reports clearly distinguish synthetic validated functionality from incomplete live integration. `docs/MARKET_DATA.md` records implemented interfaces, validation, limitations and remaining live gates.

All six findings in the complete Gemini Milestone 3 review have been independently reconciled in `docs/MARKET_DATA_REVIEW_RESPONSE.md`. The corrected foundation has 187 passing tests and both existing SEC filing verifiers pass. Complete corporate-action windows accumulate with preserved source evidence and historical availability; incomplete windows never manufacture completeness. Market lock contention uses a market-specific error, zero-volume semantics remain feed-specific, and legitimate payment-before-ex-date special dividends are retained. A final adversarial integration scenario with two actual process exits, incremental corrections, independent financial references and historical replay passed; see `docs/MARKET_DATA_ADVERSARIAL_VALIDATION.md`. Milestone 3A is approved for finalization; live integration remains incomplete and requires a separate implementation scope after the documented rights and validation gates are satisfied.

Before full live-milestone acceptance: establish applicable free account/feed and storage duration/deletion permissions, implement one provider-specific adapter and provider-wide quotas, validate real AAPL/MSFT prices and known corporate actions against primary evidence, and verify source calendar, adjustments and revision behavior. No paid fallback, unauthorized scraping or assumed permissions are allowed. Real historical share counts, market capitalization, valuation ratios, total returns, complete historical security-master/delisting reconstruction, dashboard, ML/AI, portfolio and execution functionality remain deferred; unsupported share definitions must never enter a valuation calculation.

#### Early subsequent milestone — interactive web research dashboard

Begin this milestone after the core financial-data pipeline is working and its data/provenance checks pass. Deliver a first interactive view of the available watchlist and company financial data, with source references, reporting periods and data freshness visible. Expand incrementally toward market overviews, company financial analysis, valuation models, AI research, news monitoring, watchlists and portfolio analytics as their underlying data and analysis capabilities are implemented and validated. This does not bring those later backend features into the first collector milestone.

Keep financial-data access and analysis behind clear shared backend interfaces used by both programmatic agents and the future frontend; financial calculations and provider access rules belong in the backend. Return provenance, availability/quality limitations and refresh status alongside results. Show relevant source publication/observation times, last successful collection and stale, missing or failed-refresh states without confusing reporting periods with release times.

Provide scheduled backend updates within source permissions and rate/daily limits, coordinated across agent activity and dashboard use. Use caching and bounded refresh requests so opening a page does not trigger uncontrolled upstream calls. The frontend framework, web transport and deployment architecture remain undecided. Dashboard implementation is outside this phase-initialization task.

## 3. Constraints applying to all future development

### 3.1 Completely free financial data

- Financial and news data used by the platform must be obtainable without payment. Paid APIs, pay-as-you-go services, and trials requiring payment information are prohibited.
- Free API keys are acceptable. A free offering is usable only within its verified free access conditions; do not rely on a trial or automatic paid fallback.
- Prefer official primary sources, regulatory filings, and company disclosures. Identify secondary sources and their limitations explicitly.
- Respect rate limits, licensing, attribution, retention, redistribution restrictions, and terms of service. Public accessibility alone does not establish permission to reuse data.
- Calculate ratios and valuations locally whenever practical. Cache locally to reduce requests, subject to the source's permitted retention.
- When suitable free data is unavailable, document the coverage gap and its impact rather than silently substituting paid access or inventing observations.

### 3.2 Financial correctness and provenance

- Label reported results, externally supplied estimates, and internally generated projections separately, including in stored datasets and derived outputs.
- Preserve source references, publication or release times, retrieval times, reporting periods, and the time information became available to the analysis. Record uncertainty when availability cannot be established.
- Preserve historical versions and restatements with their availability times, subject to source permissions. A revision must not silently replace the information used by an earlier experiment or thesis.
- Record units, currencies, scaling, fiscal calendars, accounting basis, and relevant definitions. Distinguish period flows from point-in-time balances and missing values from zero.
- Make adjustments and transformations explicit, including treatment of corporate actions, share counts, currency conversion, and differences between reported accounting measures and adjusted measures.
- Validate important calculations against worked examples or independent reference calculations. Check formulas, dimensions, accounting relationships, rounding, and material edge cases.
- Expose missing, stale, conflicting, or unreliable inputs. Do not present unsupported precision or silently treat unavailable data as valid.

### 3.3 Reproducible research and evaluation

- Preserve hypotheses, assumptions, methods, dataset versions or permitted fingerprints, code versions, configuration, results, and limitations. Record random seeds where relevant.
- Use information available at the simulated decision time. Prevent lookahead bias and data leakage, including leakage through restatements, preprocessing, feature selection, and model tuning.
- Account for survivorship and selection bias, including historical universe membership and delisted securities where data permits. State limitations when these cannot be addressed.
- Evaluate predictive models with appropriate time-based separation and out-of-sample checks. Record unsuccessful experiments and material model-selection decisions to make overfitting assessable.
- Support performance claims with reproducible evidence and suitable benchmarks. Future simulations must state assumptions for fees, spreads, slippage, liquidity, and execution timing.
- Express forecasts as conditional estimates with uncertainty, sensitivity, and limitations. Preserve original predictions and subsequent outcomes instead of rewriting history.

## 4. Future functional capabilities

All capabilities in this section are future work, to be scoped and validated individually.

| Area | Required capability |
| --- | --- |
| Financial research | Analyze public companies, statements, earnings, cash flows, industries, competitors, economic conditions, and markets. Collect permitted news and company announcements; retain historical data and research with provenance. |
| Investment discovery | Identify candidates for under- or overvaluation using fundamental, statistical, and AI-assisted methods. Compare companies and long-term trends using explicit selection criteria and comparable definitions. Candidate rankings are hypotheses for investigation. |
| Valuation | Implement locally calculated DCF and other justified valuation methods with documented inputs, assumptions, discounting conventions, sensitivity analysis, and independent numerical checks. |
| Forecasting | Develop statistical and machine-learning models for company performance and market trends. Compare optimistic, baseline, and pessimistic scenarios and quantify uncertainty. Distinguish scenarios from probability estimates unless probabilities are justified. |
| Research records | Store investment theses, supporting and contradictory evidence, assumptions, catalysts, invalidation conditions, revisions, and historical predictions. Link conclusions to their supporting sources and model versions. |
| Portfolio analysis | Monitor holdings, cash flows, performance, allocation, diversification, and risk. Use appropriate benchmarks and explicit valuation currency and return methodology. |
| Autonomous monitoring | Monitor permitted news, earnings, and macroeconomic releases; identify material changes, update versioned theses, flag contradictory evidence, and evaluate prior predictions. Make autonomous activity observable and bounded. |
| Interactive web dashboard | Provide market overviews, company financial analysis, valuation models, AI research, news monitoring, watchlists and portfolio analytics incrementally. Reuse the same backend data/analysis interfaces as programmatic agents, expose data freshness and provenance, and support provider-compliant scheduled updates. The first dashboard milestone follows a working core pipeline; no frontend framework is selected. |
| Simulation | Support paper trading and simulated portfolios with traceable decisions, independent risk checks, and realistic execution assumptions. Simulation must remain isolated from live execution. |
| Broker integration | Eventually support broker APIs through a separate execution boundary, only after explicit authorization, enforced risk controls, and a separately reviewed scope. |

## 5. Non-functional and architectural requirements

- **Python first:** Prefer established open-source libraries. Introduce Rust or C++ only when benchmarks identify a material bottleneck and justify the added complexity.
- **Modularity:** Keep data acquisition and normalization, research and modeling, investment decisions, risk management, and execution logically separate. Begin with simple module boundaries; this does not require separate services.
- **Shared backend interfaces:** Expose data and analysis through clear, testable interfaces for programmatic agents and a future web frontend. Keep financial logic, permissions, caching and provider limits in the shared backend; defer transport and frontend framework choices until needed.
- **Freshness and scheduled updates:** Track source observation/publication times, retrieval times, successful refreshes and failures distinctly. Make stale/missing/uncertain states visible in programmatic responses and the dashboard. Coordinate scheduled and on-demand work within provider limits, with bounded retries and no duplicate overlapping collection.
- **Testability:** Keep financial calculations independently testable and external integrations replaceable. Use deterministic fixtures and meaningful tests of calculations, data transformations, and failure behavior as implementation begins.
- **Local storage and caching:** Retain permitted source material separately from derived data, with traceable transformations. Make historical inputs recoverable where licensing allows. Choose storage technology after access patterns and data requirements are understood.
- **Efficiency:** Minimize unnecessary requests and repeated computation. Measure representative workloads before optimizing or adding infrastructure.
- **Reliability:** Handle rate limits, source outages, schema changes, partial downloads, and stale data explicitly. Make ingestion repeatable without creating duplicate observations.
- **Observability:** Record source failures, data-quality problems, research runs, model versions, and decision rationale in a form that supports debugging and audit.
- **Maintainability:** Document interfaces and domain definitions, keep changes focused, and record dependency versions and reproduction instructions once tooling is introduced.
- **Incremental expansion:** Add capabilities through reviewed, bounded tasks. Avoid speculative infrastructure and abstractions without a demonstrated need.

## 6. Security and investment risk

- Keep credentials and sensitive account information out of source control, datasets, research outputs, logs, and Obsidian. Use local ignored configuration and least-privilege access when integrations are introduced.
- Treat retrieved documents, news, and model-generated text as untrusted inputs. External content must not grant tool permissions, alter system instructions, expose secrets, or authorize financial actions.
- Keep research and decision outputs separate from execution authority. A thesis, signal, or monitoring event must not itself authorize a real order.
- No automated real-money trading is allowed during initial development. Future live execution requires explicit user authorization to enable it and a defined operating scope with enforceable risk limits.
- Before any live capability, define and independently enforce capital, position, concentration, exposure, loss, and order limits as appropriate to the approved scope. Define stale-data behavior, duplicate-order prevention, reconciliation, audit records, and an emergency stop. Fail closed when authorization or required risk checks are unavailable.
- Evaluate simulation results and operational safeguards before considering live integration; good simulated performance alone does not authorize live trading.

## 7. Repository and knowledge management

Keep code, detailed research reports, experiments, and datasets in the repository or suitable local storage. Use Obsidian for concise, durable decisions and reusable context; repository documents remain authoritative for current project requirements and implementation details.

Use the existing Git repository. Inspect current state before changes, preserve unrelated work and history, and avoid committing datasets, credentials, caches, or generated artifacts. Commit and push only when authorized by the user.

### Version control and local storage

The root `.gitignore` defines repository exclusions for Python environments/caches, credentials, downloaded datasets/databases, generated outputs, IDE settings and OS files. Place downloaded data in ignored local-storage paths such as `data/`, `datasets/` or `downloads/`; keep authored research documentation in `docs/`. These are storage conventions, not authorization to create infrastructure or acquire data.

Keep source code, tests, reviewed synthetic test fixtures, dependency lockfiles, reproducibility manifests, and authored research reports versioned. Synthetic data files may live in `tests/fixtures/` or `fixtures/`, which have data-file exceptions; downloaded financial datasets and real credentials do not belong there. Example configuration must contain placeholders only. Ignore rules do not remove already tracked files or replace credential handling. Review any already tracked sensitive or generated material with the user before deleting it or removing it from version control.

## 8. Questions for later investigation

- Which sources remain completely free for the required use, with adequate coverage, historical depth, revision history, and permitted storage? Which gaps require reducing scope?
- How should identifiers, corporate actions, delistings, fiscal periods, currencies, and accounting differences be represented consistently?
- What availability metadata is sufficient for point-in-time reconstruction, and how should analyses be limited when it is missing?
- Which numerical conventions, uncertainty methods, benchmarks, and evaluation protocols fit each valuation or strategy hypothesis?
- Which storage and versioning approach can preserve provenance and reproduce research with modest local resources?
- How should AI-generated claims be verified, contradictory evidence surfaced, and autonomous monitoring bounded and evaluated?
- What authorization model, independent risk controls, operational safeguards, and applicable legal obligations would be required before any future live execution?

These are investigation topics, not decisions or implementation work authorized by this document.
