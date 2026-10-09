import csv
import json
import random
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

BOOTSTRAP_SAMPLES = 1000
BOOTSTRAP_SEED = 42


def load_results(jsonl_path: Path) -> list[dict]:
    rows = []
    with open(jsonl_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def bootstrap_ci(
    values: list[bool], n_samples: int = BOOTSTRAP_SAMPLES, seed: int = BOOTSTRAP_SEED
) -> tuple[float, float, float]:
    """Percentile bootstrap 95% CI for a pass rate. Returns (mean, lo, hi)."""
    if not values:
        return 0.0, 0.0, 0.0
    rng = random.Random(seed)
    n = len(values)
    mean = sum(values) / n
    if n == 1:
        return mean, mean, mean
    means = []
    for _ in range(n_samples):
        sample = [values[rng.randrange(n)] for _ in range(n)]
        means.append(sum(sample) / n)
    means.sort()
    lo = means[int(0.025 * n_samples)]
    hi = means[min(int(0.975 * n_samples), n_samples - 1)]
    return mean, lo, hi


def summarize(rows: list[dict]) -> list[dict]:
    """One row per (system, profile): pass rate + 95% CI, mean/peak RSS,
    violation count, mean latency, mean attempts."""
    groups: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        key = (row["system"], row["profile"])
        groups.setdefault(key, []).append(row)

    summary = []
    for (system, profile), group in sorted(groups.items()):
        passed = [r["passed"] for r in group]
        mean, lo, hi = bootstrap_ci(passed)
        summary.append(
            {
                "system": system,
                "profile": profile,
                "n": len(group),
                "pass_rate": round(mean, 4),
                "pass_rate_ci_lo": round(lo, 4),
                "pass_rate_ci_hi": round(hi, 4),
                "mean_peak_rss_mb": round(sum(r["peak_rss_mb"] for r in group) / len(group), 1),
                "max_peak_rss_mb": round(max(r["peak_rss_mb"] for r in group), 1),
                "budget_violations": sum(1 for r in group if r["budget_violation"]),
                "mean_wall_s": round(sum(r["wall_s"] for r in group) / len(group), 2),
                "mean_attempts": round(sum(r["attempts"] for r in group) / len(group), 2),
            }
        )
    return summary


def per_task_diff(rows: list[dict], system_a: str, system_b: str, profile: str) -> list[dict]:
    """Tasks `system_a` solved that `system_b` didn't, and vice versa, under
    the given profile (majority vote across seeds per task)."""
    by_task_system: dict[tuple[str, str], list[bool]] = {}
    for row in rows:
        if row["profile"] != profile or row["system"] not in (system_a, system_b):
            continue
        by_task_system.setdefault((row["task_id"], row["system"]), []).append(row["passed"])

    task_ids = sorted({task_id for task_id, _system in by_task_system})
    diffs = []
    for task_id in task_ids:
        a_runs = by_task_system.get((task_id, system_a), [])
        b_runs = by_task_system.get((task_id, system_b), [])
        a_passed = sum(a_runs) > len(a_runs) / 2 if a_runs else False
        b_passed = sum(b_runs) > len(b_runs) / 2 if b_runs else False
        if a_passed != b_passed:
            diffs.append(
                {
                    "task_id": task_id,
                    f"{system_a}_passed": a_passed,
                    f"{system_b}_passed": b_passed,
                    "winner": system_a if a_passed else system_b,
                }
            )
    return diffs


def write_summary_csv(summary: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not summary:
        path.write_text("", encoding="utf-8")
        return
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(summary[0].keys()))
        writer.writeheader()
        writer.writerows(summary)


def write_summary_markdown(summary: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not summary:
        path.write_text("(no results)\n", encoding="utf-8")
        return
    cols = list(summary[0].keys())
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for row in summary:
        lines.append("| " + " | ".join(str(row[c]) for c in cols) + " |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_pass_rate_chart(summary: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    systems = sorted({s["system"] for s in summary})
    profiles = sorted({s["profile"] for s in summary})
    by_key = {(s["system"], s["profile"]): s for s in summary}

    fig, ax = plt.subplots(figsize=(max(6, 1.5 * len(systems) * len(profiles)), 4.5))
    width = 0.8 / max(len(profiles), 1)
    x_base = range(len(systems))
    colors = plt.cm.tab10.colors
    for pi, profile in enumerate(profiles):
        means = []
        errs_lo = []
        errs_hi = []
        for system in systems:
            row = by_key.get((system, profile))
            if row is None:
                means.append(0.0)
                errs_lo.append(0.0)
                errs_hi.append(0.0)
            else:
                means.append(row["pass_rate"])
                errs_lo.append(max(0.0, row["pass_rate"] - row["pass_rate_ci_lo"]))
                errs_hi.append(max(0.0, row["pass_rate_ci_hi"] - row["pass_rate"]))
        xs = [x + pi * width for x in x_base]
        ax.bar(
            xs,
            means,
            width=width,
            yerr=[errs_lo, errs_hi],
            capsize=3,
            label=profile,
            color=colors[pi % len(colors)],
        )
    ax.set_xticks([x + width * (len(profiles) - 1) / 2 for x in x_base])
    ax.set_xticklabels(systems)
    ax.set_ylabel("Verified pass rate")
    ax.set_ylim(0, 1.05)
    ax.set_title("Pass rate by system and profile (95% bootstrap CI)")
    ax.legend()
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def write_rss_vs_pass_scatter(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    systems = sorted({r["system"] for r in rows})
    colors = plt.cm.tab10.colors

    fig, ax = plt.subplots(figsize=(7, 5))
    for i, system in enumerate(systems):
        xs = [r["peak_rss_mb"] for r in rows if r["system"] == system]
        ys = [1 if r["passed"] else 0 for r in rows if r["system"] == system]
        ax.scatter(xs, ys, label=system, alpha=0.6, color=colors[i % len(colors)])
    ax.set_xlabel("Peak server RSS (MB)")
    ax.set_ylabel("Passed (0/1)")
    ax.set_yticks([0, 1])
    ax.set_title("Peak RSS vs. pass, per run")
    ax.legend()
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def analyze_run(run_dir: Path) -> None:
    """Reads run_dir/results.jsonl, writes summary.csv, summary.md,
    pass_rate.png, rss_vs_pass.png, and a diff table (dwarv vs retry_escalate,
    per profile) into run_dir. Never overwrites raw results.jsonl."""
    rows = load_results(run_dir / "results.jsonl")
    summary = summarize(rows)
    write_summary_csv(summary, run_dir / "summary.csv")
    write_summary_markdown(summary, run_dir / "summary.md")
    write_pass_rate_chart(summary, run_dir / "pass_rate.png")
    write_rss_vs_pass_scatter(rows, run_dir / "rss_vs_pass.png")

    profiles = sorted({r["profile"] for r in rows})
    diff_lines = []
    for profile in profiles:
        diffs = per_task_diff(rows, "dwarv", "retry_escalate", profile)
        diff_lines.append(f"## {profile}: dwarv vs retry_escalate\n")
        if not diffs:
            diff_lines.append("(no differing tasks)\n")
        else:
            for d in diffs:
                diff_lines.append(f"- {d['task_id']}: winner = {d['winner']}\n")
    (run_dir / "diff_dwarv_vs_retry_escalate.md").write_text("".join(diff_lines), encoding="utf-8")
