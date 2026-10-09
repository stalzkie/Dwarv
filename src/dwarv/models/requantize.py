"""DWARV_PLAN.md section 11.1: local GGUF re-quantization.

Uses the `llama-quantize` binary already bundled in the same llama.cpp
release archive Dwarv downloads for `llama-server` (verified: present in
`llama-b11516-bin-win-cpu-x64.zip` alongside `llama-server.exe`) -- no new
download. This runs Dwarv's own trusted tool against Dwarv's own cached
model file, not untrusted repo/model content, so it's a plain subprocess
call rather than one of verify/sandbox.py's sandboxed tiers.

`--allow-requantize` is required because the input is already a quantized
GGUF, not the original F16/F32 checkpoint -- real quality cost versus
quantizing fresh from full precision (per llama-quantize's own docs).
Callers must surface that tradeoff to the user, not hide it.
"""

import platform
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

DEFAULT_TIMEOUT_S = 1800.0  # requantizing a large (e.g. 32B) model can take minutes


@dataclass
class RequantizeResult:
    output_path: Path
    input_size_bytes: int
    output_size_bytes: int
    wall_s: float
    returncode: int | None
    timed_out: bool
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return not self.timed_out and self.returncode == 0 and self.output_path.exists()


def llama_quantize_path(cache_dir: str | Path) -> Path:
    """Same directory as llama-server -- see cli.py's _llama_server_path;
    both binaries come from the same extracted release archive."""
    exe_name = "llama-quantize.exe" if platform.system() == "Windows" else "llama-quantize"
    return Path(cache_dir) / "llama.cpp" / exe_name


def derived_model_path(input_path: str | Path, target_level: str) -> Path:
    """Where a derived quant is cached on disk, so a second call can detect
    it's already been computed and skip re-running quantize. E.g.
    qwen2.5-coder-32b-instruct-q4_k_m.gguf + "Q3_K_M" ->
    qwen2.5-coder-32b-instruct-q4_k_m.q3_k_m.gguf"""
    input_path = Path(input_path)
    suffix = target_level.lower()
    return input_path.with_name(f"{input_path.stem}.{suffix}{input_path.suffix}")


def requantize(
    input_path: str | Path,
    output_path: str | Path,
    target_level: str,
    quantize_bin: str | Path,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> RequantizeResult:
    """Produce `output_path` at `target_level` (e.g. "Q3_K_M") from the
    already-quantized `input_path`, using the real `llama-quantize` binary
    at `quantize_bin`."""
    input_path = Path(input_path)
    output_path = Path(output_path)
    if not input_path.exists():
        raise FileNotFoundError(f"requantize input not found: {input_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        str(quantize_bin),
        "--allow-requantize",
        str(input_path),
        str(output_path),
        target_level,
    ]
    start = time.monotonic()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
        returncode, timed_out = proc.returncode, False
        stdout, stderr = proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as exc:
        returncode, timed_out = None, True
        stdout, stderr = exc.stdout or "", exc.stderr or ""
    wall_s = time.monotonic() - start

    return RequantizeResult(
        output_path=output_path,
        input_size_bytes=input_path.stat().st_size,
        output_size_bytes=output_path.stat().st_size if output_path.exists() else 0,
        wall_s=wall_s,
        returncode=returncode,
        timed_out=timed_out,
        stdout=stdout,
        stderr=stderr,
    )
