# Carbon Clearinghouse backend

Python 3.12 / uv project. See `../docs/design.md` for the design and milestones.

```
uv sync
uv run pytest -q
uv run python -m clearinghouse.config   # prints each key name as set/missing (never values)
```
