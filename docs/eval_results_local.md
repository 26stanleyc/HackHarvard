# Coordinator Guide evaluation (local rule-based path)

Run `9fe5aedd-e341-42fd-b236-4f939b1fdb05` on Chalk Evaluations (EvaluationRunStatus.SUCCEEDED), 2026-10-18 03:03, git `212652b`. Dataset `clearinghouse-assistant-v1`, 44 rows; evaluation `clearinghouse-assistant`.

Assistant path in the task: **local** (no Model Gateway key inside the task: the labeled local rule-based answerer, the path live on the site today).

| Scorer | Chalk mean | Rows scored | Local re-score mean | Rows where Chalk = local |
|---|---|---|---|---|
| `suggestions_json_valid` | 1.000 | 44 | 1.000 | 44/44 |
| `numbers_grounded` | 1.000 | 44 | 1.000 | 44/44 |
| `suggestion_matches` | 1.000 | 8 | 1.000 | 44/44 |
| `answer_correct` | 1.000 | 44 | 1.000 | 44/44 |

LLM judge: not run on the local path (keeps `make eval-local` free of model usage).
Task errors: 0.

## How to read this

- The task runs the app's own code on Chalk (`assistant.service.respond`, the chat route's path after grounding) over grounding built from real app state.
- The task has no Model Gateway key, so it measures the labeled local rule-based fallback, not an LLM.
- Fixes made after a run that exposed misses are scored on the same rows, so a later 1.000 is optimistic; `tests/test_assistant.py` checks those fixes on new paraphrases. A fresh, held-out question set would be the stronger test.
- `numbers_grounded` accepts a number within 1% of any number in the grounding (spec tolerance). With ~200 numbers there (MW figures in the thousands), a made-up large number can pass by coincidence; small and exact values cannot.
- `answer_correct` v2 checks meaning (any hour format, "infeasible" / "can't fit", field key or label) after the first LLM run showed v1 matched only the rule-based wording; tests in `tests/test_assistant.py` keep the baseline's real misses failing under v2.
- Chalk names custom-scorer columns by id; each is matched to its scorer by per-row agreement with the local re-score (last column). Scorers whose rows are all identical cannot be told apart, but then their means are equal too.

## Misses

None: every applicable scorer gave 1.0 on every row.

## Earlier runs

- Run `e0499809-5b99-4fce-a1e5-8c043b2079b6` on Chalk Evaluations (EvaluationRunStatus.SUCCEEDED), 2026-10-18 02:05, git `59b84de`. Dataset `clearinghouse-assistant-v1`, 44 rows; evaluation `clearinghouse-assistant`.
  0 miss(es). Scores (Chalk mean / rows / local / agree):
  | `suggestions_json_valid` | 1.000 | 44 | 1.000 | 44/44 |
  | `numbers_grounded` | 1.000 | 44 | 1.000 | 44/44 |
  | `suggestion_matches` | 1.000 | 8 | 1.000 | 44/44 |
  | `answer_correct` | 1.000 | 44 | 1.000 | 44/44 |

- Run `aa05d812-bb33-4784-a57d-e885612d505e` on Chalk Evaluations (EvaluationRunStatus.SUCCEEDED), 2026-10-18 02:04, git `59b84de`. Dataset `clearinghouse-assistant-v1`, 44 rows; evaluation `clearinghouse-assistant`.
  0 miss(es). Scores (Chalk mean / rows / local / agree):
  | `suggestions_json_valid` | 1.000 | 44 | 1.000 | 44/44 |
  | `numbers_grounded` | 1.000 | 44 | 1.000 | 44/44 |
  | `suggestion_matches` | 1.000 | 8 | 1.000 | 44/44 |
  | `answer_correct` | 1.000 | 44 | 1.000 | 44/44 |

- Run `c5124fb0-ed27-4566-b243-5106ce6437a6` on Chalk Evaluations (EvaluationRunStatus.SUCCEEDED), 2026-10-18 01:47, git `59b84de`. Dataset `clearinghouse-assistant-v1`, 44 rows; evaluation `clearinghouse-assistant`.
  5 miss(es). Scores (Chalk mean / rows / local / agree):
  | `suggestions_json_valid` | 1.000 | 44 | 1.000 | 44/44 |
  | `numbers_grounded` | 1.000 | 44 | 1.000 | 44/44 |
  | `suggestion_matches` | 1.000 | 8 | 1.000 | 44/44 |
  | `answer_correct` | 0.886 | 44 | 0.886 | 44/44 |

