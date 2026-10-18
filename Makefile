# Repo-level shortcuts. Backend commands run with uv from backend/.
.PHONY: test lint eval

test:
	cd backend && uv run pytest -q

lint:
	cd backend && uv run ruff check .
	cd frontend && npm run lint && npm run typecheck

# Coordinator Guide evaluation on Chalk Evaluations (needs `chalk login`); writes
# docs/eval_results.md. See backend/scripts/eval_assistant.py.
eval:
	cd backend && uv run python scripts/eval_assistant.py
