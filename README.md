# sage.ai

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
| Forecast | `backend/clearinghouse/forecast/` | Gradient-boosted next-24h demand, wind and solar from EIA-930 history plus (M10) ERCOT's own wind/solar forecasts posted before the issue time and archived GFS weather forecasts; `ForecastProvider` with local and Chalk implementations |
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
| ERCOT NP4-732-CD / NP4-737-CD, point in time (M10) | Forecast inputs: STWPF, WGRPP, COP HSL (wind) and STPPF, PVGRPP, COP HSL (solar) from the latest posting before 00:00 CST on the target day | `ERCOT_*` keys |
| Open-Meteo Previous Runs API, GFS 0.25° (`gfs_global`), `*_previous_day2` only (M10) | Forecast inputs: archived 100 m wind, solar radiation and 2 m temperature forecasts (lead ≥ 48 h) at capacity-weighted ERCOT wind/solar sites (from eGRID) and 4 load centers. Weather data by [Open-Meteo.com](https://open-meteo.com/), [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) | No key (free non-commercial tier) |

Re-download and rebuild (keys in a repo-root `.env`, see `.env.example`):

```bash
cd backend
uv run python scripts/download_data.py        # raw files → backend/data/raw/ (gitignored, ~485 MB)
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
- **Forecast** (Oct–Dec 2024 test period, issued 00:00 CST on the day; M12 model with ERCOT's own load, wind and solar forecasts, day-ahead prices and archived weather forecasts, all published before the issue time): demand MAPE **1.92%** (M10: 2.88%; yesterday: 3.99%; ERCOT's load forecast used directly: 1.71%), wind MAE **1,205 MW** (M4 without weather: 4,089), solar **464 MW** (M4: 1,063). [`docs/results/forecast_metrics.json`](docs/results/forecast_metrics.json)
- **Coordination vs acting alone:** mixed, and shown wherever it lands. On the default demo day (a windy spring night) curtailed wind absorbs all the example companies' load, so every strategy ties and payouts are $0. With the M10 forecast the example coalition avoids 243 t on the sunny mild day; on the summer heat peak the planning-time fallback fires (value of coordination 0 t, though every company's schedule is now 1.75 kt below default); on the cold winter morning coordination loses 40 t to acting alone. In the adoption chart (default day), independent-marginal shifting is the worst strategy above 20% adoption; at 30% coordination recovers 0.86 kt of that and ends 0.4 kt below the default schedule, while the plain average-signal strategy is lowest. Per-day comparison with the M4 forecast: [`docs/results/demo_days_M4.json`](docs/results/demo_days_M4.json) vs [`demo_days_M10.json`](docs/results/demo_days_M10.json).

## Chalk integration

Built for Chalk's "Best Data-Driven Agent Built on Chalk" challenge. Every number below is from a recorded run; the full logs are in `docs/progress.md` and `docs/eval_results*.md`.

```
frontend (Next.js)                    backend (FastAPI)                        Chalk (env hllr1480fa66)
------------------                    -----------------                        ------------------------
BidBuilder ── AssistantPanel ───────► /api/assistant/chat (SSE) ─────────────► Model Gateway: Claude Sonnet 5.5
             (Coordinator Guide)      /api/assistant/suggest   ┐               Online queries: grid_day.* (forecast),
                                      grounding payload ───────┴─────────────►   flex_bid.* (bid features)
           ExtractDropzone ─────────► /api/assistant/extract ────────────────► Function extract-bid (sandbox)
                                                                                  └─► Model Gateway (vision)
ClearinghouseApp ───────────────────► /api/clear, /api/evaluate ─────────────► Online queries (forecast) per clear
scripts/eval_assistant.py, eval_extract.py ──────────────────────────────────► Evaluations (datasets, scorers, LLM judge)
```

| Chalk product | Where | What it does here |
|---|---|---|
| Feature store, online queries | `backend/chalk/` (deployed with `chalk apply`), `forecast/chalk.py` | Serves the day-ahead forecast (`grid_day.*`) on every clear and the per-bid profiles (`flex_bid.*`). Identical to the local model on all demo days. |
| Model Gateway | `assistant/gateway.py`, `assistant/extract.py` | Coordinator Guide answers with Claude Sonnet 5.5 (OpenAI-compatible route, `reasoning_effort="low"`); image extraction uses the gateway's native Messages route (the Chat route cannot carry images). |
| Functions (sandbox) | `backend/chalk_compute/extract_bid.py` | `extract-bid` reads an uploaded bill / chart / schedule / emissions report and returns a validated bid; the API calls it by name. The gateway key reaches it only as the Chalk secret `CHALK_ROUTER_API_KEY`. |
| Evaluations | `backend/scripts/eval_assistant.py`, `eval_extract.py` (`make eval`, `make eval-local`, `make eval-extract`) | The task is the app's own code (or the deployed function) running on Chalk; scorers are deployed functions plus a Chalk `llm_judge`. |

**The agent (Coordinator Guide):** a side panel on the Bids step. It explains every bid field (three personas: EV fleet, data center, facilities), checks feasibility, names the cleanest hours from the simulator's marginal CO2 rates, and proposes edits as cards with **Apply**. Every answer is grounded in a payload built from live app state (the user's bids, the Chalk-served forecast and bid features, the simulator); suggestions are validated before they are shown (real bid, editable field, result feasible). It never quotes savings before a clearing run.

**Evaluation results** (44-question assistant dataset across 3 days and 3 personas; 10 synthetic documents for extraction):

| Run | What | Correct answers | Only grounded numbers | Valid / correct suggestions | LLM judge (explanation quality) |
|---|---|---|---|---|---|
| `c5124fb0` | rule-based fallback, baseline | 0.886 (5 misses: intent routing) | 1.000 | 1.000 / 1.000 | — |
| `e0499809` | rule-based, after fixes | 1.000 | 1.000 | 1.000 / 1.000 | — |
| `0a9478d4` → rescore `e29825cd` | LLM, first run; scorers v2 (meaning, not wording) | 1.000 (v1: 0.409) | 0.994 | 1.000 / 1.000 | 0.668 |
| `5089df6b` | LLM, after the tied-hours grounding fix | 1.000 | 0.991 | 1.000 / 1.000 | **0.750** (cleanest hours 0.56 → 0.72) |
| `6f376d14` | image extraction, 10 documents | window 10/10, energy ±5% 10/10, max MW ±5% 10/10 | — | — | — |

How to read it: the deterministic scorers were rewritten after the first LLM run because they matched only the rule-based wording (tests keep the baseline's real misses failing); a score reached after fixing the rows that exposed a problem is optimistic; extraction documents are synthetic and cleanly stated, so 10/10 shows the pipeline works, not accuracy on messy real bills. Misses that remain: the assistant sometimes quotes a computed figure not in its data (1,800 MWh), and the judge rates feasibility answers lowest (0.68).

**What falls back when credentials are missing** (each response says which path served it):

| Missing | Effect |
|---|---|
| `CHALK_CLIENT_*` / `CHALK_ENVIRONMENT` | Forecast and bid features from the local model (UI: "local model"); extraction cannot reach the Chalk Function. |
| `CHALK_ROUTER_API_KEY` | Coordinator Guide answers with the labeled rule-based assistant; extraction is unavailable (503 with the reason). |
| Chalk Function unreachable | Extraction runs the same code in the API process, direct to the Model Gateway (`path: gateway`). |
| Chalk call fails mid-session | Forecast falls back to local for 60 s (`fallback_reason`); the assistant falls back if the gateway fails before the first token. |

Tokens: the app uses a Chalk service token limited to `query.online` + `deploy.read`; the Model Gateway key is restricted to the Anthropic provider. Deploying (`chalk apply`, `chalk_compute/extract_bid.py`, the evaluation scripts) uses the developer's `chalk login`.

## Key status and placeholders

| Key | Status | Effect |
|---|---|---|
| `EIA_API_KEY`, `ERCOT_*` | set | Raw-data downloads |
| `CHALK_CLIENT_ID`, `CHALK_CLIENT_SECRET`, `CHALK_ENVIRONMENT` | set (in the developer's `.env`) | Forecast, bid features and extraction run on Chalk (see Chalk integration). Without them the local model serves and the UI says so. |
| `CHALK_ROUTER_API_KEY` (+ `CHALK_MODEL`, `CHALK_API_HOST`) | set (in the developer's `.env`) | Coordinator Guide uses Claude Sonnet 5.5 via the Chalk Model Gateway; without it, the labeled rule-based assistant. |
| `DATA_GOV_API_KEY` | **pending** | CAMPD validation is skipped; validation (b) uses EIA-930 fuel mix × eGRID emission factors instead. |

Deviation: the forecast uses scikit-learn's `HistGradientBoostingRegressor` instead of LightGBM, because LightGBM's macOS wheel needs a system `libomp` (`brew install libomp`) that was not installed.

## Limitations (stated, not solved)

- No transmission congestion (single-node merit order); no ramping or unit commitment.
- Bids are self-reported windows; a company could misstate them. Production would set baselines from declared-in-advance or historical profiles.
- The coordinator is greedy and emissions vs load is non-convex, so it is not guaranteed optimal; a planning-time fallback keeps it no worse than acting alone on the forecast (not on actuals).
- Carbon price ($50/t), payout pool ($100,000) and background flexible load (10% of peak) are illustrative, user-adjustable inputs.
- The demo shows the operator/auditor view of all receipts; in production each company would see only its own.
