# sage.ai backend

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

## Deploying the API on Vercel

The API runs as one Vercel Function (FastAPI support; entrypoint `[tool.vercel]` in
`pyproject.toml`, startup warm-up via the lifespan). `.vercelignore` keeps the upload to
`clearinghouse/` and `data/processed/` (the Chalk project in `chalk/` alone is ~650 MB). One file
lives outside `backend/`: copy `../docs/results/calibration.json` to `data/processed/` in the
directory you deploy from (the API falls back to that copy), then from that directory:

```
vercel link --project sage-ai-api
vercel env add CHALK_CLIENT_ID production        # and CHALK_CLIENT_SECRET, CHALK_ENVIRONMENT
vercel env add FRONTEND_ORIGINS production       # the frontend URLs, comma-separated
vercel deploy --prod
```

Without the `CHALK_*` variables the local forecast model serves (the UI says so). The frontend
then needs `NEXT_PUBLIC_API_MODE=live` and `NEXT_PUBLIC_API_BASE=https://<api host>` at build time.
