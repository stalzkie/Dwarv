"""Unit tests for LlamaCppRuntime's process-management logic, with
subprocess.Popen and the health-check HTTP call mocked out -- no real
llama-server binary or model weights needed (those are exercised live
elsewhere, e.g. `dwarv doctor`/`dwarv eval`, not in this suite, since CI
doesn't download multi-GB models)."""

from dwarv.runtime.llamacpp import LlamaCppRuntime


class FakeProcess:
    """Stands in for subprocess.Popen -- starts "alive" (poll() -> None)
    until killed() is called, mirroring a real llama-server process."""

    _next_pid = 1000

    def __init__(self, *args, **kwargs):
        self.pid = FakeProcess._next_pid
        FakeProcess._next_pid += 1
        self._alive = True
        self.stdout = None
        self.returncode = 0

    def poll(self):
        return None if self._alive else self.returncode

    def terminate(self):
        self._alive = False

    def kill(self):
        self._alive = False

    def wait(self, timeout=None):
        return self.returncode


class FakeResponse:
    def __init__(self, status_code=200):
        self.status_code = status_code


def _patch_runtime(monkeypatch):
    """Patches subprocess.Popen (spawn) and httpx.get (readiness poll) so
    load() succeeds instantly without a real server. Returns the list of
    FakeProcess instances spawned, in order -- its length is the real
    spawn count regardless of how many times load() was called."""
    spawned: list[FakeProcess] = []

    def fake_popen(*args, **kwargs):
        proc = FakeProcess(*args, **kwargs)
        spawned.append(proc)
        return proc

    monkeypatch.setattr("dwarv.runtime.llamacpp.subprocess.Popen", fake_popen)
    monkeypatch.setattr(
        "dwarv.runtime.llamacpp.httpx.get", lambda url, timeout=None: FakeResponse()
    )
    monkeypatch.setattr("dwarv.runtime.llamacpp.httpx.Client", lambda **kwargs: None)
    return spawned


def test_load_same_model_and_ctx_twice_skips_the_second_spawn(monkeypatch):
    spawned = _patch_runtime(monkeypatch)
    runtime = LlamaCppRuntime("fake-llama-server", {"small": "/models/small.gguf"})

    first_s = runtime.load("small", ctx_size=4096)
    second_s = runtime.load("small", ctx_size=4096)

    assert len(spawned) == 1, "second identical load() must not spawn a new process"
    assert second_s == 0.0
    assert first_s >= 0.0


def test_load_different_model_id_spawns_a_fresh_process(monkeypatch):
    spawned = _patch_runtime(monkeypatch)
    runtime = LlamaCppRuntime(
        "fake-llama-server", {"small": "/models/small.gguf", "large": "/models/large.gguf"}
    )

    runtime.load("small", ctx_size=4096)
    runtime.load("large", ctx_size=4096)

    assert len(spawned) == 2
    assert spawned[0]._alive is False, "switching models must terminate the old process"


def test_load_different_ctx_size_spawns_a_fresh_process(monkeypatch):
    spawned = _patch_runtime(monkeypatch)
    runtime = LlamaCppRuntime("fake-llama-server", {"small": "/models/small.gguf"})

    runtime.load("small", ctx_size=4096)
    runtime.load("small", ctx_size=8192)

    assert len(spawned) == 2


def test_load_same_model_different_gpu_layers_spawns_a_fresh_process(monkeypatch):
    """The no-op skip must key on gpu_layers too, not just model_id/ctx_size
    -- otherwise switching a resident model between CPU-only and GPU
    offload (e.g. after a VRAM re-check) would wrongly be treated as a
    no-op and never actually apply -ngl."""
    spawned = _patch_runtime(monkeypatch)
    runtime = LlamaCppRuntime("fake-llama-server", {"small": "/models/small.gguf"})

    runtime.load("small", ctx_size=4096, gpu_layers=0)
    runtime.load("small", ctx_size=4096, gpu_layers=99)

    assert len(spawned) == 2


def test_load_passes_ngl_flag_only_when_gpu_layers_is_nonzero(monkeypatch):
    captured_args = []
    _patch_runtime(monkeypatch)

    def fake_popen(args, **kwargs):
        captured_args.append(args)
        return FakeProcess()

    monkeypatch.setattr("dwarv.runtime.llamacpp.subprocess.Popen", fake_popen)
    runtime = LlamaCppRuntime("fake-llama-server", {"small": "/models/small.gguf"})

    runtime.load("small", ctx_size=4096)
    assert "-ngl" not in captured_args[0]

    runtime.unload()
    runtime.load("small", ctx_size=4096, gpu_layers=99)
    assert captured_args[1][captured_args[1].index("-ngl") + 1] == "99"


def test_load_reloads_if_the_resident_process_already_exited(monkeypatch):
    """Guards against the no-op skip hiding a real crash: if the process
    died between calls, a same-model/ctx load() must still relaunch it
    rather than wrongly believing it's still resident."""
    spawned = _patch_runtime(monkeypatch)
    runtime = LlamaCppRuntime("fake-llama-server", {"small": "/models/small.gguf"})

    runtime.load("small", ctx_size=4096)
    spawned[0]._alive = False  # simulate the server crashing on its own

    runtime.load("small", ctx_size=4096)

    assert len(spawned) == 2, "a dead resident process must be relaunched, not skipped"
