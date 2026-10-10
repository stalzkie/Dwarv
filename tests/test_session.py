import json
import subprocess
import sys
from pathlib import Path

from dwarv.agent.session import _BANNER, ChatSession
from dwarv.controller.actions import Action
from dwarv.runtime.base import GenResult


def _direct_answer(message: str) -> str:
    """Builds a DWARV_RESPONSE_SCHEMA-shaped direct_answer response, the
    same structure a real schema-constrained generate() call returns (see
    DWARV_PLAN.md section 11.8) -- FakeRuntime scripts this instead of
    plain text so these tests exercise the real parsing path."""
    return json.dumps({"kind": "direct_answer", "message": message, "files": []})


def _patch_response(message: str, files: list[tuple[str, str]]) -> str:
    return json.dumps(
        {
            "kind": "patch",
            "message": message,
            "files": [{"path": path, "content": content} for path, content in files],
        }
    )


FAKE_MODELS_CONFIG = {
    "models": [
        {
            "id": "small",
            "hf_repo": "Qwen/Qwen2.5-Coder-1.5B-Instruct-GGUF",
            "measured_rss_mb": {4096: 1727.3},
            "load_time_s": {4096: 6.03},
        },
        {
            "id": "medium",
            "hf_repo": "Qwen/Qwen2.5-Coder-7B-Instruct-GGUF",
            "measured_rss_mb": {4096: 7458.5},
            "load_time_s": {4096: 23.55},
        },
        {
            "id": "large",
            "hf_repo": "Qwen/Qwen2.5-Coder-14B-Instruct-GGUF",
            "measured_rss_mb": {4096: 9965.0},
            "load_time_s": {4096: 86.42},
        },
    ]
}


class FakeRuntime:
    """Stands in for LlamaCppRuntime so these tests need no real llama-server
    or model file -- only the LLM call is faked, everything around it (repo
    detection, git worktree, patch application, sandboxed pytest) is real."""

    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self._model_id: str | None = None
        self._ctx: int | None = None
        self.load_calls: list[tuple[str, int]] = []

    def load(self, model_id: str, ctx_size: int) -> float:
        self._model_id = model_id
        self._ctx = ctx_size
        self.load_calls.append((model_id, ctx_size))
        return 0.01

    def unload(self) -> None:
        self._model_id = None

    def generate(self, messages, params) -> GenResult:
        text = self._responses.pop(0)
        return GenResult(
            text=text, prompt_tokens=10, completion_tokens=10, wall_s=0.01, finish_reason="stop"
        )

    def pid(self) -> int | None:
        return None

    def current(self) -> tuple[str | None, int | None]:
        return self._model_id, self._ctx


def _init_git_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
    (root / "app.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests" / "test_app.py").write_text(
        "from app import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n", encoding="utf-8"
    )
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True)


def _make_session(tmp_path: Path, responses: list[str]) -> tuple[ChatSession, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    session = ChatSession(
        runtime=FakeRuntime(responses), models_config=FAKE_MODELS_CONFIG, repo_dir=str(repo)
    )
    # Use this interpreter's own pytest rather than relying on PATH -- real
    # usage trusts the user's own activated environment (see repo/context.py);
    # this test fixture has no such environment to activate.
    session.repo_ctx.test_command = [sys.executable, "-m", "pytest"]
    session.start()
    return session, repo


def test_non_python_repo_falls_back_to_whole_repo_context(tmp_path):
    """Real gap found live: the AST-based graph (DWARV_PLAN.md section
    11.7) is Python-only, so a repo with zero .py files has zero graph
    symbols -- without a fallback, query_relevant_context() would return
    "" and the model would get NO code context at all, blind to the
    actual code, on any JS/Rust/Go/etc. repo. snapshot_repo_files()
    (the older, language-general dump) must be used instead whenever the
    graph found nothing to parse."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "app.js").write_text(
        "function applyDiscount(price, pct) {\n  return price - pct;\n}\n", encoding="utf-8"
    )
    (repo / "package.json").write_text('{"name": "demo", "scripts": {"test": "jest"}}\n')
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)

    session = ChatSession(
        runtime=FakeRuntime([_direct_answer("looks fine")]),
        models_config=FAKE_MODELS_CONFIG,
        repo_dir=str(repo),
    )
    session.start()
    assert session.repo_graph.symbols == {}  # confirms the graph found nothing to parse

    messages = session._messages_with_relevant_context("fix the bug in app.js")

    context_messages = [m["content"] for m in messages if "applyDiscount" in m.get("content", "")]
    assert context_messages, "the model must still see the real JS source, not nothing"


def test_direct_answer_no_code_block(tmp_path):
    session, _repo = _make_session(tmp_path, [_direct_answer("The function looks fine to me.")])

    reply = session.handle_message("what does add() do?")

    assert reply == "The function looks fine to me."


def test_proposes_and_applies_a_verified_fix(tmp_path):
    fixed = _patch_response(
        "Flipped the subtraction to addition.", [("app.py", "def add(a, b):\n    return a + b\n")]
    )
    session, repo = _make_session(tmp_path, [fixed])

    reply = session.handle_message("fix the failing test")

    assert "verified" in reply.lower()
    assert (repo / "app.py").read_text(encoding="utf-8") == "def add(a, b):\n    return a + b\n"


def test_patch_never_applied_to_real_files_when_no_test_command_exists(tmp_path):
    """CRITICAL regression test for a real, live incident: on a real repo
    with no discoverable test command, a purely diagnostic question
    ("identify files with errors") got a "patch" response back with
    destructive content, and the old code applied it straight to the
    user's real files with zero verification -- deleting two real files.
    This exact code path (test_command is None) had NO test coverage at
    all before this, since _make_session always sets a real test
    command. When there is no way to verify, Dwarv must never write to
    real files, no matter what the model proposes."""
    destructive = _patch_response("Removing this file.", [("app.py", "")])
    session, repo = _make_session(tmp_path, [destructive])
    session.repo_ctx.test_command = None
    original_content = (repo / "app.py").read_text(encoding="utf-8")

    reply = session.handle_message("identify files with errors")

    assert "not applied" in reply.lower()
    assert "no test command" in reply.lower()
    assert (repo / "app.py").read_text(encoding="utf-8") == original_content


def test_retries_with_feedback_then_succeeds(tmp_path):
    broken = _patch_response(
        "Attempt 1.", [("app.py", "def add(a, b):\n    return a - b\n")]
    )  # still wrong
    fixed = _patch_response(
        "Attempt 2, actually fixed now.", [("app.py", "def add(a, b):\n    return a + b\n")]
    )
    session, repo = _make_session(tmp_path, [broken, fixed])

    reply = session.handle_message("fix the failing test")

    assert "verified" in reply.lower()
    assert (repo / "app.py").read_text(encoding="utf-8") == "def add(a, b):\n    return a + b\n"
    assert session.last_decision is not None
    assert session.last_decision.action == Action.RETRY_WITH_FEEDBACK


def test_gives_up_after_max_attempts_without_touching_real_file(tmp_path):
    broken = _patch_response("Still trying.", [("app.py", "def add(a, b):\n    return a - b\n")])
    original = "def add(a, b):\n    return a - b\n"
    session, repo = _make_session(tmp_path, [broken] * 5)  # more than DEFAULT_MAX_ATTEMPTS

    reply = session.handle_message("fix the failing test")

    assert "not applied" in reply.lower()
    assert (repo / "app.py").read_text(encoding="utf-8") == original


def test_status_reflects_current_model_and_tier(tmp_path):
    session, _repo = _make_session(tmp_path, [])

    status = session.status_text()

    assert f"model: {session.current_model_id}" in status
    assert f"sandbox tier: {session.tier}" in status


def test_no_test_command_never_applies_real_changes(tmp_path):
    """This used to assert the opposite -- that an unverifiable patch WAS
    applied to the real file ("unverified" in the reply, file changed on
    disk). That was the exact real bug a live incident exposed (see
    test_patch_never_applied_to_real_files_when_no_test_command_exists):
    with no way to verify, Dwarv must never write to real files, however
    plausible-looking the proposed change is."""
    repo = tmp_path / "plain"
    repo.mkdir()
    (repo / "app.py").write_text("x = 1\n", encoding="utf-8")
    session = ChatSession(
        runtime=FakeRuntime([_patch_response("Updated x.", [("app.py", "x = 2\n")])]),
        models_config=FAKE_MODELS_CONFIG,
        repo_dir=str(repo),
    )
    session.start()

    reply = session.handle_message("update x")

    assert "not applied" in reply.lower()
    assert "no test command" in reply.lower()
    assert (repo / "app.py").read_text(encoding="utf-8") == "x = 1\n"


def test_malformed_response_degrades_to_raw_text_without_crashing(tmp_path):
    """Schema-constrained generation should make this unreachable in
    practice (see agent/prompts.py), but a killed/crashed server could in
    principle still hand back something else -- the turn must degrade
    gracefully, never raise an unhandled exception mid-conversation."""
    session, _repo = _make_session(tmp_path, ["not valid json at all"])

    reply = session.handle_message("what does add() do?")

    assert reply == "not valid json at all"


def test_truncated_json_response_salvages_the_message_instead_of_showing_raw_json(tmp_path):
    """Real failure mode found live running the demo script: a small
    model's degenerate repetition loop exhausted max_tokens before the
    JSON could close, so result.text was an unclosed
    '{"kind": "direct_answer", "message": "...' blob. The user must see
    the readable message text, never raw JSON syntax."""
    truncated = '{"kind": "direct_answer", "message": "The function now handles the'
    session, _repo = _make_session(tmp_path, [truncated])

    reply = session.handle_message("what does add() do?")

    assert reply == "The function now handles the"
    assert '"kind"' not in reply


def test_verified_fix_reply_includes_the_models_own_message(tmp_path):
    fixed = _patch_response(
        "Flipped the subtraction to addition.", [("app.py", "def add(a, b):\n    return a + b\n")]
    )
    session, _repo = _make_session(tmp_path, [fixed])

    reply = session.handle_message("fix the failing test")

    assert "Flipped the subtraction to addition." in reply


def test_resolve_reload_target_smaller_and_larger(tmp_path):
    session, _repo = _make_session(tmp_path, [])
    session.current_model_id = "medium"
    session.current_ctx_size = 4096

    assert session._resolve_reload_target(Action.SWITCH_SMALLER_MODEL) == ("small", 4096)
    assert session._resolve_reload_target(Action.SWITCH_LARGER_MODEL) == ("large", 4096)


def _rss_tracking_fn(runtime: FakeRuntime):
    """Returns the bundled measured RSS for whatever model `runtime`
    currently has loaded -- lets a squeeze test simulate a real reload
    actually relieving memory pressure, without a real OS process."""
    rss_by_model = {
        m["id"]: (m.get("measured_rss_mb") or {}).get(4096, 0.0)
        for m in FAKE_MODELS_CONFIG["models"]
    }

    def rss_fn() -> float:
        model_id, _ctx = runtime.current()
        return rss_by_model.get(model_id, 0.0)

    return rss_fn


def _coupled_resource_fns(runtime: FakeRuntime, total_capacity_mb: float):
    """Unlike _rss_tracking_fn's sibling tests (which use a constant,
    RSS-independent sys_available_fn), this couples the two the way the
    real ResourceMonitor actually does: psutil's "available" RAM
    mechanically shrinks by however much the server process's own RSS
    grows, since it's real system memory, not two independent numbers.
    Needed to reproduce the real bug this module's _turn() fix addresses
    -- the existing tests' decoupled fakes never could."""
    rss_by_model = {
        m["id"]: (m.get("measured_rss_mb") or {}).get(4096, 0.0)
        for m in FAKE_MODELS_CONFIG["models"]
    }

    def rss_fn() -> float:
        model_id, _ctx = runtime.current()
        return rss_by_model.get(model_id, 0.0)

    def sys_available_fn() -> float:
        return total_capacity_mb - rss_fn()

    return rss_fn, sys_available_fn


def test_loaded_models_own_rss_does_not_self_trigger_a_false_step_down(tmp_path):
    """Real bug found live running scripts/run_demo.sh: constructing each
    turn's BudgetManager from bare sys_available_mb() double-counts the
    already-loaded model's own RSS against itself, since "available RAM"
    already excludes it -- server_rss_mb() > ram_limit_mb was then true
    almost immediately for any model bigger than the smallest tier,
    causing an instant, narrated step-down on turn 1 even with total
    system capacity comfortably covering the chosen model and no
    external pressure at all. Reproduced here with total system capacity
    fixed at 12000MB and "large" (RSS 9965MB, leaving only ~2035MB
    "available") -- which would have been a false VIOLATION under the
    old ram_limit_mb=sys_available_fn() alone."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    runtime = FakeRuntime([_direct_answer("the function looks fine")])
    rss_fn, sys_available_fn = _coupled_resource_fns(runtime, total_capacity_mb=12000.0)

    session = ChatSession(
        runtime=runtime,
        models_config=FAKE_MODELS_CONFIG,
        repo_dir=str(repo),
        rss_fn=rss_fn,
        sys_available_fn=sys_available_fn,
    )
    session.repo_ctx.test_command = [sys.executable, "-m", "pytest"]
    session.start()
    assert session.current_model_id == "large"

    reply = session.handle_message("what does add() do?")

    assert reply == "the function looks fine"
    assert session.current_model_id == "large"  # did NOT self-inflict a step-down
    assert session.last_decision is None  # no RELOAD/STOP decision was ever triggered


def test_squeeze_triggers_narrated_step_down_and_conversation_continues(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    runtime = FakeRuntime([_direct_answer("The function looks fine to me.")])

    session = ChatSession(
        runtime=runtime,
        models_config=FAKE_MODELS_CONFIG,
        repo_dir=str(repo),
        rss_fn=_rss_tracking_fn(runtime),
        sys_available_fn=lambda: 15000.0,  # comfortably fits "large" at session start
    )
    session.repo_ctx.test_command = [sys.executable, "-m", "pytest"]
    session.start()
    assert session.current_model_id == "large"

    # Cut the budget to below large's RSS (9965MB) but above medium's
    # (7458.5MB) -- forces exactly one step-down, not a cascade to "small".
    session.schedule_squeeze(new_ram_limit_mb=8500.0, reason="demo squeeze", after_turns=0)

    reply = session.handle_message("what does add() do?")

    assert reply == "The function looks fine to me."  # conversation kept working
    assert session.current_model_id == "medium"  # actually stepped down
    assert session.last_decision is not None
    assert session.last_decision.action == Action.SWITCH_SMALLER_MODEL


def test_squeeze_with_no_smaller_model_stops_safely_without_crashing(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    runtime = FakeRuntime([_direct_answer("second turn works fine")])

    session = ChatSession(
        runtime=runtime,
        models_config=FAKE_MODELS_CONFIG,
        repo_dir=str(repo),
        rss_fn=_rss_tracking_fn(runtime),
        sys_available_fn=lambda: 3000.0,  # only fits "small" at session start
    )
    session.repo_ctx.test_command = [sys.executable, "-m", "pytest"]
    session.start()
    assert session.current_model_id == "small"

    session.schedule_squeeze(new_ram_limit_mb=10.0, reason="extreme squeeze", after_turns=0)

    reply = session.handle_message("what does add() do?")

    assert "not continuing" in reply.lower()
    assert session.last_decision.action == Action.STOP_SAFELY

    # the session itself isn't left broken -- the next turn (squeeze already
    # fired once, won't re-fire) works normally again
    reply2 = session.handle_message("ok, try again")
    assert reply2 == "second turn works fine"


def test_banner_is_pure_ascii():
    """Regression guard, same reasoning as render.py's own: a Unicode
    character in a fixed startup string already crashed outright with
    UnicodeEncodeError on a legacy Windows console (cp1252), confirmed
    live earlier this session. The banner is printed on every launch, so
    it's exactly the kind of fixed decoration that must never regress."""
    assert _BANNER.isascii()
