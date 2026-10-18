# Repo-level shortcuts. Backend commands run with uv from backend/.
.PHONY: test lint eval eval-local

test:
	cd backend && uv run pytest -q

lint:
	cd backend && uv run ruff check .
	cd frontend && npm run lint && npm run typecheck

# Coordinator Guide evaluation on Chalk Evaluations (needs `chalk login`). `make eval` runs the
# LLM path (Chalk Model Gateway; docs/eval_results.md, about $0.50-1 of model usage per run);
# `make eval-local` the rule-based fallback (docs/eval_results_local.md, no model usage).
eval:
	cd backend && uv run python scripts/eval_assistant.py --path llm

eval-local:
	cd backend && uv run python scripts/eval_assistant.py --path local
