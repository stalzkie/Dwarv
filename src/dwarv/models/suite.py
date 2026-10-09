from pathlib import Path

import yaml

DEFAULT_MODELS_CONFIG_PATH = Path(__file__).resolve().parents[3] / "configs" / "models.yaml"


def load_models_config(path: str | Path = DEFAULT_MODELS_CONFIG_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def resolve_model_paths(config: dict, cache_dir: str | Path) -> dict[str, str]:
    """model_id -> absolute .gguf path under <cache_dir>/models/."""
    models_dir = Path(cache_dir) / "models"
    return {entry["id"]: str(models_dir / entry["filename"]) for entry in config.get("models", [])}


def choose_model(hardware, models=None):
    raise NotImplementedError(
        "Step 5: choose_model(hardware, models=[small, medium, large]) -> (ModelId, Explanation). "
        "Picks the largest bundled Qwen2.5-Coder size whose measured RSS (configs/models.yaml) "
        "fits sys_available_mb - safety_margin, and builds a human-readable explanation from the "
        "real numbers used -- never say anything the numbers don't support."
    )
