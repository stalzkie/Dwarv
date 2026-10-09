from dataclasses import dataclass
from pathlib import Path

import yaml

DEFAULT_MODELS_CONFIG_PATH = Path(__file__).resolve().parents[3] / "configs" / "models.yaml"

# The fixed, bundled 3-model suite, smallest to largest. Never an arbitrary
# user-supplied model -- see DWARV_PLAN.md section 1.4 / 2.2.
MODEL_ORDER = ["small", "medium", "large"]

DEFAULT_CTX_SIZE = 4096
DEFAULT_SAFETY_MARGIN = 0.10  # fraction of available RAM reserved before choosing a model


def load_models_config(path: str | Path = DEFAULT_MODELS_CONFIG_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def resolve_model_paths(config: dict, cache_dir: str | Path) -> dict[str, str]:
    """model_id -> absolute .gguf path under <cache_dir>/models/."""
    models_dir = Path(cache_dir) / "models"
    return {entry["id"]: str(models_dir / entry["filename"]) for entry in config.get("models", [])}


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


def _display_name(entry: dict) -> str:
    repo_name = entry["hf_repo"].split("/")[-1]
    return repo_name.removesuffix("-GGUF")


def choose_model(
    hardware: Hardware,
    models: list[dict] | None = None,
    ctx_size: int = DEFAULT_CTX_SIZE,
    safety_margin: float = DEFAULT_SAFETY_MARGIN,
) -> ModelChoice:
    """Pick the largest bundled model whose measured RSS fits
    hardware.sys_available_mb - safety_margin, and build a human-readable
    explanation from the real numbers used. Falls back to the smallest
    model (with an explicit warning in the explanation) if nothing
    comfortably fits, rather than refusing to start."""
    if models is None:
        models = load_models_config().get("models", [])
    models_by_id = {m["id"]: m for m in models}
    safety_margin_mb = safety_margin * hardware.sys_available_mb
    budget_mb = hardware.sys_available_mb - safety_margin_mb

    chosen_id = None
    for model_id in reversed(MODEL_ORDER):  # largest first
        entry = models_by_id.get(model_id)
        if entry is None:
            continue
        rss_mb = (entry.get("measured_rss_mb") or {}).get(ctx_size)
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
    rss_mb = (entry.get("measured_rss_mb") or {}).get(ctx_size)

    chosen_idx = MODEL_ORDER.index(chosen_id)
    next_up_name = next_up_rss_mb = None
    if chosen_idx + 1 < len(MODEL_ORDER):
        next_entry = models_by_id.get(MODEL_ORDER[chosen_idx + 1])
        if next_entry is not None:
            next_up_name = _display_name(next_entry)
            next_up_rss_mb = (next_entry.get("measured_rss_mb") or {}).get(ctx_size)

    explanation = _explain_choice(
        entry=entry,
        rss_mb=rss_mb,
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
    )


def _explain_choice(
    entry: dict,
    rss_mb: float | None,
    sys_available_mb: float,
    safety_margin_mb: float,
    next_up_name: str | None,
    next_up_rss_mb: float | None,
    fallback: bool,
) -> str:
    name = _display_name(entry)
    available_gb = sys_available_mb / 1024
    margin_gb = safety_margin_mb / 1024

    if rss_mb is None:
        return (
            f"Using {name} this session, but it has no measured RSS on record for this "
            f"context size -- run scripts/benchmark_models.py to fill that in. Proceeding "
            f"cautiously since it's the smallest bundled option."
        )

    rss_gb = rss_mb / 1024
    base = (
        f"Using {name} this session -- you have {available_gb:.1f}GB free "
        f"(keeping a {margin_gb:.1f}GB safety margin), and {name} needs about {rss_gb:.1f}GB."
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
