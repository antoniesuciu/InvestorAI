# Milestone 3A — Final adversarial integration validation

Verified **2026-10-28** against the offline foundation built from baseline `a0a48f1`. The user subsequently approved Milestone 3A for finalization on 2026-10-28.

**Result: passed. Milestone 3A, the offline foundation, is complete and approved.** No production defect was reproduced and no production code was changed by this validation task. One integrated regression was added in `tests/test_market_adversarial.py`; no collection of redundant unit tests was added. Live provider integration remains disabled and incomplete.

## Scenario and independent references

A fresh temporary store receives invented daily US-equity-style bars for January 2024, with explicit sessions and positive volume. Controlled local observation times are in February 2025; bar publication dates remain in 2024. The source file is replaced between updates, so old snapshots must remain reproducible from stored evidence rather than the current input file.

| Stage | Source evidence | Independent price-return reference |
| --- | --- | --- |
| Initial historical ingestion | History through January 5; 2:1 split and cash dividend | $120 cost → 2 shares × $62 = $124: **1/30**, approximately 3.333333% |
| Overlapping incremental update | Through January 10; another 3:1 split and dividend | $120 → 6 shares × $22 = $132: **10%** |
| Incomplete update | Through January 12; reverse split known, one dividend omitted | Full-interval return **unavailable**; unaffected earlier return stays 1/30 |
| Complete reconciliation | Overlap restores coverage and supplies the missing dividend | $120 → 3 shares × $45 = $135: **12.5%** |
| Retroactive corrections | Initial price $120→$125, second split 3→2.5, first dividend $0.50→$0.75 | $125 → 2.5 shares × $45 = $112.50: **−10%**; earlier market interval now **−0.8%** |
| Interrupted extension, then restart | Two actual child-process exits; successful restart extends through January 16 | $125 → 2.5 shares × $46 = $115: **−8%** |

Expected values are hand-calculated holdings/cost results represented independently with exact `Fraction` arithmetic. Production Decimal results must agree within `1e-32`. Dividends remain separately evidenced and excluded from these **price returns**; these are not total-return calculations. Six previously published snapshots and their earlier return views remain readable after corrections and restart.

## Adversarial checks

- DNS resolution, socket connections and `connect_ex` are blocked in the parent test; each child independently installs a network-denying audit hook.
- One child exits with `os._exit(73)` after writing half a raw-object temporary. Another exits after a complete orphan snapshot is written but before the latest pointer is replaced. Both bypass normal cleanup.
- Reopening the store after each exit selects the last published snapshot. Partial temporaries and the orphan never become queryable; retry succeeds and links to the correct published predecessor.
- Incomplete action coverage blocks the affected historical return, while an unaffected earlier interval remains usable. Later complete overlapping evidence repairs the current view without repairing earlier cutoffs retroactively.
- All six historical snapshots are reloaded and compared with their saved data/manifests. Original immutable files remain byte-identical. Source-page and completeness-window references pass hash verification; old observations keep their original times.
- A cutoff preceding local ingestion is rejected despite older market/publication dates. Corrected price and event versions coexist with their originals.
- A repeated source hits cache without changing the snapshot. Deliberate committed-data corruption is rejected during inspection and cache lookup; restoration of the original bytes occurs only in the temporary test store and is not an application repair feature.

## Reproduction and final checks

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -p test_market_adversarial.py -v
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

The full suite passes **187 tests**, including the unchanged 122 SEC/analysis baseline tests. Both existing original SEC filing verifiers also pass: 52 original current-period inputs plus eight comparatives and 48 formula checks; 16 cumulative endpoints, 18 quarterly derived values and four direct-quarter controls. Verifier writes were suppressed to preserve existing evidence timestamps. Summary evidence is in ignored `data/market/verification/adversarial_validation.json`.

This validates process interruption, not hardware power-loss durability or real-provider correctness. Existing retention/access gates, cancellations/retractions, complex actions, legacy-proof recovery and uncertified pre-collection history remain as documented. No live market calls, dependency installation, commits or pushes were made. Full live Milestone 3 is not being declared complete.
