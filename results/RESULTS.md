# Dwarv — Results

All numbers on this page are real measurements from this repository's own benchmark and evaluation runs — none are estimated or illustrative. Raw data, charts, and significance reports backing every table here live alongside this file in `results/benchmarks/` and `results/eval/` (copies of `benchmarks/latest/` and `eval_results/`, which remain the canonical originals).

Test machine: Windows 11, 12 logical / 6 physical CPU cores, 16GB RAM, NVIDIA GeForce RTX 3050 (8GB VRAM). Full hardware details: `results/benchmarks/hardware.json`.

---

## 1. Model benchmarks (CPU baseline)

Load time, memory footprint, and generation speed for each of the three bundled Qwen2.5-Coder models, CPU-only, at context size 4096. Source: `results/benchmarks/models_benchmark.csv`, reproducible via `python scripts/benchmark_models.py`.

| model | quant | file size | load time | peak RSS | generation speed |
|---|---|---|---|---|---|
| small (1.5B) | Q4_K_M | 1.04 GB | 6.0 s | 1.69 GB | 19.5 tok/s |
| medium (7B) | Q4_K_M | 4.36 GB | 23.6 s | 7.28 GB | 4.2 tok/s |
| large (14B) | Q4_K_M | 8.37 GB | 86.4 s | 9.73 GB | 2.2 tok/s |

Charts: `gen_time_by_model.png`, `load_time_by_model.png`, `rss_by_model.png`.

## 2. GPU offload benchmark (Vulkan)

Same three models, same machine, comparing CPU-only (`-ngl 0`) against full GPU offload (`-ngl 99`) via `llama-bench`. Source: `results/benchmarks/gpu_offload_benchmark.csv`.

| model | CPU generation | GPU generation | speedup |
|---|---|---|---|
| small (1.5B, Q4_K_M) | 19.54 tok/s | 163.51 tok/s | **8.4x** |
| medium (7B, Q4_K_M) | 4.55 tok/s | 44.26 tok/s | **9.7x** |
| large (14B, IQ2_M) | 3.47 tok/s | 19.16 tok/s | **5.5x** |

Prompt-processing speed (first-token latency) improved even more: 5.3x (small), 10.1x (medium), 6.7x (large). All three models fit entirely in the 8GB GPU with room to spare — full offload (`-ngl 99`) was used throughout, no partial-layer tuning was needed.

Reproduction command (run per model, with llama.cpp's Vulkan-enabled `llama-server`/`llama-bench` build):
```bash
llama-bench -m <model.gguf> -ngl 0,99 -p 128 -n 128
```

Vulkan was chosen over CUDA for the GPU build: the CUDA asset for the same llama.cpp release (`b11516`) needs a separate ~400MB `cudart` redistributable on top of its own ~265MB binary and only supports NVIDIA; the Vulkan binary is 33MB total, needs no separate runtime, and runs on NVIDIA, AMD, and Intel GPUs.

## 3. Internal comparison evaluation (`realcompare1`)

Compares Dwarv's actual resource-aware policy against three baselines on real HumanEval+ coding tasks, verified through Dwarv's own cross-platform sandbox (not evalplus's own Linux-only checker). 20 tasks, 4 systems (`fixed`, `retry`, `retry_escalate`, `dwarv`), 2 budget profiles (`static_loose` — no memory pressure, `squeeze_mid` — memory pressure triggered after the first attempt). Full raw data: `results/eval/realcompare1/results.jsonl`. Methodology and protocol: `docs/EXPERIMENT.md`.

### Summary

| system | profile | n | pass rate | 95% CI | mean wall time | mean attempts |
|---|---|---|---|---|---|---|
| dwarv | squeeze_mid | 20 | **0.60** | [0.40, 0.80] | 13.4s | 1.8 |
| fixed | squeeze_mid | 20 | 0.50 | [0.30, 0.70] | 6.9s | 1.0 |
| retry | squeeze_mid | 20 | 0.55 | [0.35, 0.75] | 15.8s | 1.9 |
| retry_escalate | squeeze_mid | 20 | 0.40 | [0.20, 0.60] | 16.0s | 2.2 |
| dwarv | static_loose | 32 | 0.5625 | [0.41, 0.75] | 11.5s | 1.88 |
| fixed | static_loose | 32 | 0.50 | [0.31, 0.66] | 5.9s | 1.00 |
| retry | static_loose | 32 | 0.5312 | [0.38, 0.69] | 13.4s | 1.94 |
| retry_escalate | static_loose | 32 | 0.5625 | [0.38, 0.72] | 11.1s | 1.88 |

`static_loose` carries a mix of 1 and 2 seeds per task (some tasks have two independent runs, from an earlier partial run that was resumed rather than discarded); `squeeze_mid` is a clean 1 seed × 20 tasks. `budget_violations` was 0 for every system and profile — the differences below are in answer quality under pressure, not crash counts.

### Statistical significance

Paired tests (McNemar's exact test for pass/fail, Wilcoxon signed-rank for continuous metrics) rather than comparing independent confidence intervals, since every system ran the identical tasks. Full report: `results/eval/realcompare1/significance.md`.

**dwarv vs. retry_escalate** (isolates resource-awareness itself — both systems retry and escalate on failure, only `dwarv`'s escalation is RAM-aware) under `squeeze_mid`: dwarv won every one of the 4 tasks where the two systems disagreed, and lost none (both_pass=8, both_fail=8, dwarv_only=4, retry_escalate_only=0). This is the widest pass-rate gap of any pairwise comparison in the run (0.60 vs. 0.40). McNemar's exact test on this split: p=0.125 — not significant at the standard α=0.05 threshold at this sample size (n=20).

**dwarv vs. fixed** (the plain, no-policy baseline):

| test | profile | result |
|---|---|---|
| McNemar (pass/fail) | squeeze_mid | p=0.625, not significant |
| McNemar (pass/fail) | static_loose | p=1.0, not significant |
| Wilcoxon (wall_s) | squeeze_mid | p=0.0073, **significant** — dwarv is slower |
| Wilcoxon (wall_s) | static_loose | p=0.0007, **significant** — dwarv is slower |
| Wilcoxon (peak_rss_mb) | squeeze_mid | p=0.1126, not significant |
| Wilcoxon (peak_rss_mb) | static_loose | p=0.4524, not significant |

Dwarv measurably costs more wall-clock time than the no-policy baseline (statistically significant, both profiles) — the real, honest price of retrying and escalating instead of answering once and stopping. Memory usage is statistically indistinguishable, since RSS is dominated by which model is loaded, not by the policy logic around it.

### Interpretation

The comparison that isolates resource-awareness (dwarv vs. retry_escalate) points entirely in the predicted direction under memory pressure, with the largest effect size observed in the whole run — but a clean 4-0 split at n=20 does not clear the standard significance threshold. This is a real, directionally consistent result, not a statistically confirmed one at this sample size.

## 4. Harness validation run (`dryrun2`)

A smaller, earlier run (5 tasks, 1 seed, `static_loose` only) used to confirm the evaluation harness itself works correctly end-to-end — real inference, real sandboxed verification, real bootstrap confidence intervals, real resumable JSONL output — before the full `realcompare1` run. Raw data: `results/eval/dryrun2/`.

## 5. Where everything lives

- `results/benchmarks/` — CPU and GPU benchmark data (CSV + charts), copied from `benchmarks/latest/`.
- `results/eval/realcompare1/` — the real comparison evaluation (§3 above), copied from `eval_results/realcompare1/`.
- `results/eval/dryrun2/` — the harness validation run (§4 above), copied from `eval_results/dryrun2/`.
- `benchmarks/` and `eval_results/` at the repository root remain the canonical, original locations (including `benchmarks/history/` and `eval_results/dryrun1/`, earlier runs kept for history) — nothing here replaces them, this folder is a single place to find the headline numbers together.
- `docs/EXPERIMENT.md` — the full evaluation protocol, decision criteria, and methodology notes.
- `docs/DECISIONS.md` — the reasoning behind every non-obvious technical choice, including the GPU offload Vulkan-vs-CUDA decision and the real bugs found while live-testing.
