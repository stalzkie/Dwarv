import csv
import json
import random
from pathlib import Path

import matplotlib
from scipy.stats import binomtest, wilcoxon

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


def _paired_pass_outcomes(
    rows: list[dict], system_a: str, system_b: str, profile: str
) -> dict[str, tuple[bool, bool]]:
    """{task_id: (a_passed, b_passed)} -- majority vote across seeds per
    task per system. Shared pairing logic for per_task_diff/mcnemar_test,
    since both need the exact same task-to-outcome alignment."""
    by_task_system: dict[tuple[str, str], list[bool]] = {}
    for row in rows:
        if row["profile"] != profile or row["system"] not in (system_a, system_b):
            continue
        by_task_system.setdefault((row["task_id"], row["system"]), []).append(row["passed"])

    task_ids = sorted({task_id for task_id, _system in by_task_system})
    outcomes = {}
    for task_id in task_ids:
        a_runs = by_task_system.get((task_id, system_a), [])
        b_runs = by_task_system.get((task_id, system_b), [])
        if not a_runs or not b_runs:
            continue  # task not run under both systems -- can't pair it
        a_passed = sum(a_runs) > len(a_runs) / 2
        b_passed = sum(b_runs) > len(b_runs) / 2
        outcomes[task_id] = (a_passed, b_passed)
    return outcomes


def per_task_diff(rows: list[dict], system_a: str, system_b: str, profile: str) -> list[dict]:
    """Tasks `system_a` solved that `system_b` didn't, and vice versa, under
    the given profile (majority vote across seeds per task)."""
    outcomes = _paired_pass_outcomes(rows, system_a, system_b, profile)
    diffs = []
    for task_id, (a_passed, b_passed) in outcomes.items():
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


def mcnemar_test(rows: list[dict], system_a: str, system_b: str, profile: str) -> dict:
    """Exact McNemar's test on paired binary pass/fail outcomes for the
    SAME tasks run under both systems -- the statistically correct test
    here (not an unpaired comparison of two independent pass rates),
    because it isolates exactly the disagreements that matter (task_id,
    system) while controlling for per-task difficulty, which two
    independent bootstrap CIs do not. Uses the exact binomial form (not
    the chi-square approximation), which is correct and recommended at
    small discordant-pair counts like ours. See DWARV_PLAN.md's write-up
    for citations."""
    outcomes = _paired_pass_outcomes(rows, system_a, system_b, profile)
    both_pass = both_fail = a_only = b_only = 0
    for a_passed, b_passed in outcomes.values():
        if a_passed and b_passed:
            both_pass += 1
        elif not a_passed and not b_passed:
            both_fail += 1
        elif a_passed:
            a_only += 1
        else:
            b_only += 1

    discordant = a_only + b_only
    p_value = (
        1.0
        if discordant == 0
        else binomtest(min(a_only, b_only), discordant, 0.5, alternative="two-sided").pvalue
    )
    return {
        "system_a": system_a,
        "system_b": system_b,
        "profile": profile,
        "n_tasks": len(outcomes),
        "both_pass": both_pass,
        "both_fail": both_fail,
        f"{system_a}_only": a_only,
        f"{system_b}_only": b_only,
        "p_value": round(p_value, 4),
        "significant_at_0.05": bool(p_value < 0.05),
    }


def wilcoxon_signed_rank(
    rows: list[dict], system_a: str, system_b: str, profile: str, metric: str = "wall_s"
) -> dict:
    """Paired Wilcoxon signed-rank test for a continuous metric (e.g.
    wall_s, peak_rss_mb) between two systems on the SAME tasks (mean
    across seeds per task, paired by task_id) -- the paired analog of
    McNemar's test for non-binary outcomes, more powerful than an
    unpaired comparison for the same reason."""
    by_task_system: dict[tuple[str, str], list[float]] = {}
    for row in rows:
        if row["profile"] != profile or row["system"] not in (system_a, system_b):
            continue
        by_task_system.setdefault((row["task_id"], row["system"]), []).append(row[metric])

    task_ids = sorted({task_id for task_id, _system in by_task_system})
    a_vals, b_vals = [], []
    for task_id in task_ids:
        a_runs = by_task_system.get((task_id, system_a))
        b_runs = by_task_system.get((task_id, system_b))
        if not a_runs or not b_runs:
            continue
        a_vals.append(sum(a_runs) / len(a_runs))
        b_vals.append(sum(b_runs) / len(b_runs))

    base = {
        "system_a": system_a,
        "system_b": system_b,
        "profile": profile,
        "metric": metric,
        "n_tasks": len(a_vals),
    }
    if len(a_vals) < 2 or all(a == b for a, b in zip(a_vals, b_vals, strict=True)):
        return {**base, "p_value": None, "note": "too few paired tasks, or no variance to test"}
    try:
        statistic, p_value = wilcoxon(a_vals, b_vals)
    except ValueError as exc:
        return {**base, "p_value": None, "note": str(exc)}
    return {
        **base,
        f"mean_{system_a}": round(sum(a_vals) / len(a_vals), 3),
        f"mean_{system_b}": round(sum(b_vals) / len(b_vals), 3),
        "statistic": round(float(statistic), 4),
        "p_value": round(float(p_value), 4),
        "significant_at_0.05": bool(p_value < 0.05),
    }


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


def write_with_vs_without_chart(
    summary: list[dict], path: Path, with_system: str = "dwarv", without_system: str = "fixed"
) -> None:
    """Focused two-system comparison: `with_system` (the full policy) vs
    `without_system` (one model, one generation, no retry/resource-
    awareness at all -- the closest thing to "no Dwarv" in this harness),
    one bar pair per profile, 95% bootstrap CI error bars."""
    path.parent.mkdir(parents=True, exist_ok=True)
    profiles = sorted(
        {s["profile"] for s in summary if s["system"] in (with_system, without_system)}
    )
    by_key = {(s["system"], s["profile"]): s for s in summary}

    fig, ax = plt.subplots(figsize=(max(5, 1.8 * len(profiles)), 4.5))
    width = 0.35
    x_base = range(len(profiles))
    for i, system in enumerate((without_system, with_system)):
        means, errs_lo, errs_hi = [], [], []
        for profile in profiles:
            row = by_key.get((system, profile))
            if row is None:
                means.append(0.0)
                errs_lo.append(0.0)
                errs_hi.append(0.0)
            else:
                means.append(row["pass_rate"])
                errs_lo.append(max(0.0, row["pass_rate"] - row["pass_rate_ci_lo"]))
                errs_hi.append(max(0.0, row["pass_rate_ci_hi"] - row["pass_rate"]))
        xs = [x + i * width for x in x_base]
        ax.bar(
            xs,
            means,
            width=width,
            yerr=[errs_lo, errs_hi],
            capsize=3,
            label=f"{system} ({'with Dwarv' if system == with_system else 'without Dwarv'})",
        )
    ax.set_xticks([x + width / 2 for x in x_base])
    ax.set_xticklabels(profiles)
    ax.set_ylabel("Verified pass rate")
    ax.set_ylim(0, 1.05)
    ax.set_title(f"{with_system} vs {without_system}: pass rate by profile (95% bootstrap CI)")
    ax.legend()
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def write_significance_report(
    rows: list[dict], path: Path, with_system: str = "dwarv", without_system: str = "fixed"
) -> None:
    """McNemar's test (paired pass/fail) and Wilcoxon signed-rank (paired
    wall_s, peak_rss_mb) for with_system vs without_system, one row per
    profile, written to both CSV and Markdown. The statistically honest
    companion to the bar chart -- a visual gap isn't evidence by itself at
    small n; this is."""
    path.parent.mkdir(parents=True, exist_ok=True)
    profiles = sorted({r["profile"] for r in rows})

    results = []
    for profile in profiles:
        mcnemar = mcnemar_test(rows, with_system, without_system, profile)
        wall = wilcoxon_signed_rank(rows, with_system, without_system, profile, "wall_s")
        rss = wilcoxon_signed_rank(rows, with_system, without_system, profile, "peak_rss_mb")
        results.append({"test": "mcnemar (pass/fail)", "profile": profile, **mcnemar})
        results.append({"test": "wilcoxon (wall_s)", "profile": profile, **wall})
        results.append({"test": "wilcoxon (peak_rss_mb)", "profile": profile, **rss})

    all_keys: list[str] = []
    for r in results:
        for k in r:
            if k not in all_keys:
                all_keys.append(k)
    csv_path = path.with_suffix(".csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=all_keys)
        writer.writeheader()
        writer.writerows(results)

    lines = [
        f"# Significance: {with_system} (with Dwarv) vs {without_system} (without Dwarv)\n",
        "\nMcNemar's test is the correct test for paired pass/fail outcomes on "
        "identical tasks (controls for per-task difficulty, unlike comparing two "
        "independent bootstrap CIs). Wilcoxon signed-rank is its paired analog for "
        "continuous metrics. A non-significant p-value at small n means "
        "**not yet enough data to tell**, not evidence of no difference.\n\n",
    ]
    for r in results:
        lines.append(f"## {r['test']}, profile={r['profile']}\n\n")
        for k, v in r.items():
            if k in ("test", "profile"):
                continue
            lines.append(f"- {k}: {v}\n")
        lines.append("\n")
    path.with_suffix(".md").write_text("".join(lines), encoding="utf-8")


def analyze_run(run_dir: Path) -> None:
    """Reads run_dir/results.jsonl, writes summary.csv, summary.md,
    pass_rate.png, rss_vs_pass.png, a diff table (dwarv vs retry_escalate,
    per profile), and -- "with Dwarv" (the full policy) vs "without Dwarv"
    (fixed: one model, one generation, no retry/resource-awareness) --
    with_vs_without.png plus significance.csv/.md (McNemar + Wilcoxon
    signed-rank, the statistically correct paired tests for this exact
    same-tasks-both-systems setup). Never overwrites raw results.jsonl."""
    rows = load_results(run_dir / "results.jsonl")
    summary = summarize(rows)
    write_summary_csv(summary, run_dir / "summary.csv")
    write_summary_markdown(summary, run_dir / "summary.md")
    write_pass_rate_chart(summary, run_dir / "pass_rate.png")
    write_rss_vs_pass_scatter(rows, run_dir / "rss_vs_pass.png")
    write_with_vs_without_chart(summary, run_dir / "with_vs_without.png")
    write_significance_report(rows, run_dir / "significance")

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
