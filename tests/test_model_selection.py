from dwarv.models.suite import (
    Hardware,
    choose_model,
    estimate_rss_mb,
    find_best_quant_for_tier,
    find_quant_entry,
    quant_rss_mb,
    resolve_model_paths,
)

# Real figures from benchmarks/history/20261009T083427Z/ (ctx 4096), so the
# tests exercise the algorithm against genuine measured data, not invented
# numbers.
FAKE_MODELS = [
    {
        "id": "small",
        "hf_repo": "Qwen/Qwen2.5-Coder-1.5B-Instruct-GGUF",
        "measured_rss_mb": {4096: 1727.3},
    },
    {
        "id": "medium",
        "hf_repo": "Qwen/Qwen2.5-Coder-7B-Instruct-GGUF",
        "measured_rss_mb": {4096: 7458.5},
    },
    {
        "id": "large",
        "hf_repo": "Qwen/Qwen2.5-Coder-14B-Instruct-GGUF",
        "measured_rss_mb": {4096: 9965.0},
    },
]


def test_low_ram_picks_small():
    choice = choose_model(Hardware(sys_available_mb=3000.0), models=FAKE_MODELS)
    assert choice.model_id == "small"


def test_ample_ram_picks_large():
    choice = choose_model(Hardware(sys_available_mb=20000.0), models=FAKE_MODELS)
    assert choice.model_id == "large"


def test_borderline_respects_safety_margin():
    # medium needs 7458.5MB; at 8280MB available, a 10% margin leaves only
    # 7452MB (just under), but a 0% margin leaves the full 8280MB (enough).
    tight = choose_model(Hardware(sys_available_mb=8280.0), models=FAKE_MODELS, safety_margin=0.10)
    assert tight.model_id == "small"

    loose = choose_model(Hardware(sys_available_mb=8280.0), models=FAKE_MODELS, safety_margin=0.0)
    assert loose.model_id == "medium"


def test_explanation_matches_numbers_used():
    choice = choose_model(Hardware(sys_available_mb=20000.0), models=FAKE_MODELS)
    available_gb = 20000.0 / 1024
    rss_gb = 9965.0 / 1024
    assert f"{available_gb:.1f}GB" in choice.explanation
    assert f"{rss_gb:.1f}GB" in choice.explanation
    assert "Qwen2.5-Coder-14B-Instruct" in choice.explanation


def test_falls_back_to_smallest_when_nothing_fits():
    choice = choose_model(Hardware(sys_available_mb=500.0), models=FAKE_MODELS)
    assert choice.model_id == "small"
    assert "no smaller option" in choice.explanation


# DWARV_PLAN.md section 11.6 -- real file sizes for "large"'s quants,
# verified via the HF API against bartowski/Qwen2.5-Coder-14B-Instruct-GGUF
# 2026-10-10 (same numbers as configs/models.yaml), ordered highest-quality
# first as the real config does.
FAKE_MODELS_WITH_QUANTS = [
    *FAKE_MODELS[:-1],
    {
        **FAKE_MODELS[-1],
        "quants": [
            {"level": "IQ4_XS", "file_size_bytes": 8119840992},
            {"level": "IQ4_NL", "file_size_bytes": 8549183712},
            {"level": "IQ3_M", "file_size_bytes": 6916538592},
            {"level": "IQ3_XS", "file_size_bytes": 6383362272},
            {"level": "IQ2_M", "file_size_bytes": 5356146912},
            {"level": "IQ2_S", "file_size_bytes": 5003727072},
            {"level": "IQ2_XS", "file_size_bytes": 4704575712},
        ],
    },
]


def test_estimate_rss_mb_uses_conservative_factor():
    # 1GB file -> 1024MB * 1.67
    assert estimate_rss_mb(1024 * 1024 * 1024) == 1024 * 1.67


def test_find_quant_entry_found_and_missing():
    large = FAKE_MODELS_WITH_QUANTS[-1]
    assert find_quant_entry(large, "IQ2_M")["file_size_bytes"] == 5356146912
    assert find_quant_entry(large, "NOT_A_LEVEL") is None


def test_quant_rss_mb_default_vs_named():
    large = FAKE_MODELS_WITH_QUANTS[-1]
    assert quant_rss_mb(large, None, 4096) == 9965.0  # real measured default
    assert quant_rss_mb(large, "IQ2_M", 4096) == estimate_rss_mb(5356146912)
    assert quant_rss_mb(large, "NOT_A_LEVEL", 4096) is None


def test_find_best_quant_for_tier_prefers_default_when_it_fits():
    large = FAKE_MODELS_WITH_QUANTS[-1]
    assert find_best_quant_for_tier(large, budget_mb=20000.0, ctx_size=4096) == (None, True)


def test_find_best_quant_for_tier_picks_highest_quality_that_fits():
    large = FAKE_MODELS_WITH_QUANTS[-1]
    # default (9965) doesn't fit; IQ2_M's estimate (~8528.8) is the highest
    # -quality quant in the list whose estimate fits 8700.
    level, fits = find_best_quant_for_tier(large, budget_mb=8700.0, ctx_size=4096)
    assert fits is True
    assert level == "IQ2_M"


def test_find_best_quant_for_tier_nothing_fits_even_maximally_compressed():
    large = FAKE_MODELS_WITH_QUANTS[-1]
    level, fits = find_best_quant_for_tier(large, budget_mb=100.0, ctx_size=4096)
    assert fits is False
    assert level is None


def test_choose_model_without_quant_choices_falls_back_to_smaller_tier():
    # Unchanged default behavior: large's default (9965) doesn't fit 8700,
    # and with no quant_choices supplied, nothing is known about large's
    # quants being available/cached -- falls back to medium, same as today.
    choice = choose_model(
        Hardware(sys_available_mb=8700.0), models=FAKE_MODELS_WITH_QUANTS, safety_margin=0.0
    )
    assert choice.model_id == "medium"
    assert choice.quant_level is None


def test_choose_model_with_quant_choices_keeps_the_bigger_tier_compressed():
    # Once setup-offline has recorded that "large" is cached as IQ2_M,
    # choose_model correctly uses that RSS instead of assuming the default
    # -- and prefers the bigger, compressed model over the smaller, full
    # -quality one.
    choice = choose_model(
        Hardware(sys_available_mb=8700.0),
        models=FAKE_MODELS_WITH_QUANTS,
        safety_margin=0.0,
        quant_choices={"large": "IQ2_M"},
    )
    assert choice.model_id == "large"
    assert choice.quant_level == "IQ2_M"
    assert "IQ2_M" in choice.explanation
    assert "estimated from file size" in choice.explanation


def test_resolve_model_paths_uses_quant_choice_filename(tmp_path):
    models_with_filenames = [
        {
            "id": "large",
            "filename": "qwen2.5-coder-14b-instruct-q4_k_m.gguf",
            "quants": [
                {
                    "level": "IQ2_M",
                    "filename": "Qwen2.5-Coder-14B-Instruct-IQ2_M.gguf",
                    "file_size_bytes": 5356146912,
                }
            ],
        }
    ]

    default_paths = resolve_model_paths({"models": models_with_filenames}, tmp_path)
    assert default_paths["large"].endswith("qwen2.5-coder-14b-instruct-q4_k_m.gguf")

    overridden_paths = resolve_model_paths(
        {"models": models_with_filenames}, tmp_path, quant_choices={"large": "IQ2_M"}
    )
    assert overridden_paths["large"].endswith("Qwen2.5-Coder-14B-Instruct-IQ2_M.gguf")
