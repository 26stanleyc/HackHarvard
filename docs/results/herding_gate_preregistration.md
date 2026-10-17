# M2 herding gate — pre-registration

Written and committed before any simulator code exists (2026-10-17). It pins the interpretation of the M2 gate in `docs/design.md` so it cannot be adjusted after results are seen.

- **Days:** the 7 herding-gate days in `backend/data/processed/days.json` (2024-02-03, 03-18, 03-27, 04-24, 04-27, 09-03, 11-29). Used for nothing else.
- **Shares:** flexible load as % of the day's peak demand, where share = Σ(max_mw of population bids) / day's actual peak demand. Grid 0, 2.5, 5, …, 30%.
- **Population:** synthetic bids from the stated distribution in `clearinghouse/population.py`, fixed seeds, averaged over K = 5 draws. No background population in the gate.
- **Planning:** yesterday-as-forecast inputs (previous CST day's actual demand, non-fossil supply, net imports and curtailment). **Scoring:** the simulator on the actual day's EIA-930 inputs.
- **Primary metric (B3 definition):** strategy 3 (independent-marginal). Predicted savings = Σ_i [E(all default) − E(i runs strategy 3 alone, others default)]. Realized savings = E(all default) − E(all run strategy 3 together). Gap = predicted − realized; gap % = gap / predicted (only where predicted > 0).
- **Secondary (reported, not part of the decision):** the same metric for strategy 2 (independent-average).
- **GO** if the primary gap % ≥ 10% at some share ≤ 20% on at least 4 of the 7 days, using the mean over the K draws. **NO-GO** otherwise: stop and ask the user.
- The uncalibrated simulator uses stated default parameters (`clearinghouse/params.py`). Nothing is tuned against gate results.
