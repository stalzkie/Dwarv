import json
from dataclasses import dataclass
from pathlib import Path

import yaml

DEFAULT_MODELS_CONFIG_PATH = Path(__file__).resolve().parents[3] / "configs" / "models.yaml"

# The fixed, bundled 3-model suite, smallest to largest. Never an arbitrary
# user-supplied model -- see DWARV_PLAN.md section 1.4 / 2.2.
MODEL_ORDER = ["small", "medium", "large"]

DEFAULT_CTX_SIZE = 4096
DEFAULT_SAFETY_MARGIN = 0.10  # fraction of available RAM reserved before choosing a model

# DWARV_PLAN.md section 11.6: conservative multiplier for estimating a
# quant level's RSS from its file size alone, used only for the extra
# `quants` entries in configs/models.yaml that have no measured_rss_mb
# (bartowski's I-quant files -- Qwen's own official repo publishes none).
# Derived from our *own* 3 real (measured_rss_mb / file_size) ratios at
# ctx 4096 -- 1.621 (small), 1.670 (medium), 1.163 (large) -- taking the
# largest observed ratio so the estimate errs toward overestimating RSS
# (safer for a resource-aware selector than underestimating and picking
# something that doesn't actually fit). This is a labeled estimate, not a
# measurement -- see docs/DECISIONS.md for the real small/IQ2_M
# live-measured check and how close this estimate actually came.
QUANT_RSS_ESTIMATE_FACTOR = 1.67


def estimate_rss_mb(file_size_bytes: int) -> float:
    return (file_size_bytes / (1024 * 1024)) * QUANT_RSS_ESTIMATE_FACTOR


def load_models_config(path: str | Path = DEFAULT_MODELS_CONFIG_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


# DWARV_PLAN.md section 11.6: records which quant level `setup-offline`
# actually downloaded for each tier (model_id -> quant level; a tier using
# its default quant is simply omitted), so a later chat session's
# choose_model() and resolve_model_paths() calls reflect what's really on
# disk instead of assuming every tier is at its default quant.
QUANT_CHOICE_FILENAME = "quant_choice.json"


def load_quant_choices(cache_dir: str | Path) -> dict[str, str]:
    path = Path(cache_dir) / "models" / QUANT_CHOICE_FILENAME
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_quant_choices(cache_dir: str | Path, choices: dict[str, str]) -> None:
    path = Path(cache_dir) / "models" / QUANT_CHOICE_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(choices, indent=2), encoding="utf-8")


def resolve_model_paths(
    config: dict, cache_dir: str | Path, quant_choices: dict[str, str] | None = None
) -> dict[str, str]:
    """model_id -> absolute .gguf path under <cache_dir>/models/. `quant_choices`
    (model_id -> quant level, from setup-offline's QUANT_CHOICE_FILENAME) overrides
    the default filename with whichever quant was actually downloaded for that
    tier -- see find_best_quant_for_tier()."""
    models_dir = Path(cache_dir) / "models"
    quant_choices = quant_choices or {}
    paths = {}
    for entry in config.get("models", []):
        model_id = entry["id"]
        level = quant_choices.get(model_id)
        filename = entry["filename"]
        if level is not None:
            quant_entry = find_quant_entry(entry, level)
            if quant_entry is not None:
                filename = quant_entry["filename"]
        paths[model_id] = str(models_dir / filename)
    return paths


def find_quant_entry(entry: dict, level: str) -> dict | None:
    for q in entry.get("quants", []):
        if q["level"] == level:
            return q
    return None


def quant_rss_mb(entry: dict, quant_level: str | None, ctx_size: int) -> float | None:
    """RSS for `entry`'s default quant (quant_level=None -> real
    measured_rss_mb) or a named quant from entry['quants'] (quant_level set
    -> estimate_rss_mb(), labeled estimate, not a measurement)."""
    if quant_level is None:
        return (entry.get("measured_rss_mb") or {}).get(ctx_size)
    quant_entry = find_quant_entry(entry, quant_level)
    return estimate_rss_mb(quant_entry["file_size_bytes"]) if quant_entry else None


def find_best_quant_for_tier(
    entry: dict, budget_mb: float, ctx_size: int
) -> tuple[str | None, bool]:
    """Returns (quant_level, fits). quant_level is None for the default
    quant (the common case: it already fits, or nothing fits at all and
    there's no better option than the smallest/default download).
    `entry['quants']` must be ordered highest-quality (least compressed)
    first, as configs/models.yaml lists them -- the first one whose
    estimated RSS fits budget_mb is picked, preferring to stay as close to
    the default quality as the hardware allows rather than jumping
    straight to the most aggressive compression available."""
    default_rss = (entry.get("measured_rss_mb") or {}).get(ctx_size)
    if default_rss is not None and default_rss <= budget_mb:
        return None, True
    for q in entry.get("quants", []):
        if estimate_rss_mb(q["file_size_bytes"]) <= budget_mb:
            return q["level"], True
    return None, False


@dataclass
class Hardware:
    sys_available_mb: float
    sys_total_mb: float | None = None


@dataclass
class ModelChoice:
    model_id: str
    display_name: str
    explanation: str
    required_rss_mb: float
    sys_available_mb: float
    safety_margin_mb: float
    quant_level: str | None = None  # None = the tier's default quant


def _display_name(entry: dict) -> str:
    repo_name = entry["hf_repo"].split("/")[-1]
    return repo_name.removesuffix("-GGUF")


def choose_model(
    hardware: Hardware,
    models: list[dict] | None = None,
    ctx_size: int = DEFAULT_CTX_SIZE,
    safety_margin: float = DEFAULT_SAFETY_MARGIN,
    quant_choices: dict[str, str] | None = None,
) -> ModelChoice:
    """Pick the largest bundled model whose RSS fits hardware.sys_available_mb
    - safety_margin, and build a human-readable explanation from the real
    numbers used. Falls back to the smallest model (with an explicit warning
    in the explanation) if nothing comfortably fits, rather than refusing to
    start.

    `quant_choices` (model_id -> quant level, from setup-offline's recorded
    choice -- see find_best_quant_for_tier()) tells this function which
    quant is actually cached for each tier, so the RSS check reflects
    reality rather than always assuming the default quant. None (the
    default) means every tier is assumed to be at its default quant --
    today's exact behavior, unchanged for any existing caller."""
    if models is None:
        models = load_models_config().get("models", [])
    models_by_id = {m["id"]: m for m in models}
    quant_choices = quant_choices or {}
    safety_margin_mb = safety_margin * hardware.sys_available_mb
    budget_mb = hardware.sys_available_mb - safety_margin_mb

    chosen_id = None
    for model_id in reversed(MODEL_ORDER):  # largest first
        entry = models_by_id.get(model_id)
        if entry is None:
            continue
        rss_mb = quant_rss_mb(entry, quant_choices.get(model_id), ctx_size)
        if rss_mb is not None and rss_mb <= budget_mb:
            chosen_id = model_id
            break

    fallback = False
    if chosen_id is None:
        fallback = True
        chosen_id = next((mid for mid in MODEL_ORDER if mid in models_by_id), None)
        if chosen_id is None:
            raise RuntimeError("no bundled models configured in configs/models.yaml")

    entry = models_by_id[chosen_id]
    chosen_quant_level = quant_choices.get(chosen_id)
    rss_mb = quant_rss_mb(entry, chosen_quant_level, ctx_size)

    chosen_idx = MODEL_ORDER.index(chosen_id)
    next_up_name = next_up_rss_mb = None
    if chosen_idx + 1 < len(MODEL_ORDER):
        next_entry = models_by_id.get(MODEL_ORDER[chosen_idx + 1])
        if next_entry is not None:
            next_up_name = _display_name(next_entry)
            next_up_rss_mb = quant_rss_mb(
                next_entry, quant_choices.get(MODEL_ORDER[chosen_idx + 1]), ctx_size
            )

    explanation = _explain_choice(
        entry=entry,
        rss_mb=rss_mb,
        quant_level=chosen_quant_level,
        sys_available_mb=hardware.sys_available_mb,
        safety_margin_mb=safety_margin_mb,
        next_up_name=next_up_name,
        next_up_rss_mb=next_up_rss_mb,
        fallback=fallback,
    )

    return ModelChoice(
        model_id=chosen_id,
        display_name=_display_name(entry),
        explanation=explanation,
        required_rss_mb=rss_mb if rss_mb is not None else float("nan"),
        sys_available_mb=hardware.sys_available_mb,
        safety_margin_mb=safety_margin_mb,
        quant_level=chosen_quant_level,
    )


def _explain_choice(
    entry: dict,
    rss_mb: float | None,
    quant_level: str | None,
    sys_available_mb: float,
    safety_margin_mb: float,
    next_up_name: str | None,
    next_up_rss_mb: float | None,
    fallback: bool,
) -> str:
    name = _display_name(entry)
    if quant_level is not None:
        name = f"{name} ({quant_level})"
    available_gb = sys_available_mb / 1024
    margin_gb = safety_margin_mb / 1024

    if rss_mb is None:
        return (
            f"Using {name} this session, but it has no measured RSS on record for this "
            f"context size -- run scripts/benchmark_models.py to fill that in. Proceeding "
            f"cautiously since it's the smallest bundled option."
        )

    rss_gb = rss_mb / 1024
    estimate_note = (
        " (estimated from file size, not independently measured -- see docs/DECISIONS.md)"
        if quant_level is not None
        else ""
    )
    base = (
        f"Using {name} this session -- you have {available_gb:.1f}GB free "
        f"(keeping a {margin_gb:.1f}GB safety margin), and {name} needs about "
        f"{rss_gb:.1f}GB{estimate_note}."
    )
    if quant_level is not None:
        base += (
            f" The default quant for {_display_name(entry)} didn't fit, so this is a more "
            "compressed version of the same model -- a real quality tradeoff, not free."
        )
    if fallback:
        return (
            f"{base} That's tighter than I'd like, but it's the smallest model in the "
            f"bundled suite, so there's no smaller option to fall back to."
        )
    if next_up_name and next_up_rss_mb is not None:
        next_gb = next_up_rss_mb / 1024
        return (
            f"{base} The next size up, {next_up_name}, would need about {next_gb:.1f}GB -- "
            f"more than fits comfortably right now."
        )
    return f"{base} That leaves headroom to spare."
