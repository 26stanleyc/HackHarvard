# Carbon Clearinghouse backend

Python 3.12 / uv project. See `../docs/design.md` for the design and milestones.

```
uv sync
uv run pytest -q
uv run python -m clearinghouse.config   # prints each key name as set/missing (never values)
```

## API (M6)

```
uv run uvicorn clearinghouse.api:app --port 8000      # from backend/
scripts/smoke_api.sh [http://localhost:8000]          # curl every endpoint (server must be running)
uv run python scripts/build_adoption_chart.py         # data/processed/adoption_chart.json (+ _meta)
uv run python scripts/regenerate_fixtures.py          # fixtures/*.json from real responses
```

Endpoints: `GET /api/meta`, `/api/presets`, `/api/adoption-chart`, `/api/health`;
`POST /api/clear`, `/api/evaluate` (contract: `clearinghouse/schemas.py`, design B6). Extra CORS
origins: `FRONTEND_ORIGINS` (comma-separated). Carbon price $50/t, pool $100,000 and background
10% of forecast peak are illustrative defaults.
