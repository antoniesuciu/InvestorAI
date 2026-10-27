# Investor AI — Project Requirements

## 1. Purpose and direction

Build an intelligent investment research and speculation platform that can analyze markets, discover opportunities, develop and challenge investment theses, and eventually support automated investment decisions. Develop it incrementally, prioritizing financial correctness, scientific rigor, maintainability, and extensibility.

Research outputs must distinguish evidence from interpretation and uncertainty from fact. Neither an AI-generated thesis nor a backtest establishes that an investment will be profitable.

This document is the source of truth for project scope and constraints. The capabilities below describe the long-term direction; they do not authorize immediate implementation.

## 2. Current scope: project foundation only

The current deliverables are this requirements document and project-specific Codex instructions in `AGENTS.md`.

Completion criteria:

- Immediate work and future capabilities are clearly separated.
- Data costs, financial correctness, reproducibility, security, and trading safeguards are explicit.
- Existing files, Git branches, remotes, and history are preserved.
- Changes are presented for review without committing or pushing.

Application code, provider selection, dependency installation, infrastructure configuration, and execution or trading functionality are outside this initialization. Extensive research is also outside scope.

The next task is independent research into completely free financial-data providers, news sources, and financial analysis frameworks. It must verify current access conditions and limitations before making recommendations. No provider, framework, database, broker, deployment platform, or detailed implementation design is selected here.

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
| Simulation | Support paper trading and simulated portfolios with traceable decisions, independent risk checks, and realistic execution assumptions. Simulation must remain isolated from live execution. |
| Broker integration | Eventually support broker APIs through a separate execution boundary, only after explicit authorization, enforced risk controls, and a separately reviewed scope. |

## 5. Non-functional and architectural requirements

- **Python first:** Prefer established open-source libraries. Introduce Rust or C++ only when benchmarks identify a material bottleneck and justify the added complexity.
- **Modularity:** Keep data acquisition and normalization, research and modeling, investment decisions, risk management, and execution logically separate. Begin with simple module boundaries; this does not require separate services.
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

Use the existing Git repository. Inspect current state before changes, preserve unrelated work and history, and avoid committing datasets, credentials, caches, or generated artifacts. This initialization must not create commits or push changes.

### Recommended future `.gitignore`

Create a `.gitignore` when project tooling and storage paths are introduced. A suitable starting point is:

```gitignore
# Python environments, builds, and caches
.venv/
venv/
__pycache__/
*.py[cod]
.pytest_cache/
.mypy_cache/
.ruff_cache/
.coverage
.coverage.*
htmlcov/
build/
dist/
*.egg-info/
.ipynb_checkpoints/

# Local credentials and configuration
.env
.env.*
!.env.example
.secrets/

# Proposed local storage and generated output paths
data/
cache/
artifacts/
logs/
```

These storage paths are recommendations, not an established directory layout. Keep reviewed synthetic test fixtures, dependency lockfiles, reproducibility manifests, and authored research reports versioned. Example configuration must contain placeholders only. Ignore rules do not remove already tracked secrets or replace credential handling.

## 8. Questions for later investigation

- Which sources remain completely free for the required use, with adequate coverage, historical depth, revision history, and permitted storage? Which gaps require reducing scope?
- How should identifiers, corporate actions, delistings, fiscal periods, currencies, and accounting differences be represented consistently?
- What availability metadata is sufficient for point-in-time reconstruction, and how should analyses be limited when it is missing?
- Which numerical conventions, uncertainty methods, benchmarks, and evaluation protocols fit each valuation or strategy hypothesis?
- Which storage and versioning approach can preserve provenance and reproduce research with modest local resources?
- How should AI-generated claims be verified, contradictory evidence surfaced, and autonomous monitoring bounded and evaluated?
- What authorization model, independent risk controls, operational safeguards, and applicable legal obligations would be required before any future live execution?

These are investigation topics, not decisions or implementation work authorized by this document.
