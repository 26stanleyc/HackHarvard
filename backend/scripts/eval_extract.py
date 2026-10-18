"""Evaluate bid extraction from images on Chalk Evaluations (Phase 4, image part).

    cd backend && uv run python scripts/eval_extract.py      # or: make eval-extract

Dataset ``clearinghouse-extract-v1``: the 10 synthetic documents in ``fixtures/extract/`` (made
by ``scripts/make_extract_fixtures.py``) with their answer keys (``ground_truth.json``). Task: the
deployed Chalk Function ``extract-bid`` itself (``chalk_compute/extract_bid.py``; importing it
deploys the current version). Scorers (deployed functions): the window exactly right, energy
within 5%, max power within 5%, and all three at once. Writes
``docs/eval_results_extract.md`` with the run id and every miss. About 10 vision calls per run.
"""

from __future__ import annotations

import base64
import json
import subprocess
import sys
from pathlib import Path

import chalkcompute

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
sys.path.insert(0, str(BACKEND))

# Chalk imports this file in each scorer's container, so the module level must not import
# chalk_compute/extract_bid.py (not shipped there); main() imports it, which also deploys it.
IMAGE = chalkcompute.Image.debian_slim("3.12")

DATASET = "clearinghouse-extract-v1"
EVALUATION = "clearinghouse-extract"
TOL = 0.05
Results = chalkcompute.EvaluationScorerResult


def _parse(output: str) -> dict:
    try:
        o = json.loads(output)
        return o if isinstance(o, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


def check(output: str, truth_json: str) -> dict[str, tuple[float, dict]]:
    """The four scores for one row (plain function: used on Chalk and for the local check)."""
    got, t = _parse(output), json.loads(truth_json)
    if "error" in got or not got:
        miss = {"error": got.get("error", "no output")}
        return {k: (0.0, miss) for k in ("window", "energy", "max_mw", "all")}
    window = (got.get("earliest_hour"), got.get("deadline_hour")) == (t["earliest_hour"],
                                                                     t["deadline_hour"])

    def close(k):
        v = got.get(k)
        return isinstance(v, int | float) and abs(v - t[k]) <= TOL * abs(t[k])

    res = {"window": window, "energy": close("energy_mwh"), "max_mw": close("max_mw")}
    res["all"] = all(res.values())
    meta = {"got": {k: got.get(k) for k in ("earliest_hour", "deadline_hour", "energy_mwh",
                                            "max_mw", "confidence")},
            "truth": {k: t[k] for k in ("earliest_hour", "deadline_hour", "energy_mwh", "max_mw")}}
    return {k: (1.0 if v else 0.0, meta) for k, v in res.items()}


@chalkcompute.function(image=IMAGE)
def ch_extract_window(output: str, truth_json: str) -> list[Results]:
    s, m = check(output, truth_json)["window"]
    return [chalkcompute.EvaluationScorerResult(score=s, metadata=m)]


@chalkcompute.function(image=IMAGE)
def ch_extract_energy(output: str, truth_json: str) -> list[Results]:
    s, m = check(output, truth_json)["energy"]
    return [chalkcompute.EvaluationScorerResult(score=s, metadata=m)]


@chalkcompute.function(image=IMAGE)
def ch_extract_max_mw(output: str, truth_json: str) -> list[Results]:
    s, m = check(output, truth_json)["max_mw"]
    return [chalkcompute.EvaluationScorerResult(score=s, metadata=m)]


@chalkcompute.function(image=IMAGE)
def ch_extract_all(output: str, truth_json: str) -> list[Results]:
    s, m = check(output, truth_json)["all"]
    return [chalkcompute.EvaluationScorerResult(score=s, metadata=m)]


SCORERS = {"window": ch_extract_window, "energy": ch_extract_energy,
           "max_mw": ch_extract_max_mw, "all": ch_extract_all}


def main() -> None:
    import datetime as dt

    sys.path.insert(0, str(BACKEND / "chalk_compute"))
    from extract_bid import extract_bid  # deploys the current version of the function

    truth = json.loads((REPO / "fixtures" / "extract" / "ground_truth.json").read_text())
    fx = REPO / "fixtures" / "extract"
    rows = [{"file": f, "kind": t["kind"], "mime": "image/png",
             "image_b64": base64.b64encode((fx / f).read_bytes()).decode(),
             "truth_json": json.dumps(t)} for f, t in sorted(truth.items())]
    sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                         cwd=BACKEND).stdout.strip()
    dataset = chalkcompute.DatasetClient().upload(DATASET, rows)
    evaluation = chalkcompute.Evaluation.create(EVALUATION, dataset=dataset, task=extract_bid,
                                                scorers=list(SCORERS.values()))
    run = evaluation.run(metadata={"git_sha": sha}).wait()
    print("run", run.id, run.status)
    res = chalkcompute.DatasetClient().read(run.result_dataset).to_pylist()

    def score(v):
        if isinstance(v, str):
            try:
                v = json.loads(v)
            except json.JSONDecodeError:
                return None
        if isinstance(v, list):
            v = v[0] if v else None
        if isinstance(v, dict):
            v = v.get("score")
        return float(v) if isinstance(v, int | float) and not isinstance(v, bool) else None

    # Chalk names scorer columns by id: match each to its scorer by per-row agreement with the
    # local check of the same outputs (reported).
    local = [check(r.get("output") or "", r["truth_json"]) for r in res]
    cols = [c for c in (res[0] if res else {}) if c.startswith("scorer_")
            and not c.endswith("_error")]
    lines = ["# Bid extraction from images — evaluation", "",
             f"Run `{run.id}` on Chalk Evaluations ({run.status}), "
             f"{dt.datetime.now().strftime('%Y-%m-%d %H:%M')}, git `{sha}`. Dataset `{DATASET}` "
             f"({len(rows)} synthetic documents, `fixtures/extract/`); task: the Chalk Function "
             "`extract-bid` (Claude Sonnet 5.5 vision via the Chalk Model Gateway).", "",
             "| Scorer | Chalk mean | Local re-score | Rows where Chalk = local |",
             "|---|---|---|---|"]
    unmatched = list(cols)
    for name in SCORERS:
        loc = [lr[name][0] for lr in local]
        best, agree = None, -1
        for c in unmatched:
            k = sum(1 for r, lv in zip(res, loc, strict=True) if score(r.get(c)) == lv)
            if k > agree:
                best, agree = c, k
        if best:
            unmatched.remove(best)
        vals = [score(r.get(best)) for r in res] if best else []
        vals = [v for v in vals if v is not None]
        label = {"window": "window exact", "energy": "energy ±5%", "max_mw": "max MW ±5%",
                 "all": "all three"}[name]
        lines.append(f"| {label} | {sum(vals) / len(vals):.2f} | {sum(loc) / len(loc):.2f} | "
                     f"{agree}/{len(res)} |" if vals else f"| {label} | — | — | — |")
    lines += ["", f"Task errors: {sum(1 for r in res if r.get('output_error'))}.", "",
              "Documents are fictional and carry a SAMPLE footer; formats follow public "
              "descriptions of Texas commercial bills, GHG Protocol Scope 2 inventories, depot "
              "charging plans and load charts. Several need arithmetic (billing-period kWh / days, "
              "power x hours, a 94% share). The first answer key for `bill_01` ignored that 94% "
              "share; the model's first extraction caught it and the key was corrected before "
              "this evaluation.", "", "## Results by document", ""]
    for r, lr in zip(res, local, strict=True):
        meta = lr["all"][1]
        mark = "match" if lr["all"][0] == 1 else "MISS"
        got = json.dumps(meta.get("got", meta))
        lines.append(f"- `{r['file']}` ({r['kind']}): **{mark}**. got {got}; "
                     f"truth {json.dumps(meta.get('truth', {}))}")
    out = REPO / "docs" / "eval_results_extract.md"
    out.write_text("\n".join(lines) + "\n")
    print("\n".join(lines[4:10]))
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
