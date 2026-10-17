# Fixtures (mock data)

**These files are hand-written mock data, not engine output.** They exist so the frontend
can be built against the API contract (docs/design.md, section B6) before the backend is
ready. Milestone M6 regenerates them from real API responses.

The numbers are ERCOT-scale and internally consistent (schedules respect each bid's window,
max MW and energy; hourly CO2 sums to the total; Shapley shares sum to `total_avoided_t`),
because they were produced by a small throwaway toy model rather than typed by hand. They
are **not** real results: plant names, merit order, calibration numbers and curves are
illustrative only. Do not quote them.

| File | Endpoint | Pydantic model (`backend/clearinghouse/schemas.py`) |
|---|---|---|
| `meta.json` | `GET /api/meta` | `MetaResponse` |
| `presets.json` | `GET /api/presets` | `PresetsResponse` |
| `adoption_chart.json` | `GET /api/adoption-chart` | `AdoptionChartResponse` |
| `clear_request.json` | `POST /api/clear` (body) | `ClearRequest` |
| `clear_response.json` | `POST /api/clear` | `ClearResponse` |
| `evaluate_request.json` | `POST /api/evaluate` (body) | `EvaluateRequest` |
| `evaluate_response.json` | `POST /api/evaluate` | `EvaluateResponse` |

Hour convention: slot `h` (0-23) covers `[h:00, h+1:00)`; `earliest_hour` (0-23) is the
inclusive start and `deadline_hour` (1-24) the exclusive end. The mock coordinator trace
uses 10% blocks (30 steps for 3 bids) instead of the engine's 1% blocks.

`backend/tests/test_fixtures_schema.py` validates every file here.
