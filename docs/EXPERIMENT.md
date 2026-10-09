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

### Scoped real run (`realcompare1`, 2026-10-10) -- 20 tasks, squeeze_mid included, not the full frozen protocol

Hackathon time-scoped: all 20 tasks in the frozen subset (not the full 40), both `static_loose` and `squeeze_mid` (critically including `squeeze_mid`, unlike the dry run -- this is the condition the hypothesis is actually about), but only 1 seed per task for this profile pair rather than the full protocol's 3+. The run also crashed partway through on a real bug (a HumanEval+ ground-truth integer exceeding Python's int-to-str conversion limit inside the generated check script -- fixed in `verify/evalplus_adapter.py`, see git history) and was resumed rather than restarted, so `static_loose` carries a mix of seed counts per task (8 tasks x1 seed + 12 tasks x2 seeds, from before the crash, = 32 raw rows over 20 tasks) while `squeeze_mid` is a clean 1 seed x 20 tasks = 20 raw rows. The paired analysis majority-votes across whatever seeds exist per task, so this is handled correctly, but it means `squeeze_mid`'s per-task bit has no majority-vote smoothing the way some of `static_loose`'s does.

| system | profile | n | pass_rate | 95% CI | mean_peak_rss_mb | budget_violations | mean_wall_s | mean_attempts |
|---|---|---|---|---|---|---|---|---|
| dwarv | squeeze_mid | 20 | 0.60 | [0.40, 0.80] | 1751.4 | 0 | 13.40 | 1.80 |
| fixed | squeeze_mid | 20 | 0.50 | [0.30, 0.70] | 1751.2 | 0 | 6.90 | 1.00 |
| retry | squeeze_mid | 20 | 0.55 | [0.35, 0.75] | 1752.5 | 0 | 15.80 | 1.90 |
| retry_escalate | squeeze_mid | 20 | 0.40 | [0.20, 0.60] | 1751.6 | 0 | 15.99 | 2.20 |
| dwarv | static_loose | 32 | 0.5625 | [0.41, 0.75] | 1749.8 | 0 | 11.54 | 1.88 |
| fixed | static_loose | 32 | 0.50 | [0.31, 0.66] | 1751.9 | 0 | 5.88 | 1.00 |
| retry | static_loose | 32 | 0.5312 | [0.38, 0.69] | 1751.8 | 0 | 13.40 | 1.94 |
| retry_escalate | static_loose | 32 | 0.5625 | [0.38, 0.72] | 1752.0 | 0 | 11.11 | 1.88 |

Full tables/charts/raw data: `eval_results/realcompare1/{results.jsonl,summary.csv,summary.md,significance.csv,significance.md,pass_rate.png,rss_vs_pass.png,with_vs_without.png,diff_dwarv_vs_retry_escalate.md}`.

**dwarv vs fixed** (the significance report's default "with vs without Dwarv" pair, `significance.md`): McNemar on pass/fail is non-significant in both profiles (p=0.625 squeeze_mid, p=1.0 static_loose) -- not enough data to detect a correctness difference via the strict paired test, even though the raw pass rates favor `dwarv` in both. Wilcoxon on `wall_s` **is** significant in both profiles (p=0.0073 squeeze_mid, p=0.0007 static_loose): `dwarv` is reliably slower (13.4s vs 6.9s squeeze_mid; 11.5s vs 5.9s static_loose), the expected, honest cost of a policy that retries/escalates (mean_attempts ~1.8-1.9 vs fixed's fixed 1.0). Peak RSS is statistically indistinguishable (p=0.11 squeeze_mid, p=0.45 static_loose) -- expected, since RSS is dominated by which model is loaded, not by the policy logic around it.

**dwarv vs retry_escalate** is the comparison the decision criteria above actually turn on, since both systems retry and escalate on failure and only `dwarv`'s escalation is RAM-aware -- this isolates resource-awareness itself, not just "retrying helps." Computed directly (not in the default significance report, which only covers `dwarv` vs `fixed`): under `squeeze_mid`, `dwarv` won every one of the 4 tasks where the two systems disagreed and lost none (McNemar: both_pass=8, both_fail=8, dwarv_only=4, retry_escalate_only=0, p=0.125) -- directionally exactly what the hypothesis predicts, and it's the single widest pass-rate gap in the whole run (0.60 vs 0.40). Under `static_loose`, where no advantage is predicted, the two systems are close and split 2-1 in `dwarv`'s favor (p=1.0). A clean 4-0 sweep is a real, visible signal, but at n=20 it isn't enough to clear p<0.05 -- McNemar's exact test needs more discordant pairs than this to reach significance even with a perfect split.

`budget_violations` is 0 for every system and profile in this run, including the baselines under `squeeze_mid` -- the pressure this profile induces didn't trigger a hard OOM/budget breach for anyone at this n. The differences observed are in verified-pass-rate quality under pressure, not in raw violation counts, which matters for how to read "resource-awareness helped": it shows up as better answers under pressure, not as fewer crashes.

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

**Not yet run.** Would need all 40 tasks in `eval_tasks/subset_v1.json`, all 3 profiles, 3+ seeds each, across 4 systems -- 40 x 3 x 3 x 4 = 1,440 task-runs, each potentially involving multiple model loads (load times alone range 6s-86s per the real benchmark in `benchmarks/`). `realcompare1` above is a real, hackathon-scoped fraction of this (20 tasks, 2 of 3 profiles, 1 seed) chosen specifically to include `squeeze_mid` rather than expand `static_loose`/`static_tight` further, since `squeeze_mid` is where the hypothesis is actually tested; the full protocol remains real follow-up work.

## Interpretation

**Suggestive, not proven.** `realcompare1` is the first real run to include `squeeze_mid`, and the comparison that actually tests the hypothesis (`dwarv` vs `retry_escalate`, isolating resource-awareness from "retrying helps at all") points the predicted direction with the largest pass-rate gap of any pairwise comparison in the run (0.60 vs 0.40, a clean 4-0 sweep on disagreements) -- but n=20 with 1 seed is underpowered to call that significant (McNemar p=0.125), consistent with the earlier power calculation (~100+ tasks needed to reliably detect a medium effect). `dwarv` also measurably costs more wall-clock time than a no-policy baseline (p<0.01), which is an honest, expected tradeoff of retrying/escalating rather than a flaw. The full frozen protocol (40 tasks x 3+ seeds x all 3 profiles) is still needed to actually claim support.
