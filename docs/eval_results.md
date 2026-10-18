# Coordinator Guide evaluation (LLM via Chalk Model Gateway)

Run `e29825cd-21b8-4b62-a510-d60d1730f065` on Chalk Evaluations (EvaluationRunStatus.SUCCEEDED), 2026-10-18 02:59, git `212652b`. Dataset `clearinghouse-assistant-v1`, 44 rows; evaluation `clearinghouse-assistant-llm (rescored)` (rescore of run `0a9478d4-8f0a-40fd-9f71-50ec33a547b0`: same task outputs, current scorers).

Assistant path in the task: **chalk**.

| Scorer | Chalk mean | Rows scored | Local re-score mean | Rows where Chalk = local |
|---|---|---|---|---|
| `suggestions_json_valid` | 1.000 | 44 | 1.000 | 44/44 |
| `numbers_grounded` | 0.994 | 44 | 0.994 | 44/44 |
| `suggestion_matches` | 1.000 | 8 | 1.000 | 44/44 |
| `answer_correct` | 1.000 | 44 | 1.000 | 44/44 |
| `explanation-quality` (LLM judge) | 0.668 | 44 | — | — |

LLM judge: Chalk llm_judge with anthropic/claude-sonnet-5-5 via the Chalk Model Gateway (rescore).
Task errors: 0.

## How to read this

- The task runs the app's own code on Chalk (`assistant.service.respond`, the chat route's path after grounding) over grounding built from real app state.
- The task calls Claude through the Chalk Model Gateway (key from a Chalk secret), the path live on the site when the gateway key is set.
- Fixes made after a run that exposed misses are scored on the same rows, so a later 1.000 is optimistic; `tests/test_assistant.py` checks those fixes on new paraphrases. A fresh, held-out question set would be the stronger test.
- `numbers_grounded` accepts a number within 1% of any number in the grounding (spec tolerance). With ~200 numbers there (MW figures in the thousands), a made-up large number can pass by coincidence; small and exact values cannot.
- `answer_correct` v2 checks meaning (any hour format, "infeasible" / "can't fit", field key or label) after the first LLM run showed v1 matched only the rule-based wording; tests in `tests/test_assistant.py` keep the baseline's real misses failing under v2.
- Chalk names custom-scorer columns by id; each is matched to its scorer by per-row agreement with the local re-score (last column). Scorers whose rows are all identical cannot be told apart, but then their means are equal too.

## Misses

- `numbers_grounded` = 0.75 on "How should I set energy?" (2024-03-29, facilities): {"numbers": 4, "ungrounded": [1800.0]}  
  answer: Energy is the total MWh your chillers and ice storage need inside the occupancy window. The coordinator decides when it's delivered, never how much, so the full amount is always scheduled. Set it to what the buildings actually need to be cool by occupancy.

Check it against chiller capacity: energy 

## Earlier runs

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

