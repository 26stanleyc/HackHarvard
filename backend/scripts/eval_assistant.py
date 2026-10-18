"""Evaluate Coordinator Guide (the bid assistant) on Chalk Evaluations.

    cd backend && uv run python scripts/eval_assistant.py [--path llm|local]   # make eval

``--path llm`` (default; the path live on the site when the Model Gateway key is set): the task
calls Claude through the Chalk Model Gateway. The key reaches the task only as the Chalk secret
``CHALK_ROUTER_API_KEY`` (``chalk secret set``), injected by Chalk at run time. Results:
``docs/eval_results.md``. ``--path local``: the labeled rule-based fallback, no LLM; results:
``docs/eval_results_local.md``.

What runs where:
* Locally: the dataset ``clearinghouse-assistant-v1`` is built from real app state. For each
  row, the grounding payload is built exactly as ``/api/assistant/chat`` builds it (forecast and
  bid features from the provider: Chalk online queries when ``CHALK_*`` are set; the simulator's
  marginal rates), and stored in the row (``grounding_json``) with the expected answer facts.
* On Chalk: the task is a deployed ``@chalkcompute.function`` that runs the app's own
  ``assistant.service.respond`` (the chat code path after grounding) over ``grounding_json``;
  the scorers are deployed functions wrapping ``assistant/eval_scorers.py``. An LLM-judge
  scorer (Chalk ``llm_judge``, through the Model Gateway) is added when the gateway serves the
  configured model.
* Locally again: the result dataset is read back, means are computed (per scorer, over rows
  where it applies), every row is re-scored locally as a cross-check, and
  ``docs/eval_results.md`` is written with the run id and every miss.

Chalk imports this file inside each deployed function, so module top level only defines the
image, the task and the scorers; everything that needs app data runs under ``__main__``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import chalkcompute

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from pydantic import BaseModel, Field  # noqa: E402

# Every deployed function here runs in IMAGE (which ships the clearinghouse package), so this
# light import is safe at module level. The judge gets the same glossary the assistant uses.
from clearinghouse.assistant.glossary import GLOSSARY  # noqa: E402

# Must equal clearinghouse.assistant.eval_dataset.DATASET (checked in main). Not imported at top
# level: Chalk loads this file in every deployed function's container.
DATASET = "clearinghouse-assistant-v1"

EVALUATIONS = {"local": "clearinghouse-assistant", "llm": "clearinghouse-assistant-llm"}
RESULTS = {"local": "eval_results_local.md", "llm": "eval_results.md"}
ENV_ID = "hllr1480fa66"
MODEL = "anthropic/claude-sonnet-5-5"

IMAGE = (
    chalkcompute.Image.debian_slim("3.12")
    .pip_install(["pydantic==2.14.1", "numpy==2.5.4", "openai==3.24.0", "python-dotenv==1.2.4"])
    .add_local_python_source("clearinghouse")
)


def _answer(question: str, grounding_json: str, persona: str, focus_field: str) -> str:
    import json as _json

    from clearinghouse.assistant import service
    from clearinghouse.assistant.eval_scorers import task_output
    from clearinghouse.assistant.models import ChatRequest

    payload = _json.loads(grounding_json)
    req = ChatRequest(day=payload["day"], persona=persona, focus_field=focus_field or None,
                      messages=[{"role": "user", "content": question}])
    return task_output(service.respond(payload, req))


@chalkcompute.function(image=IMAGE)
def clearinghouse_assistant(question: str, grounding_json: str, persona: str,
                            focus_field: str) -> str:
    """Task, local path: no gateway key in the container, so the rule-based answerer runs."""
    return _answer(question, grounding_json, persona, focus_field)


@chalkcompute.function(
    image=IMAGE, call_timeout=120.0,
    secrets=[chalkcompute.Secret.from_chalk_env("CHALK_ROUTER_API_KEY")],
    env={"CHALK_ENVIRONMENT": ENV_ID, "CHALK_API_HOST": "https://api.chalk.ai",
         "CHALK_MODEL": MODEL},
)
def clearinghouse_assistant_llm(question: str, grounding_json: str, persona: str,
                                focus_field: str) -> str:
    """Task, LLM path: Claude through the Chalk Model Gateway (key from the Chalk secret)."""
    return _answer(question, grounding_json, persona, focus_field)


TASKS = {"local": clearinghouse_assistant, "llm": clearinghouse_assistant_llm}


class ExplanationQuality(BaseModel):
    # No ge/le: Claude structured outputs reject minimum/maximum on numbers (HTTP 400 in run
    # 0a9478d4); the range is stated in the description instead.
    score: float = Field(description="Overall quality, a number from 0 to 1 inclusive.")
    correct: bool = Field(description="Is the explanation factually correct per the glossary?")
    reason: str


# LLM judge (Chalk llm_judge, through the Model Gateway as this environment). Defined at module
# level so Chalk can import it in its container; it only runs in the LLM evaluation.
EXPLANATION_QUALITY = chalkcompute.scorers.llm_judge(
    ExplanationQuality, model=MODEL, name="explanation-quality", image=IMAGE,
    inputs=("output", "expected_field", "question"),
    instructions=(
        "You grade an assistant that explains electricity-bid fields to non-experts. The "
        "output is JSON; grade its 'answer'. If expected_field is non-empty: is the explanation "
        "of that field correct per the glossary below, and understandable to a non-expert? If "
        "expected_field is empty, grade whether the answer addresses the question clearly "
        "without inventing numbers.\n\nGlossary:\n"
        + "\n".join(f"- {k} ({g['label']}, {g['unit']}): {g['definition']}"
                    for k, g in GLOSSARY.items())))


Results = chalkcompute.EvaluationScorerResult


def _result(score_meta) -> list[Results]:
    score, meta = score_meta
    if score is None:
        return []  # scorer does not apply to this row: emit nothing
    return [chalkcompute.EvaluationScorerResult(score=float(score), metadata=meta)]


@chalkcompute.function(image=IMAGE)
def ch_suggestions_json_valid(output: str) -> list[Results]:
    from clearinghouse.assistant import eval_scorers as S
    return _result(S.suggestions_json_valid(output))


@chalkcompute.function(image=IMAGE)
def ch_numbers_grounded(output: str, grounding_json: str, question: str,
                        expected_numbers_json: str) -> list[Results]:
    from clearinghouse.assistant import eval_scorers as S
    return _result(S.numbers_grounded(output, grounding_json, question, expected_numbers_json))


@chalkcompute.function(image=IMAGE)
def ch_suggestion_matches(output: str,
                          expected_suggestion_json: str) -> list[Results]:
    from clearinghouse.assistant import eval_scorers as S
    return _result(S.suggestion_matches(output, expected_suggestion_json))


@chalkcompute.function(image=IMAGE)
def ch_answer_correct(output: str, expected_json: str, bids_json: str) -> list[Results]:
    from clearinghouse.assistant import eval_scorers as S
    return _result(S.answer_correct(output, expected_json, bids_json))


SCORER_FNS = {
    "suggestions_json_valid": ch_suggestions_json_valid,
    "numbers_grounded": ch_numbers_grounded,
    "suggestion_matches": ch_suggestion_matches,
    "answer_correct": ch_answer_correct,
}


# --- run -----------------------------------------------------------------------------------------


def main(path: str = "llm", rescore: str | None = None) -> None:
    import datetime as dt
    import subprocess
    import tempfile

    import pyarrow as pa

    from clearinghouse.assistant import eval_scorers as S

    sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True,
                         text=True, cwd=BACKEND).stdout.strip()
    judge_table = None
    if rescore:
        # Score an existing run's task outputs with the current scorers (no task re-run, no
        # model usage); the LLM judge's scores are read from the original run.
        original = chalkcompute.EvaluationRun.from_id(rescore)
        EVALUATION = f"{EVALUATIONS[path]} (rescored)"
        rescorers = list(SCORER_FNS.values()) + ([EXPLANATION_QUALITY] if path == "llm" else [])
        run = original.rescore(scorers=rescorers, name=EVALUATION,
                               metadata={"git_sha": sha, "path": path,
                                         "rescore_of": rescore}).wait()
        print("rescore run", run.id, run.status, "of", rescore)
        judge_note = (f"Chalk llm_judge with {MODEL} via the Chalk Model Gateway (rescore)"
                      if path == "llm" else "not run")
    else:
        # Rows (and the gateway check) are built in a subprocess: building them loads the repo
        # .env, whose CHALK_CLIENT_* are the app's query-only token. This process keeps using the
        # developer's `chalk login` for the dataset upload and the evaluation run.
        with tempfile.TemporaryDirectory() as tmp:
            rows_file = Path(tmp) / "rows.json"
            subprocess.run([sys.executable, "-m", "clearinghouse.assistant.eval_dataset",
                            str(rows_file)], check=True, cwd=BACKEND)
            built = json.loads(rows_file.read_text())
        rows, gw = built["rows"], built["gateway"]
        print(f"dataset {DATASET}: {len(rows)} rows")
        from clearinghouse.assistant.eval_dataset import DATASET as BUILT_DATASET

        assert BUILT_DATASET == DATASET, (BUILT_DATASET, DATASET)
        scorers = list(SCORER_FNS.values())
        judge_note = ("not run on the local path (keeps `make eval-local` free of model usage)"
                      if path == "local" else "")
        if path == "llm":
            scorers.append(EXPLANATION_QUALITY)
            judge_note = f"Chalk llm_judge with {MODEL} via the Chalk Model Gateway"

        if path == "llm" and gw["gateway"] != "chalk":
            raise SystemExit(f"--path llm needs the Model Gateway: {gw['reason']}")
        EVALUATION = EVALUATIONS[path]
        dataset = chalkcompute.DatasetClient().upload(DATASET, rows)
        evaluation = chalkcompute.Evaluation.create(EVALUATION, dataset=dataset,
                                                    task=TASKS[path], scorers=scorers)
        run = evaluation.run(metadata={"git_sha": sha, "path": path}).wait()
        print("run", run.id, run.status)
    table: pa.Table = chalkcompute.DatasetClient().read(run.result_dataset)
    res = table.to_pylist()

    cols = table.column_names
    task_errors = [r for r in res if r.get("output_error")]

    def chalk_score(v):
        """A scorer cell -> float or None (scorers here return list[EvaluationScorerResult])."""
        if isinstance(v, str):
            try:
                v = json.loads(v)
            except json.JSONDecodeError:
                return None
        if isinstance(v, list):
            v = v[0] if v else None
        if isinstance(v, dict):
            v = v.get("score", v.get("value"))
        return float(v) if isinstance(v, int | float) and not isinstance(v, bool) else None

    # Local cross-check: the same scorer code on the task outputs Chalk produced.
    local_rows = [S.score_row(r["output"], r) if r.get("output") else {} for r in res]
    local_scores = {n: [lr[n][0] for lr in local_rows if lr and lr[n][0] is not None]
                    for n in S.SCORERS}
    misses = [(n, r["question"], r["day"], r["persona"], v, meta,
               json.loads(r["output"]).get("answer", ""))
              for r, lr in zip(res, local_rows, strict=True) for n, (v, meta) in lr.items()
              if v is not None and v < 1]

    # Chalk names custom-scorer columns by an internal id (scorer_<id>), so each column is
    # matched to the scorer whose local per-row scores it reproduces; the match is reported.
    scorer_cols = [c for c in cols if c.startswith("scorer_") and not c.endswith("_error")]
    chalk_vals = {c: [chalk_score(r.get(c)) for r in res] for c in scorer_cols}
    means, unmatched = {}, list(scorer_cols)
    for name in S.SCORERS:
        local_col = [lr[name][0] if lr else None for lr in local_rows]
        best, agree = None, -1
        for c in unmatched:
            k = sum(1 for a, b in zip(chalk_vals[c], local_col, strict=True)
                    if (a is None and b is None) or (a is not None and b is not None
                                                     and abs(a - b) < 1e-6))
            if k > agree:
                best, agree = c, k
        if best is None:
            means[name] = (None, 0, None, 0)
            continue
        unmatched.remove(best)
        vals = [v for v in chalk_vals[best] if v is not None]
        means[name] = (sum(vals) / len(vals) if vals else None, len(vals), best, agree)
    judge_cols = unmatched + [c for c in cols if "explanation" in c.lower().replace("-", "_")
                              and not c.endswith(("_error", "_metadata"))]
    judge_vals = [v for c in judge_cols
                  for v in (chalk_vals.get(c) or [chalk_score(r.get(c)) for r in res])
                  if v is not None]
    if judge_table is not None:
        jres = judge_table.to_pylist()
        judge_cols = [c for c in judge_table.column_names if "explanation" in c.lower()
                      and not c.endswith(("_error", "_metadata"))]
        judge_vals = [v for c in judge_cols for v in (chalk_score(r.get(c)) for r in jres)
                      if v is not None]
    print("judge columns:", judge_cols)
    llm_paths = sorted({json.loads(r["output"]).get("llm") for r in res if r.get("output")})

    lines = [
        "# Coordinator Guide evaluation ("
        + ("LLM via Chalk Model Gateway" if path == "llm" else "local rule-based path") + ")", "",
        f"Run `{run.id}` on Chalk Evaluations ({run.status}), "
        f"{dt.datetime.now().strftime('%Y-%m-%d %H:%M')}, git `{sha}`. "
        f"Dataset `{DATASET}`, {len(res)} rows; evaluation `{EVALUATION}`"
        + (f" (rescore of run `{rescore}`: same task outputs, current scorers)" if rescore
           else "") + ".", "",
        f"Assistant path in the task: **{', '.join(p or '?' for p in llm_paths)}**"
        + (" (no Model Gateway key inside the task: the labeled local rule-based answerer, "
           "the path live on the site today)" if llm_paths == ["local"] else "") + ".", "",
        "| Scorer | Chalk mean | Rows scored | Local re-score mean | Rows where Chalk = local |",
        "|---|---|---|---|---|",
    ]
    for name in S.SCORERS:
        m, n, _, agree = means[name]
        loc = local_scores[name]
        lm = sum(loc) / len(loc) if loc else None
        lines.append(f"| `{name}` | {'—' if m is None else f'{m:.3f}'} | {n} | "
                     f"{'—' if lm is None else f'{lm:.3f}'} | {agree}/{len(res)} |")
    if judge_vals:
        lines.append(f"| `explanation-quality` (LLM judge) | "
                     f"{sum(judge_vals) / len(judge_vals):.3f} | {len(judge_vals)} | — | — |")
    lines += ["", f"LLM judge: {judge_note}.",
              f"Task errors: {len(task_errors)}.", "", "## How to read this", "",
              "- The task runs the app's own code on Chalk (`assistant.service.respond`, the "
              "chat route's path after grounding) over grounding built from real app state.",
              ("- The task calls Claude through the Chalk Model Gateway (key from a Chalk "
               "secret), the path live on the site when the gateway key is set."
               if path == "llm" else
               "- The task has no Model Gateway key, so it measures the labeled local "
               "rule-based fallback, not an LLM."),
              "- Fixes made after a run that exposed misses are scored on the same rows, so a "
              "later 1.000 is optimistic; `tests/test_assistant.py` checks those fixes on new "
              "paraphrases. A fresh, held-out question set would be the stronger test.",
              "- `numbers_grounded` accepts a number within 1% of any number in the grounding "
              "(spec tolerance). With ~200 numbers there (MW figures in the thousands), a "
              "made-up large number can pass by coincidence; small and exact values cannot.",
              "- `answer_correct` v2 checks meaning (any hour format, \"infeasible\" / "
              "\"can't fit\", field key or label) after the first LLM run showed v1 matched "
              "only the rule-based wording; tests in `tests/test_assistant.py` keep the "
              "baseline's real misses failing under v2.",
              "- Chalk names custom-scorer columns by id; each is matched to its scorer by "
              "per-row agreement with the local re-score (last column). Scorers whose rows are "
              "all identical cannot be told apart, but then their means are equal too.",
              "", "## Misses", ""]
    if not misses:
        lines.append("None: every applicable scorer gave 1.0 on every row.")
    for n, q, day, persona, v, meta, ans in misses:
        lines.append(f"- `{n}` = {v:.2f} on \"{q}\" ({day}, {persona}): {json.dumps(meta)}  ")
        lines.append(f"  answer: {ans[:300]}")
    out = BACKEND.parent / "docs" / RESULTS[path]
    lines += ["", "## Earlier runs", ""] + _history(out)
    out.write_text("\n".join(lines) + "\n")
    print("\n".join(lines[:14 + len(S.SCORERS)]))
    print("sample scorer cell:", repr(res[0].get(scorer_cols[0]))[:200] if scorer_cols else None)
    print(f"wrote {out}")


def _history(path: Path) -> list[str]:
    """The previous file's run line + score table (and its own history), kept below."""
    if not path.is_file():
        return []
    old = path.read_text().splitlines()
    head = next((ln for ln in old if ln.startswith("Run `")), None)
    table = [ln for ln in old[: old.index("## Misses")] if ln.startswith("| `")] \
        if "## Misses" in old else []
    n_miss = sum(1 for ln in old[old.index("## Misses"):] if ln.startswith("- `")) \
        if "## Misses" in old else 0
    prev = old[old.index("## Earlier runs") + 2:] if "## Earlier runs" in old else []
    if not head:
        return prev
    cur = [f"- {head}", f"  {n_miss} miss(es). Scores (Chalk mean / rows / local / agree):"]
    cur += [f"  {ln}" for ln in table]
    return cur + [""] + [ln for ln in prev if ln.strip() or ln == ""]


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--path", choices=("llm", "local"), default="llm")
    ap.add_argument("--rescore", metavar="RUN_ID",
                    help="score an existing run's outputs with the current scorers")
    a = ap.parse_args()
    main(a.path, a.rescore)
