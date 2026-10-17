# Carbon Clearinghouse

Coordinated load shifting across competing companies, with avoided-carbon payouts.

Carbon-aware tools tell every flexible load (EV fleets, data centers, office pre-cooling) to move into the same "clean" hours. When enough load follows the same signal, it exhausts the spare clean energy and fossil plants ramp up to meet the rush, so realized savings fall short of what each company was promised (the herding effect; Martin, Powell & Rajagopal, *Nature Communications*, 2025). The clearinghouse takes flexibility bids from independent companies, schedules them jointly on a calibrated ERCOT grid simulator, and pays each company — at a stated carbon price — its Shapley share of the emissions that coordination avoided beyond what it could have saved acting alone.

The design and the pre-registered milestones are in [docs/design.md](docs/design.md). Every check, number and decision from the build is in [docs/progress.md](docs/progress.md).

## Run it locally

Requirements: [uv](https://docs.astral.sh/uv/) (Python 3.12 is installed by uv) and Node.js 20+.

```bash
cd backend && uv sync && uv run uvicorn clearinghouse.api:app --port 8000
```

In a second terminal:

```bash
cd frontend && npm install && npm run dev
```

Open http://localhost:3000. The frontend talks to the backend at `http://localhost:8000` (`NEXT_PUBLIC_API_BASE`). With `NEXT_PUBLIC_API_MODE=fixture` it runs on the recorded responses in `fixtures/` instead, and says so on screen.

No API keys are needed to run the app: the processed datasets (1.3 MB), the calibrated parameters, the forecast models and the precomputed adoption chart are committed. Keys are only needed to re-download raw data (see below).

Checks:

```bash
cd backend && uv run pytest -q && uv run ruff check .
cd frontend && npm run lint && npm run typecheck && npm run build
backend/scripts/smoke_api.sh http://localhost:8000   # with the backend running
```

## How it works

| Piece | Where | What it does |
|---|---|---|
| Data | `backend/clearinghouse/data/` | EIA-930, eGRID, fuel prices and ERCOT curtailment → hourly fixed-CST (UTC-6) series in `backend/data/processed/` |
| Simulator | `backend/clearinghouse/simulator.py` | Vectorized merit order of 283 ERCOT fossil plants; added load is first absorbed by curtailed wind/solar, then dispatched up the fossil stack |
| Calibration | `backend/clearinghouse/calibration.py` | Fits per-fuel availability and a monthly coal-vs-gas price scale on 248 training days, validates on 104 held-out days |
| Forecast | `backend/clearinghouse/forecast/` | Gradient-boosted next-24h demand, wind and solar from EIA-930 history; `ForecastProvider` with local and Chalk implementations |
| Strategies | `backend/clearinghouse/strategies.py` | Default, independent-average (standard carbon-aware tool), independent-marginal, and the coordinator (greedy 1% blocks, earliest deadline first) |
| Settlement | `backend/clearinghouse/settlement.py` | Exact Shapley over all 2^n coalitions (n ≤ 8), leave-one-out, payouts with pool proration |
| API | `backend/clearinghouse/api.py` | FastAPI: `/api/meta`, `/api/presets`, `/api/adoption-chart`, `/api/clear`, `/api/evaluate` (contract in `schemas.py`) |
| Frontend | `frontend/` | Next.js: story, bid builder, watch-the-coordinator (three acts), beat the coordinator, receipts with leave-the-coalition |

Every strategy plans on the forecast and is scored on the actual day. The payout baseline is every company acting alone with the independent-marginal strategy, so companies are paid only for what coordination adds.

## Data sources

| Source | Used for | Access |
|---|---|---|
| EIA-930 (ERCOT, BA code ERCO), six-month bulk files, 2024 | Hourly demand, generation by fuel, interchange; forecast training | No key |
| EPA eGRID2023 rev2 (PLNT23, GEN23) | Plant capacity, heat rate, CO2 rate, names | No key |
| EIA API v2, Texas monthly fuel cost (sector 98; oil sector 1) | Merit-order fuel prices | `EIA_API_KEY` |
| ERCOT NP3-965-ER 60-day SCED disclosure | Hourly wind/solar curtailment (Σ max(0, HSL − base point)) | `ERCOT_*` keys |
| ERCOT NP4-732-CD / NP4-737-CD | Validation of the curtailment series | `ERCOT_*` keys |

Re-download and rebuild (keys in a repo-root `.env`, see `.env.example`):

```bash
cd backend
uv run python scripts/download_data.py        # raw files → backend/data/raw/ (gitignored, ~430 MB)
uv run python scripts/build_processed.py      # → backend/data/processed/
uv run python scripts/calibrate.py            # → calibrated_params.json, docs/results/calibration.json
uv run python scripts/train_forecast.py       # → data/processed/forecast/, docs/results/forecast_metrics.json
uv run python scripts/build_adoption_chart.py # → data/processed/adoption_chart.json
uv run python scripts/run_herding_gate.py --params calibrated
uv run python scripts/regenerate_fixtures.py  # → fixtures/
```

`uv run python -m clearinghouse.config` prints each key as set or missing (never the values).

## Results (held-out data, reported as they came out)

- **Calibration** (104 held-out days): hourly fossil CO2 MAPE **7.7%** (target ≤ 15%); hourly coal-share error **6.9 points**; marginal emission factor simulated **0.627** vs observed **0.549 t/MWh**. The simulator runs somewhat more coal than observed (27% vs 23% share). [`docs/results/calibration.json`](docs/results/calibration.json)
- **Herding gate** (pre-registered, 7 days used for nothing else): **GO** — 7/7 days on the uncalibrated simulator, 6/7 on the calibrated one. Realized savings of independent-marginal shifting fall ≥ 10% short of the sum of solo savings at flexible-load shares of 2.5–12.5% of peak. [`docs/results/herding_gate_preregistration.md`](docs/results/herding_gate_preregistration.md), [`herding_gate_calibrated.json`](docs/results/herding_gate_calibrated.json)
- **Forecast** (Oct–Dec 2024 test period, issued 00:00 CST on the day): demand MAPE **3.09%** vs 3.99% for yesterday-as-forecast; wind and solar have no weather inputs and errors are large. [`docs/results/forecast_metrics.json`](docs/results/forecast_metrics.json)
- **Coordination vs acting alone:** mixed, and shown wherever it lands. On the default demo day (a windy spring night) curtailed wind absorbs all the example companies' load, so every strategy ties and payouts are $0. On the summer heat peak the example coalition avoids 170 t. In the adoption chart (default day), independent-marginal shifting is the worst strategy above 20% adoption and coordination recovers about 1.4 kt of that at 30%, but does not beat the default schedule on actuals. Forecast error is the main reason coordination planned on the forecast can lose on the actual day.

## Key status and placeholders

| Key | Status | Effect |
|---|---|---|
| `EIA_API_KEY`, `ERCOT_*` | set | Raw-data downloads |
| `CHALK_CLIENT_ID`, `CHALK_CLIENT_SECRET`, `CHALK_ENVIRONMENT` | **pending** | The local forecast provider serves every clear and the UI says "local model (Chalk credentials pending)". `ChalkForecastProvider` (REST) and the Chalk project in `backend/chalk/` are written but not deployed. To switch: `chalk apply` from `backend/chalk/`, then fill the three variables; no code change. |
| `DATA_GOV_API_KEY` | **pending** | CAMPD validation is skipped; validation (b) uses EIA-930 fuel mix × eGRID emission factors instead. |

Deviation: the forecast uses scikit-learn's `HistGradientBoostingRegressor` instead of LightGBM, because LightGBM's macOS wheel needs a system `libomp` (`brew install libomp`) that was not installed.

## Limitations (stated, not solved)

- No transmission congestion (single-node merit order); no ramping or unit commitment.
- Bids are self-reported windows; a company could misstate them. Production would set baselines from declared-in-advance or historical profiles.
- The coordinator is greedy and emissions vs load is non-convex, so it is not guaranteed optimal; a planning-time fallback keeps it no worse than acting alone on the forecast (not on actuals).
- Carbon price ($50/t), payout pool ($100,000) and background flexible load (10% of peak) are illustrative, user-adjustable inputs.
- The demo shows the operator/auditor view of all receipts; in production each company would see only its own.
