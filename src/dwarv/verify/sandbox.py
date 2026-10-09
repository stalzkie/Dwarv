import os
import platform
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import psutil

DEFAULT_TIMEOUT_S = 30.0
DEFAULT_MEMORY_LIMIT_MB = 2048.0
DOCKER_IMAGE = "python:3.11-slim"  # MVP assumes a Python repo; see docs/DECISIONS.md


@dataclass
class SandboxResult:
    returncode: int | None
    stdout: str
    stderr: str
    timed_out: bool
    tier: int
    tier_detail: str


def docker_available() -> bool:
    docker = shutil.which("docker")
    if not docker:
        return False
    try:
        out = subprocess.run([docker, "info"], capture_output=True, text=True, timeout=5)
        return out.returncode == 0
    except Exception:
        return False


def unshare_network_available() -> bool:
    """Whether `unshare -n` actually works here, not just whether the binary
    exists -- e.g. GitHub Actions' ubuntu-latest runners ship `unshare` but
    don't permit unprivileged users to create network namespaces with it."""
    if platform.system() != "Linux" or shutil.which("unshare") is None:
        return False
    try:
        return (
            subprocess.run(
                ["unshare", "-n", "--", "true"], capture_output=True, timeout=3
            ).returncode
            == 0
        )
    except Exception:
        return False


def _native_tier() -> tuple[int, str]:
    """The best non-Docker tier for this OS."""
    system = platform.system()
    if system in ("Linux", "Darwin"):
        if unshare_network_available():
            return 2, "resource.setrlimit + unshare -n"
        if system == "Linux":
            return (
                2,
                "resource.setrlimit only (unshare present but not permitted in this environment)",
            )
        return 2, "resource.setrlimit only (no unshare on macOS)"
    return 3, "Windows Job Object + timeout -- reduced isolation, no rlimit/unshare equivalent"


def sandbox_tier() -> tuple[int, str]:
    """Pick the verifier sandbox tier per DWARV_PLAN.md section 2.3. The
    '+ unshare -n' suffix in the tier-2 detail string is the only reliable
    signal that network isolation is actually active on this tier -- callers
    that care should check for that exact substring, not just "unshare"."""
    if docker_available():
        return 1, "Docker (--network none) -- strongest, cross-platform"
    return _native_tier()


def _merged_env(extra_env: dict[str, str] | None) -> dict[str, str] | None:
    """Inherit the caller's environment and layer `extra_env` on top,
    appending to (never replacing) any var the user's own environment
    already set -- e.g. PYTHONPATH, so a src-layout repo's own pythonpath
    config isn't clobbered. Returns None (inherit as-is) when there's
    nothing to add, so callers that don't need this stay byte-identical."""
    if not extra_env:
        return None
    env = os.environ.copy()
    for key, value in extra_env.items():
        existing = env.get(key)
        env[key] = f"{value}{os.pathsep}{existing}" if existing else value
    return env


def run(
    cmd: list[str],
    cwd: Path,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    memory_limit_mb: float | None = DEFAULT_MEMORY_LIMIT_MB,
    force_tier: int | None = None,
    skip_docker: bool = False,
    extra_env: dict[str, str] | None = None,
) -> SandboxResult:
    """Run `cmd` in `cwd` under the tier `sandbox_tier()` selects (or
    `force_tier`, for deterministic testing without needing Docker). `cwd`
    must always be a disposable worktree (see repo/worktree.py) -- this
    function never protects the caller from running against a real tree.

    `skip_docker=True` falls through to the native tier even when Docker is
    available. Use this for running a *user's own* test command: Docker's
    generic `python:3.11-slim` image has none of a real project's installed
    dependencies (often not even the test runner itself), so it can only
    meaningfully run fully self-contained code (e.g. the Step 9 internal
    eval's EvalPlus candidates) -- not an arbitrary repo's real test suite,
    which needs the host/user's own activated environment. See
    docs/DECISIONS.md.

    `extra_env` is appended to the inherited environment (e.g.
    `{"PYTHONPATH": str(worktree)}` so bare `pytest` can import a root-level
    module from a `tests/` subdirectory -- see docs/DECISIONS.md for why
    this is a PYTHONPATH fix rather than switching to `python -m pytest`)."""
    if force_tier is not None:
        tier, tier_detail = force_tier, f"forced tier {force_tier}"
    elif skip_docker:
        tier, tier_detail = _native_tier()
    else:
        tier, tier_detail = sandbox_tier()
    env = _merged_env(extra_env)
    if tier == 1:
        return _run_tier1_docker(cmd, cwd, timeout_s, memory_limit_mb, tier_detail, env)
    if tier == 2:
        return _run_tier2_posix(cmd, cwd, timeout_s, memory_limit_mb, tier_detail, env)
    return _run_tier3_windows(cmd, cwd, timeout_s, memory_limit_mb, tier_detail, env)


def _run_tier1_docker(
    cmd: list[str],
    cwd: Path,
    timeout_s: float,
    memory_limit_mb: float | None,
    tier_detail: str,
    env: dict[str, str] | None,
) -> SandboxResult:
    docker_cmd = [
        "docker",
        "run",
        "--rm",
        "--network",
        "none",
        "-v",
        f"{cwd}:/workspace",
        "-w",
        "/workspace",
    ]
    if memory_limit_mb:
        docker_cmd += ["--memory", f"{int(memory_limit_mb)}m"]
    if env:
        for key, value in env.items():
            if os.environ.get(key) != value:
                docker_cmd += ["-e", f"{key}={value}"]
    docker_cmd += [DOCKER_IMAGE, *cmd]
    try:
        proc = subprocess.run(docker_cmd, capture_output=True, text=True, timeout=timeout_s)
        return SandboxResult(proc.returncode, proc.stdout, proc.stderr, False, 1, tier_detail)
    except subprocess.TimeoutExpired as exc:
        return SandboxResult(None, exc.stdout or "", exc.stderr or "", True, 1, tier_detail)


def _run_tier2_posix(
    cmd: list[str],
    cwd: Path,
    timeout_s: float,
    memory_limit_mb: float | None,
    tier_detail: str,
    env: dict[str, str] | None,
) -> SandboxResult:
    import resource  # POSIX only; this path never runs on Windows

    is_linux = platform.system() == "Linux"

    def _limits() -> None:
        # RLIMIT_AS is effectively unusable on macOS: dyld/the shared cache
        # needs far more virtual address space than Linux's loader even for
        # a trivial process, so setting a tight RLIMIT_AS here crashes the
        # child before it can even exec. Linux only.
        if memory_limit_mb and is_linux:
            limit_bytes = int(memory_limit_mb * 1024 * 1024)
            resource.setrlimit(resource.RLIMIT_AS, (limit_bytes, limit_bytes))
        cpu_s = int(timeout_s) + 1
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_s, cpu_s))

    full_cmd = cmd
    if unshare_network_available():
        full_cmd = ["unshare", "-n", "--", *cmd]

    try:
        proc = subprocess.run(
            full_cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            preexec_fn=_limits,
            env=env,
        )
        return SandboxResult(proc.returncode, proc.stdout, proc.stderr, False, 2, tier_detail)
    except subprocess.TimeoutExpired as exc:
        return SandboxResult(None, exc.stdout or "", exc.stderr or "", True, 2, tier_detail)


def _run_tier3_windows(
    cmd: list[str],
    cwd: Path,
    timeout_s: float,
    memory_limit_mb: float | None,
    tier_detail: str,
    env: dict[str, str] | None,
) -> SandboxResult:
    """No rlimit/unshare equivalent on Windows. The wall-clock timeout is real
    and kernel-enforced (the process is killed on expiry); the memory limit
    is an approximate watchdog polled via psutil, not a kernel-enforced cap
    like a true Job Object -- see docs/DECISIONS.md for why a full Job Object
    (CreateJobObject/SetInformationJobObject via pywin32 or ctypes) was
    deferred rather than built here."""
    proc = subprocess.Popen(
        cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env
    )
    start = time.monotonic()
    timed_out = False
    killed_for_memory = False

    while proc.poll() is None:
        if time.monotonic() - start > timeout_s:
            proc.kill()
            timed_out = True
            break
        if memory_limit_mb:
            try:
                rss_mb = psutil.Process(proc.pid).memory_info().rss / (1024 * 1024)
                if rss_mb > memory_limit_mb:
                    proc.kill()
                    killed_for_memory = True
                    break
            except psutil.NoSuchProcess:
                break
        time.sleep(0.1)

    stdout, stderr = proc.communicate()
    detail = tier_detail + (" [killed: exceeded memory watchdog]" if killed_for_memory else "")
    return SandboxResult(proc.returncode, stdout or "", stderr or "", timed_out, 3, detail)
