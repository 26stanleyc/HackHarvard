# Bid extraction from images — evaluation

Run `6f376d14-8f34-4006-b1c3-9f8f0492269d` on Chalk Evaluations (EvaluationRunStatus.SUCCEEDED), 2026-10-18 03:18, git `f8cf76f`. Dataset `clearinghouse-extract-v1` (10 synthetic documents, `fixtures/extract/`); task: the Chalk Function `extract-bid` (Claude Sonnet 5.5 vision via the Chalk Model Gateway).

| Scorer | Chalk mean | Local re-score | Rows where Chalk = local |
|---|---|---|---|
| window exact | 1.00 | 1.00 | 10/10 |
| energy ±5% | 1.00 | 1.00 | 10/10 |
| max MW ±5% | 1.00 | 1.00 | 10/10 |
| all three | 1.00 | 1.00 | 10/10 |

Task errors: 0.

Documents are fictional and carry a SAMPLE footer; formats follow public descriptions of Texas commercial bills, GHG Protocol Scope 2 inventories, depot charging plans and load charts. Several need arithmetic (billing-period kWh / days, power x hours, a 94% share). The first answer key for `bill_01` ignored that 94% share; the model's first extraction caught it and the key was corrected before this evaluation.

## Results by document

- `load_chart_01.png` (hourly load chart (data center)): **match**. got {"earliest_hour": 0, "deadline_hour": 24, "energy_mwh": 120.0, "max_mw": 20.0, "confidence": 0.9}; truth {"earliest_hour": 0, "deadline_hour": 24, "energy_mwh": 120, "max_mw": 20}
- `scope2_report_01.png` (Scope 2 emissions report (cold storage)): **match**. got {"earliest_hour": 0, "deadline_hour": 16, "energy_mwh": 160.0, "max_mw": 20.0, "confidence": 0.95}; truth {"earliest_hour": 0, "deadline_hour": 16, "energy_mwh": 160, "max_mw": 20}
- `load_chart_02.png` (charging load chart (school buses)): **match**. got {"earliest_hour": 9, "deadline_hour": 15, "energy_mwh": 6.0, "max_mw": 1.2, "confidence": 0.85}; truth {"earliest_hour": 9, "deadline_hour": 15, "energy_mwh": 6.0, "max_mw": 1.2}
- `load_chart_03.png` (charging load chart (delivery vans)): **match**. got {"earliest_hour": 1, "deadline_hour": 6, "energy_mwh": 5.4, "max_mw": 1.5, "confidence": 0.85}; truth {"earliest_hour": 1, "deadline_hour": 6, "energy_mwh": 5.4, "max_mw": 1.5}
- `job_queue_01.png` (job scheduler screen (training run)): **match**. got {"earliest_hour": 6, "deadline_hour": 24, "energy_mwh": 120.0, "max_mw": 8.0, "confidence": 0.95}; truth {"earliest_hour": 6, "deadline_hour": 24, "energy_mwh": 120, "max_mw": 8}
- `charging_schedule_01.png` (fleet charging schedule): **match**. got {"earliest_hour": 18, "deadline_hour": 24, "energy_mwh": 9.6, "max_mw": 2.4, "confidence": 0.95}; truth {"earliest_hour": 18, "deadline_hour": 24, "energy_mwh": 9.6, "max_mw": 2.4}
- `bill_02.png` (chiller sub-meter statement (offices)): **match**. got {"earliest_hour": 4, "deadline_hour": 9, "energy_mwh": 18.0, "max_mw": 6.0, "confidence": 0.93}; truth {"earliest_hour": 4, "deadline_hour": 9, "energy_mwh": 18, "max_mw": 6}
- `bill_03.png` (utility bill (water pumping)): **match**. got {"earliest_hour": 0, "deadline_hour": 16, "energy_mwh": 96.0, "max_mw": 12.0, "confidence": 0.9}; truth {"earliest_hour": 0, "deadline_hour": 16, "energy_mwh": 96, "max_mw": 12}
- `bill_01.png` (utility bill (EV depot)): **match**. got {"earliest_hour": 0, "deadline_hour": 6, "energy_mwh": 7.332, "max_mw": 1.6, "confidence": 0.8}; truth {"earliest_hour": 0, "deadline_hour": 6, "energy_mwh": 7.332, "max_mw": 1.6}
- `bill_04.png` (cold storage invoice (MWh units)): **match**. got {"earliest_hour": 20, "deadline_hour": 24, "energy_mwh": 24.0, "max_mw": 8.0, "confidence": 0.85}; truth {"earliest_hour": 20, "deadline_hour": 24, "energy_mwh": 24, "max_mw": 8}
