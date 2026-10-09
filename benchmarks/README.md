# Model benchmarks

Real, measured load-time and RSS data for the three bundled Qwen2.5-Coder
models, produced by `scripts/benchmark_models.py`. This is **not** the
internal accuracy eval harness (Step 9, `eval/` + `eval_tasks/` +
`eval_results/`, which measures verified pass rate) — this is hardware
profiling: how much RAM each model actually uses and how long it takes to
load, which is exactly the real data `models/suite.choose_model` (Step 5)
and `controller/policy.py` (Step 7) need to make and explain a model choice.
Never hand-edit these numbers into `configs/models.yaml` — only a real run
of this script should.

## Layout

```
benchmarks/
├── README.md          # this file
├── latest/             # a copy of the most recent run (overwritten each run)
│   ├── models_benchmark.csv
│   ├── hardware.json
│   ├── rss_by_model.png
│   ├── load_time_by_model.png
│   └── gen_time_by_model.png
└── history/             # every run ever taken, kept forever -- never deleted or overwritten
    ├── <run_id>/        # run_id = UTC timestamp, e.g. 20261009T083427Z
    │   └── ... (same files as latest/)
    └── ...
```

Each run's CSV has one row per model benchmarked, with columns:

| Column | Meaning |
|---|---|
| `model_id` | `small` / `medium` / `large` |
| `family`, `quant`, `file_size_mb` | from `configs/models.yaml` |
| `ctx_size` | context size the server was launched with |
| `load_time_s` | wall-clock seconds from subprocess launch to the `/health` endpoint responding |
| `rss_after_load_mb` | server RSS 3s after load completes (let it settle) |
| `rss_after_gen_mb` | server RSS 1s after one generation finishes |
| `peak_rss_mb` | highest RSS sample seen across the whole run for this model |
| `sys_available_mb_after_gen` | system-wide available RAM at that point (not just this process) |
| `gen_wall_s`, `gen_completion_tokens` | timing for one fixed 200-max-token generation |
| `run_id`, `os`, `cpu_logical`, `cpu_physical`, `ram_total_mb`, `ram_available_mb_at_start` | run/machine context, so numbers from different machines are never silently compared as if they were the same hardware |

## Running a new benchmark

Requires `llama-server` and the bundled models already downloaded (`dwarv setup-offline`):

```bash
# uses $DWARV_CACHE_DIR, or ./cache if unset
python scripts/benchmark_models.py

# explicit cache dir, custom context size, a subset of models
python scripts/benchmark_models.py --cache-dir D:\dwarv-cache --ctx-size 4096 --models small medium
```

This loads each requested model, waits for RSS to settle, runs one fixed
generation, samples RSS again, unloads, and moves to the next model
(sequential, never two models resident at once). It writes a new directory
under `benchmarks/history/<run_id>/` and refreshes `benchmarks/latest/` to
match. **Nothing in `history/` is ever overwritten or deleted by the
script** — that's what makes it a real run history instead of a single
mutable snapshot.

After a run you consider authoritative, copy its `load_time_s` /
`measured_rss_mb` into `configs/models.yaml` by hand (see that file's
header comment for which run it currently reflects).

## Current baseline

`configs/models.yaml` currently reflects `history/20261009T083427Z/`
(Windows 11, 12 logical / 6 physical CPU, 16.3GB RAM total, ~9.1GB
available at run start, Q4_K_M quant, ctx 4096, no GPU offload — CPU-only
per the plan's MVP scope). Peak RSS scales roughly linearly with model
size (1.7GB / 7.5GB / 10.0GB for small/medium/large); load time does not
(6s / 24s / 86s) because larger files take proportionally longer to read
from disk on top of the larger compute-graph setup cost.
