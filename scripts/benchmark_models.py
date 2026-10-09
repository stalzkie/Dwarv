#!/usr/bin/env python
"""Benchmark the bundled Qwen2.5-Coder models: real load time + RSS + one
generation, per DWARV_PLAN.md Step 3's acceptance check ("record RSS per
(model, ctx) into configs/models.yaml"). Writes a timestamped CSV + charts
into benchmarks/history/<run_id>/, and refreshes benchmarks/latest/ with a
copy of the most recent run. A maintainer/dev tool, not part of the
installed `dwarv` CLI surface -- run it directly with the project's venv.

Usage:
    DWARV_CACHE_DIR=/path/to/cache python scripts/benchmark_models.py
    python scripts/benchmark_models.py --cache-dir D:\\dwarv-cache --ctx-size 4096
    python scripts/benchmark_models.py --models small medium
"""

import argparse
import csv
import datetime as dt
import json
import os
import platform
import shutil
import sys
import time
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import psutil

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))  # works even without `pip install -e .`

from dwarv.cli import _llama_server_exe_name  # noqa: E402
from dwarv.models.suite import load_models_config, resolve_model_paths  # noqa: E402
from dwarv.resources.monitor import ResourceMonitor  # noqa: E402
from dwarv.runtime.base import GenParams  # noqa: E402
from dwarv.runtime.llamacpp import LlamaCppRuntime  # noqa: E402

BENCHMARKS_DIR = REPO_ROOT / "benchmarks"
HISTORY_DIR = BENCHMARKS_DIR / "history"
LATEST_DIR = BENCHMARKS_DIR / "latest"

PROMPT = "Write a Python function that reverses a string."


def hardware_snapshot() -> dict:
    vm = psutil.virtual_memory()
    return {
        "os": platform.platform(),
        "cpu_logical": psutil.cpu_count(logical=True),
        "cpu_physical": psutil.cpu_count(logical=False),
        "ram_total_mb": round(vm.total / (1024**2), 1),
        "ram_available_mb_at_start": round(vm.available / (1024**2), 1),
    }


def benchmark_one_model(
    runtime: LlamaCppRuntime,
    monitor: ResourceMonitor,
    model_id: str,
    model_cfg: dict,
    ctx_size: int,
    settle_s: float = 3.0,
) -> dict:
    load_s = runtime.load(model_id, ctx_size=ctx_size)
    time.sleep(settle_s)  # let RSS settle after load before sampling
    after_load = monitor.sample_once()

    gen_start = time.monotonic()
    result = runtime.generate(
        messages=[{"role": "user", "content": PROMPT}],
        params=GenParams(max_tokens=200),
    )
    gen_wall_s = time.monotonic() - gen_start
    time.sleep(1.0)
    after_gen = monitor.sample_once()
    peak_rss_mb = monitor.peak_rss_mb()

    runtime.unload()

    file_size_bytes = model_cfg.get("file_size_bytes")
    return {
        "model_id": model_id,
        "family": model_cfg.get("family"),
        "quant": model_cfg.get("quant"),
        "file_size_mb": round(file_size_bytes / (1024**2), 1) if file_size_bytes else None,
        "ctx_size": ctx_size,
        "load_time_s": round(load_s, 2),
        "rss_after_load_mb": round(after_load.server_rss_mb, 1),
        "rss_after_gen_mb": round(after_gen.server_rss_mb, 1),
        "peak_rss_mb": round(peak_rss_mb, 1),
        "sys_available_mb_after_gen": round(after_gen.sys_available_mb, 1),
        "gen_wall_s": round(gen_wall_s, 2),
        "gen_completion_tokens": result.completion_tokens,
    }


def write_csv(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].keys())
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_charts(rows: list[dict], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    model_ids = [r["model_id"] for r in rows]
    ctx = rows[0]["ctx_size"]

    fig, ax = plt.subplots(figsize=(6, 4))
    ax.bar(model_ids, [r["peak_rss_mb"] for r in rows], color="#4C72B0")
    ax.set_ylabel("Peak server RSS (MB)")
    ax.set_title(f"Peak RSS by model (ctx={ctx})")
    fig.tight_layout()
    fig.savefig(out_dir / "rss_by_model.png", dpi=150)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(6, 4))
    ax.bar(model_ids, [r["load_time_s"] for r in rows], color="#DD8452")
    ax.set_ylabel("Load time (s)")
    ax.set_title(f"Model load time by model (ctx={ctx})")
    fig.tight_layout()
    fig.savefig(out_dir / "load_time_by_model.png", dpi=150)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(6, 4))
    ax.bar(model_ids, [r["gen_wall_s"] for r in rows], color="#55A868")
    ax.set_ylabel("Generation wall time (s)")
    ax.set_title(f"200-token generation time by model (ctx={ctx})")
    fig.tight_layout()
    fig.savefig(out_dir / "gen_time_by_model.png", dpi=150)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ctx-size", type=int, default=4096)
    parser.add_argument("--cache-dir", default=None, help="overrides DWARV_CACHE_DIR")
    parser.add_argument(
        "--models", nargs="+", default=["small", "medium", "large"], help="model ids to benchmark"
    )
    args = parser.parse_args()

    cache_dir = (
        Path(args.cache_dir)
        if args.cache_dir
        else Path(os.environ.get("DWARV_CACHE_DIR", REPO_ROOT / "cache"))
    )

    config = load_models_config()
    model_paths = resolve_model_paths(config, cache_dir)
    models_by_id = {m["id"]: m for m in config["models"]}

    exe_name = _llama_server_exe_name(config) or "llama-server"
    llama_server_path = cache_dir / "llama.cpp" / exe_name
    if not llama_server_path.exists():
        raise SystemExit(
            f"llama-server not found at {llama_server_path}; run `dwarv setup-offline` first."
        )

    runtime = LlamaCppRuntime(str(llama_server_path), model_paths)
    monitor = ResourceMonitor(pid_fn=runtime.pid, interval_s=0.5)
    monitor.start()

    run_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    hw = hardware_snapshot()

    rows = []
    try:
        for model_id in args.models:
            if model_id not in model_paths:
                print(f"skipping {model_id}: no path configured in configs/models.yaml")
                continue
            print(f"Benchmarking {model_id}...")
            row = benchmark_one_model(
                runtime, monitor, model_id, models_by_id[model_id], args.ctx_size
            )
            row["run_id"] = run_id
            row.update(hw)
            rows.append(row)
            print(f"  load_time_s={row['load_time_s']} peak_rss_mb={row['peak_rss_mb']}")
    finally:
        monitor.stop()
        runtime.unload()

    if not rows:
        raise SystemExit("no models benchmarked -- nothing to write")

    run_dir = HISTORY_DIR / run_id
    write_csv(rows, run_dir / "models_benchmark.csv")
    write_charts(rows, run_dir)
    with open(run_dir / "hardware.json", "w", encoding="utf-8") as f:
        json.dump(hw, f, indent=2)

    if LATEST_DIR.exists():
        shutil.rmtree(LATEST_DIR)
    shutil.copytree(run_dir, LATEST_DIR)

    print(f"\nWrote {run_dir}")
    print(f"Refreshed {LATEST_DIR}")


if __name__ == "__main__":
    main()
