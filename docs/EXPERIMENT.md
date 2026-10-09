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

#### Proper paired-comparison statistics (added after the dry run, researched before building)

Comparing two independent bootstrap CIs (as above) is the wrong test for this data: `dwarv` and `fixed` ran the identical 5 tasks, so the outcomes are *paired*, not independent samples -- treating them as independent throws away the information that both systems saw exactly the same task difficulty. The correct tests, researched before implementing (not guessed):

- **McNemar's exact test** for paired binary pass/fail outcomes on identical tasks -- the standard method in matched-pairs classifier comparisons, more powerful than unpaired tests because it isolates only the tasks where the two systems disagree. ([Exact McNemar's Test, Fay](https://cran.r-project.org/web/packages/exact2x2/vignettes/exactMcNemar.pdf); [McNemar's Test: The Hidden Gem for Paired Binary Data](https://jameshoward.us/2024/12/17/mcnemars-test-the-hidden-gem-for-paired-binary-data/))
- **Wilcoxon signed-rank test**, the paired analog for continuous metrics (`wall_s`, `peak_rss_mb`) on the same tasks.
- **Sample size**: detecting a medium effect size in a pass-rate comparison at standard power (0.8) and significance (α=0.05) typically needs on the order of 100-138+ tasks per condition for an unpaired proportion test -- confirming numerically why n=5 (or even the frozen protocol's 40 tasks x 3 seeds, which aren't fully independent since the same 40 tasks repeat) is nowhere near enough to detect anything but a large effect.

Implemented in `eval/analyze.py`: `mcnemar_test()`, `wilcoxon_signed_rank()`, `write_significance_report()` (CSV + Markdown), `write_with_vs_without_chart()` (focused `dwarv` vs `fixed` bar chart, i.e. "with Dwarv" vs "without Dwarv" -- `fixed` being the closest thing to no wrapper at all: one model, one generation, no retry, no resource-awareness). Run for real against `dryrun2`:

`dwarv` vs `fixed`, `static_loose`: 0 discordant pairs (both systems passed the identical 4 of 5 tasks and failed the identical 1) -- McNemar p=1.0. `wall_s` p=0.625, `peak_rss_mb` p=0.4375 (Wilcoxon). All non-significant, which is the honest, correct conclusion at this n -- not "no difference," but **not enough data to tell**, now backed by the statistically appropriate test rather than eyeballing overlapping CIs. See `eval_results/dryrun2/{significance.csv,significance.md,with_vs_without.png}`.

### A real case where Dwarv did not help (honesty check, Step 10)

Per `DWARV_PLAN.md` Step 10's "also show one case where Dwarv does not help" --
this is a genuine result from `dryrun2`, not staged: on `HumanEval/103`
(`rounded_avg(n, m)`: average two integers, round, return as a binary
string like `"0b11"`, or `-1` if `n > m`), the `dwarv` system got it wrong
twice (`WRONG_OUTPUT`), retried with the actual failure feedback both
times, still didn't converge, and correctly **stopped** once its 3-attempt
budget was exhausted (`stop_reason: GAVE_UP`, `final_code: None` -- no
unverified code was left applied). The likely culprit is the task's
combination of a non-obvious rounding rule and the `"0b"`-prefixed binary
string format, which small local models commonly get subtly wrong. This is
the system working as designed -- honest failure, no silent bad output --
not a crash or a hidden defect. Referenced from the Step 10 demo script
instead of re-running it live, since it's already real, captured data.

### Full frozen protocol

**Not yet run.** Would need all 40 tasks in `eval_tasks/subset_v1.json`, all 3 profiles (including `squeeze_mid`, which is where the hypothesis is actually tested), 3+ seeds each, across 4 systems -- 40 x 3 x 3 x 4 = 1,440 task-runs, each potentially involving multiple model loads (load times alone range 6s-86s per the real benchmark in `benchmarks/`). This is real CPU-bound LLM inference time, correctly scoped as 48h-version follow-up work per `DWARV_PLAN.md` section 8, not something to run inside a single development session.

## Interpretation

Not yet supported or refuted -- the full protocol hasn't run. The dry run's only claim is that the harness itself works end to end on real hardware, real models, and real verification, which is what Step 9's acceptance check asks for.
