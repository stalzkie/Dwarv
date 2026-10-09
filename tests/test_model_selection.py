from dwarv.models.suite import Hardware, choose_model

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
