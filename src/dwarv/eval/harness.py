import json
import platform
import subprocess
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import yaml

from dwarv.eval.baselines import run_dwarv_policy, run_fixed, run_retry, run_retry_escalate
from dwarv.models.suite import MODEL_ORDER, load_models_config, resolve_model_paths
from dwarv.resources.budget import load_budgets_config
from dwarv.resources.squeeze import SqueezeScheduler
from dwarv.runtime.llamacpp import LlamaCppRuntime
from dwarv.verify.evalplus_adapter import EvalTask, build_eval_tasks

REPO_ROOT = Path(__file__).resolve().parents[3]
EVAL_RESULTS_DIR = REPO_ROOT / "eval_results"
DEFAULT_SYSTEMS_CONFIG_PATH = REPO_ROOT / "configs" / "systems.yaml"
DEFAULT_CTX_SIZE = 4096
SYSTEMS = ["fixed", "retry", "retry_escalate", "dwarv"]


def load_systems_baselines(path: str | Path = DEFAULT_SYSTEMS_CONFIG_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    return cfg.get("baselines", {})


def _git_commit_hash() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, timeout=5
        )
        return out.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def _result_key(system: str, profile: str, task_id: str, seed: int) -> str:
    return f"{system}|{profile}|{task_id}|{seed}"


def _load_completed_keys(jsonl_path: Path) -> set[str]:
    completed: set[str] = set()
    if not jsonl_path.exists():
        return completed
    with open(jsonl_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            completed.add(_result_key(row["system"], row["profile"], row["task_id"], row["seed"]))
    return completed


def _llama_server_exe_name(models_config: dict) -> str:
    key = f"{platform.system()},{platform.machine()}"
    entry = models_config.get("llama_cpp", {}).get("assets", {}).get(key)
    return entry[2] if entry else "llama-server"


def _run_one(
    runtime,
    system: str,
    task: EvalTask,
    profile_name: str,
    profile: dict,
    models_config: dict,
    baselines_cfg: dict,
    ctx_size: int,
):
    if system == "fixed":
        cfg = baselines_cfg.get("fixed", {"model_id": "small", "ctx_size": ctx_size})
        return run_fixed(runtime, task, cfg["model_id"], cfg.get("ctx_size", ctx_size))
    if system == "retry":
        cfg = baselines_cfg.get(
            "retry", {"model_id": "small", "ctx_size": ctx_size, "max_attempts": 3}
        )
        return run_retry(
            runtime,
            task,
            cfg["model_id"],
            cfg.get("ctx_size", ctx_size),
            max_attempts=profile["max_attempts"],
            time_limit_s=profile["time_limit_s"],
        )
    if system == "retry_escalate":
        return run_retry_escalate(
            runtime,
            task,
            MODEL_ORDER,
            ctx_size,
            max_attempts=profile["max_attempts"],
            time_limit_s=profile["time_limit_s"],
        )
    if system == "dwarv":
        squeeze = None
        squeeze_cfg = profile.get("squeeze")
        if squeeze_cfg is not None:
            squeeze = SqueezeScheduler()
            squeeze.schedule(
                squeeze_cfg["new_ram_limit_mb"],
                reason=f"{profile_name} profile",
                after_turns=squeeze_cfg["trigger"]["after_attempt"],
            )
        return run_dwarv_policy(
            runtime,
            task,
            models_config,
            ctx_size,
            profile["ram_limit_mb"],
            max_attempts=profile["max_attempts"],
            time_limit_s=profile["time_limit_s"],
            squeeze=squeeze,
        )
    raise ValueError(f"unknown system {system!r}")


def run_harness(
    run_id: str,
    systems: list[str],
    profiles: list[str],
    task_ids: list[str],
    seeds: list[int],
    cache_dir: str,
    ctx_size: int = DEFAULT_CTX_SIZE,
) -> Path:
    """Nested loop over profiles x tasks x seeds x systems. Crash-safe JSONL
    (one line appended per completed run); resumable by (system, profile,
    task_id, seed) -- re-running with the same run_id skips what's already
    in the file instead of duplicating it. Never overwrites a prior run_id's
    results -- each run_id gets its own directory."""
    run_dir = EVAL_RESULTS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = run_dir / "results.jsonl"

    models_config = load_models_config()
    baselines_cfg = load_systems_baselines()
    all_profiles = load_budgets_config().get("profiles", {})

    config_snapshot = {
        "run_id": run_id,
        "systems": systems,
        "profiles": profiles,
        "task_ids": task_ids,
        "seeds": seeds,
        "ctx_size": ctx_size,
        "git_commit": _git_commit_hash(),
        "timestamp": datetime.now(UTC).isoformat(),
    }
    with open(run_dir / "config.json", "w", encoding="utf-8") as f:
        json.dump(config_snapshot, f, indent=2)

    model_paths = resolve_model_paths(models_config, cache_dir)
    llama_server = Path(cache_dir) / "llama.cpp" / _llama_server_exe_name(models_config)
    runtime = LlamaCppRuntime(str(llama_server), model_paths)

    eval_tasks = {t.task_id: t for t in build_eval_tasks(task_ids)}
    completed = _load_completed_keys(jsonl_path)

    try:
        for profile_name in profiles:
            profile = all_profiles[profile_name]
            for task_id in task_ids:
                task = eval_tasks[task_id]
                for seed in seeds:
                    for system in systems:
                        key = _result_key(system, profile_name, task_id, seed)
                        if key in completed:
                            continue
                        result = _run_one(
                            runtime,
                            system,
                            task,
                            profile_name,
                            profile,
                            models_config,
                            baselines_cfg,
                            ctx_size,
                        )
                        row = asdict(result)
                        row["profile"] = profile_name
                        row["seed"] = seed
                        row["run_id"] = run_id
                        with open(jsonl_path, "a", encoding="utf-8") as f:
                            f.write(json.dumps(row) + "\n")
                        completed.add(key)
    finally:
        runtime.unload()

    return jsonl_path
