import platform
import subprocess
from unittest.mock import patch

import pytest

from dwarv.models.requantize import (
    derived_model_path,
    llama_quantize_path,
    requantize,
)


def test_llama_quantize_path_matches_platform_exe_name(tmp_path):
    path = llama_quantize_path(tmp_path)
    assert path.parent == tmp_path / "llama.cpp"
    if platform.system() == "Windows":
        assert path.name == "llama-quantize.exe"
    else:
        assert path.name == "llama-quantize"


def test_derived_model_path_naming():
    result = derived_model_path("/cache/models/qwen2.5-coder-32b-instruct-q4_k_m.gguf", "Q3_K_M")
    assert result.name == "qwen2.5-coder-32b-instruct-q4_k_m.q3_k_m.gguf"


def test_requantize_raises_if_input_missing(tmp_path):
    with pytest.raises(FileNotFoundError):
        requantize(
            tmp_path / "missing.gguf",
            tmp_path / "out.gguf",
            "Q3_K_M",
            quantize_bin="/fake/llama-quantize",
        )


def test_requantize_builds_the_correct_command_and_reports_success(tmp_path):
    input_path = tmp_path / "in.gguf"
    input_path.write_bytes(b"fake gguf bytes" * 100)
    output_path = tmp_path / "out.gguf"

    def fake_run(cmd, capture_output, text, timeout):
        assert cmd == [
            "/fake/llama-quantize",
            "--allow-requantize",
            str(input_path),
            str(output_path),
            "Q3_K_M",
        ]
        # Simulate the real binary actually producing a smaller output file.
        output_path.write_bytes(b"smaller" * 10)
        return subprocess.CompletedProcess(cmd, returncode=0, stdout="quantized ok", stderr="")

    with patch("subprocess.run", side_effect=fake_run):
        result = requantize(input_path, output_path, "Q3_K_M", quantize_bin="/fake/llama-quantize")

    assert result.ok
    assert result.returncode == 0
    assert not result.timed_out
    assert result.input_size_bytes == len(b"fake gguf bytes" * 100)
    assert result.output_size_bytes == len(b"smaller" * 10)
    assert result.output_size_bytes < result.input_size_bytes
    assert "quantized ok" in result.stdout


def test_requantize_reports_nonzero_returncode_as_not_ok(tmp_path):
    input_path = tmp_path / "in.gguf"
    input_path.write_bytes(b"x")
    output_path = tmp_path / "out.gguf"

    def fake_run(cmd, capture_output, text, timeout):
        return subprocess.CompletedProcess(cmd, returncode=1, stdout="", stderr="bad quant type")

    with patch("subprocess.run", side_effect=fake_run):
        result = requantize(
            input_path, output_path, "NOT_A_REAL_LEVEL", quantize_bin="/fake/llama-quantize"
        )

    assert not result.ok
    assert result.returncode == 1
    assert "bad quant type" in result.stderr
    assert result.output_size_bytes == 0  # no output file was ever created


def test_requantize_handles_timeout(tmp_path):
    input_path = tmp_path / "in.gguf"
    input_path.write_bytes(b"x")
    output_path = tmp_path / "out.gguf"

    def fake_run(cmd, capture_output, text, timeout):
        raise subprocess.TimeoutExpired(cmd, timeout)

    with patch("subprocess.run", side_effect=fake_run):
        result = requantize(
            input_path,
            output_path,
            "Q3_K_M",
            quantize_bin="/fake/llama-quantize",
            timeout_s=0.01,
        )

    assert result.timed_out
    assert not result.ok
    assert result.returncode is None
