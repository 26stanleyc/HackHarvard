# Coordinator Guide evaluation (LLM via Chalk Model Gateway)

Run `5089df6b-bf85-4277-9550-883bab2c5250` on Chalk Evaluations (EvaluationRunStatus.SUCCEEDED), 2026-10-18 03:10, git `f8cf76f`. Dataset `clearinghouse-assistant-v1`, 44 rows; evaluation `clearinghouse-assistant-llm`.

Assistant path in the task: **chalk**.

| Scorer | Chalk mean | Rows scored | Local re-score mean | Rows where Chalk = local |
|---|---|---|---|---|
| `suggestions_json_valid` | 1.000 | 44 | 1.000 | 44/44 |
| `numbers_grounded` | 0.991 | 44 | 0.991 | 44/44 |
| `suggestion_matches` | 1.000 | 8 | 1.000 | 44/44 |
| `answer_correct` | 1.000 | 44 | 1.000 | 44/44 |
| `explanation-quality` (LLM judge) | 0.750 | 44 | — | — |

LLM judge: Chalk llm_judge with anthropic/claude-sonnet-5-5 via the Chalk Model Gateway.
Task errors: 0.

## How to read this

- The task runs the app's own code on Chalk (`assistant.service.respond`, the chat route's path after grounding) over grounding built from real app state.
- The task calls Claude through the Chalk Model Gateway (key from a Chalk secret), the path live on the site when the gateway key is set.
- Fixes made after a run that exposed misses are scored on the same rows, so a later 1.000 is optimistic; `tests/test_assistant.py` checks those fixes on new paraphrases. A fresh, held-out question set would be the stronger test.
- `numbers_grounded` accepts a number within 1% of any number in the grounding (spec tolerance). With ~200 numbers there (MW figures in the thousands), a made-up large number can pass by coincidence; small and exact values cannot.
- `answer_correct` v2 checks meaning (any hour format, "infeasible" / "can't fit", field key or label) after the first LLM run showed v1 matched only the rule-based wording; tests in `tests/test_assistant.py` keep the baseline's real misses failing under v2.
- Chalk names custom-scorer columns by id; each is matched to its scorer by per-row agreement with the local re-score (last column). Scorers whose rows are all identical cannot be told apart, but then their means are equal too.

## Misses

- `numbers_grounded` = 0.60 on "What does max power mean?" (2024-08-20, ev_fleet): {"numbers": 5, "ungrounded": [50.0, 7.5]}  
  answer: Max power is the most your depot can draw in any single hour, which is your total charger capacity. In MW, it's roughly the number of chargers times their rating (for example, 50 chargers at 150 kW is 7.5 MW).

It matters because energy divided by max power is the fewest hours you must charge. For y

## Earlier runs

- Run `e29825cd-21b8-4b62-a510-d60d1730f065` on Chalk Evaluations (EvaluationRunStatus.SUCCEEDED), 2026-10-18 02:59, git `212652b`. Dataset `clearinghouse-assistant-v1`, 44 rows; evaluation `clearinghouse-assistant-llm (rescored)` (rescore of run `0a9478d4-8f0a-40fd-9f71-50ec33a547b0`: same task outputs, current scorers).
  1 miss(es). Scores (Chalk mean / rows / local / agree):
  | `suggestions_json_valid` | 1.000 | 44 | 1.000 | 44/44 |
  | `numbers_grounded` | 0.994 | 44 | 0.994 | 44/44 |
  | `suggestion_matches` | 1.000 | 8 | 1.000 | 44/44 |
  | `answer_correct` | 1.000 | 44 | 1.000 | 44/44 |
  | `explanation-quality` (LLM judge) | 0.668 | 44 | — | — |

- Run `10426eba-1535-4b52-a2b8-1bee622a9b1e` on Chalk Evaluations (EvaluationRunStatus.SUCCEEDED), 2026-10-18 02:53, git `212652b`. Dataset `clearinghouse-assistant-v1`, 44 rows; evaluation `clearinghouse-assistant-llm (rescored)` (rescore of run `0a9478d4-8f0a-40fd-9f71-50ec33a547b0`: same task outputs, current scorers).
  2 miss(es). Scores (Chalk mean / rows / local / agree):
  | `suggestions_json_valid` | 1.000 | 44 | 1.000 | 44/44 |
  | `numbers_grounded` | 0.994 | 44 | 0.994 | 44/44 |
  | `suggestion_matches` | 1.000 | 8 | 1.000 | 44/44 |
  | `answer_correct` | 0.977 | 44 | 0.977 | 44/44 |

- Run `0a9478d4-8f0a-40fd-9f71-50ec33a547b0` on Chalk Evaluations (EvaluationRunStatus.SUCCEEDED), 2026-10-18 02:48, git `212652b`. Dataset `clearinghouse-assistant-v1`, 44 rows; evaluation `clearinghouse-assistant-llm`.
  29 miss(es). Scores (Chalk mean / rows / local / agree):
  | `suggestions_json_valid` | 1.000 | 44 | 1.000 | 44/44 |
  | `numbers_grounded` | 0.975 | 44 | 0.975 | 44/44 |
  | `suggestion_matches` | 1.000 | 8 | 1.000 | 44/44 |
  | `answer_correct` | 0.409 | 44 | 0.409 | 44/44 |

