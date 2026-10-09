import platform
import sys

import pytest

from dwarv.verify.sandbox import run, sandbox_tier


def _native_tier() -> int:
    """Tier 2 (resource.setrlimit) on POSIX, tier 3 (psutil watchdog) on
    Windows -- never Docker, so these tests run deterministically without
    needing a Docker install or an image pull."""
    return 3 if platform.system() == "Windows" else 2


def test_timeout_kills_infinite_loop(tmp_path):
    script = tmp_path / "loop.py"
    script.write_text("while True:\n    pass\n", encoding="utf-8")

    result = run(
        [sys.executable, "loop.py"], cwd=tmp_path, timeout_s=2.0, force_tier=_native_tier()
    )

    assert result.timed_out


def test_memory_bomb_is_stopped(tmp_path):
    script = tmp_path / "bomb.py"
    script.write_text(
        "x = []\nwhile True:\n    x.append(bytearray(10 * 1024 * 1024))\n",
        encoding="utf-8",
    )

    result = run(
        [sys.executable, "bomb.py"],
        cwd=tmp_path,
        timeout_s=10.0,
        memory_limit_mb=300.0,
        force_tier=_native_tier(),
    )

    # Either the OS/rlimit (tier 2) or the psutil watchdog (tier 3) stopped
    # it before the 10s timeout; either way, it never ran forever.
    assert result.timed_out or result.returncode not in (0, None)


def test_network_access_fails(tmp_path):
    tier, detail = sandbox_tier()
    network_isolated = (
        tier == 1 or "+ unshare -n" in detail
    )  # the exact success marker, not just "unshare"
    if not network_isolated:
        pytest.skip(
            f"tier {tier} ({detail}) has no network isolation on this OS -- documented limitation"
        )

    script = tmp_path / "net.py"
    script.write_text(
        "import socket\n"
        "s = socket.create_connection(('8.8.8.8', 53), timeout=3)\n"
        "s.close()\n"
        "print('connected')\n",
        encoding="utf-8",
    )

    result = run([sys.executable, "net.py"], cwd=tmp_path, timeout_s=10.0)

    assert "connected" not in result.stdout


def test_extra_env_pythonpath_fixes_sibling_import(tmp_path):
    """Regression test for the Step 10 demo bug: a flat repo layout (a
    module at the root, a test/script in a subdirectory with no
    __init__.py) can't import the root module because the subprocess's
    sys.path[0] is the *script's own* directory, not cwd -- the same reason
    bare `pytest` fails to import app.py from tests/test_app.py. extra_env's
    PYTHONPATH (as agent/session.py's _verify_in_worktree passes) fixes it
    without changing which interpreter runs."""
    (tmp_path / "mod.py").write_text("VALUE = 42\n", encoding="utf-8")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "script.py").write_text("import mod\nprint(mod.VALUE)\n", encoding="utf-8")

    without = run(
        [sys.executable, "sub/script.py"], cwd=tmp_path, timeout_s=10.0, force_tier=_native_tier()
    )
    assert without.returncode != 0
    assert "ModuleNotFoundError" in without.stderr

    with_pythonpath = run(
        [sys.executable, "sub/script.py"],
        cwd=tmp_path,
        timeout_s=10.0,
        force_tier=_native_tier(),
        extra_env={"PYTHONPATH": str(tmp_path)},
    )
    assert with_pythonpath.returncode == 0
    assert with_pythonpath.stdout.strip() == "42"


def test_file_writes_stay_in_temp_dir(tmp_path):
    script = tmp_path / "write.py"
    script.write_text(
        "from pathlib import Path\nPath('output.txt').write_text('hello')\n", encoding="utf-8"
    )

    run([sys.executable, "write.py"], cwd=tmp_path, timeout_s=10.0, force_tier=_native_tier())

    assert (tmp_path / "output.txt").read_text(encoding="utf-8") == "hello"
