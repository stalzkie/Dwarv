# Experiment protocol

This validates the hypothesis in `DWARV_PLAN.md` section 1.2. It is run by
`dwarv eval` (developer tool only; never the end-user surface) against the
frozen task subset in `eval_tasks/subset_v1.json`.

## Protocol (freeze before the final run)

- Same machine, same background load, same llama.cpp build (`configs/models.yaml`'s `llama_cpp.release_tag`), same three bundled models, same frozen task subset, same budget profiles, same sampling parameters (temperature 0.2 baseline, adjusted only by the policy itself for `dwarv`).
- Systems: `fixed` (Baseline A), `retry` (Baseline C), `retry_escalate` (Baseline C+), `dwarv` (Dwarv's actual policy). No optional Baseline B (LMForge) -- not part of the rewritten product plan.
- Equalize total resources: same `max_attempts`, same `time_limit_s` (from the active budget profile, `configs/budgets.yaml`), same three bundled models available to `retry_escalate` and `dwarv`. `fixed`/`retry` are deliberately pinned to a single model (`small`, per `configs/systems.yaml`'s `baselines` section) -- a well-chosen-but-static config, per the hypothesis statement.
- Budget profiles (`configs/budgets.yaml`, MB values derived from the real measured RSS table, not guessed): `static_tight` (small+medium fit, large doesn't), `static_loose` (all three fit comfortably), `squeeze_mid` (starts loose, cuts to small-only after attempt 1).
- Seeds: at least 3 per (system, profile, task) for the frozen final run. Report mean and spread.
- Task source: HumanEval+ (`evalplus.data.get_human_eval_plus`), verified via our own cross-platform sandbox (`verify/evalplus_adapter.py`), not evalplus's own checker -- see `docs/DECISIONS.md` for why.
- Primary metric: verified pass rate (base + plus EvalPlus tests combined) at equal budget.
- Secondary metrics: peak RSS, budget violations, wall-clock latency, attempts used, model-switch overhead.
- Noise sources to record: other processes on the dev machine, page cache state, sampling randomness (each system's seed controls only where applicable -- `fixed`/`retry`/`retry_escalate` don't expose a seed knob beyond the model's own sampling).

## Decision criteria (written in advance, not amended after seeing results)

The hypothesis is **supported** only if `dwarv` beats `retry_escalate` on verified pass rate under `squeeze_mid` by a margin larger than seed-to-seed spread, **and** has fewer budget violations. If `dwarv` only beats `fixed` or `retry` but not `retry_escalate`, report that the gain comes from escalation, not resource awareness. This requires the full protocol (3+ seeds, `squeeze_mid` included) -- **not yet run**; see below.

## Results

### Dry run (`dryrun2`, 2026-10-09) -- harness validation only, NOT the frozen protocol

Per the Step 9 acceptance check ("a small dry run completes end-to-end and produces all tables/plots"), not a hypothesis test. 5 tasks (first 5 of `eval_tasks/subset_v1.json`), 1 seed, `static_loose` profile only -- no `squeeze_mid`, no repeated seeds, so **the decision criteria above cannot be evaluated from this data** and this section draws no conclusion about the hypothesis. Raw data: `eval_results/dryrun2/results.jsonl`; tables/charts: `eval_results/dryrun2/{summary.csv,summary.md,pass_rate.png,rss_vs_pass.png,diff_dwarv_vs_retry_escalate.md}`. (`eval_results/dryrun1/` is an earlier run kept for history; its `fixed`/`retry` rows have `peak_rss_mb=0` because RSS tracking wasn't yet wired into those two baselines -- fixed before `dryrun2`, see `docs/PROGRESS.md`.)

| system | n | pass_rate | 95% CI | mean_peak_rss_mb | budget_violations | mean_wall_s | mean_attempts |
|---|---|---|---|---|---|---|---|
| dwarv | 5 | 0.8 | [0.4, 1.0] | 1756.2 | 0 | 6.87 | 1.4 |
| fixed | 5 | 0.8 | [0.4, 1.0] | 1756.3 | 0 | 7.19 | 1.0 |
| retry | 5 | 0.6 | [0.2, 1.0] | 1756.5 | 0 | 11.34 | 1.8 |
| retry_escalate | 5 | 0.8 | [0.4, 1.0] | 1756.2 | 0 | 8.11 | 1.4 |

All four systems ran on the small model the whole time in this run (no escalation/step-down was triggered under `static_loose` -- there was no resource pressure to react to, and no repeated-failure streak long enough to trigger `retry_escalate`'s or `dwarv`'s escalation rule on these particular 5 tasks). With n=5 and 1 seed, the CIs are wide and overlapping -- exactly what you'd expect from a dry run, not evidence of anything. Harness mechanics confirmed working: real model inference, real sandboxed verification (`verify/evalplus_adapter.py` against our own cross-platform sandbox, not evalplus's Windows-incompatible checker), real JSONL output, real resumability, real bootstrap CIs, real charts.

### Full frozen protocol

**Not yet run.** Would need all 40 tasks in `eval_tasks/subset_v1.json`, all 3 profiles (including `squeeze_mid`, which is where the hypothesis is actually tested), 3+ seeds each, across 4 systems -- 40 x 3 x 3 x 4 = 1,440 task-runs, each potentially involving multiple model loads (load times alone range 6s-86s per the real benchmark in `benchmarks/`). This is real CPU-bound LLM inference time, correctly scoped as 48h-version follow-up work per `DWARV_PLAN.md` section 8, not something to run inside a single development session.

## Interpretation

Not yet supported or refuted -- the full protocol hasn't run. The dry run's only claim is that the harness itself works end to end on real hardware, real models, and real verification, which is what Step 9's acceptance check asks for.
