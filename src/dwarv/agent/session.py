from typing import Protocol

import psutil

from dwarv.agent.prompts import extract_patch, repair_prompt, system_prompt
from dwarv.controller.actions import Action
from dwarv.controller.policy import RELOAD_ACTIONS, decide
from dwarv.models.suite import (
    DEFAULT_CTX_SIZE,
    MODEL_ORDER,
    Hardware,
    choose_model,
    load_models_config,
)
from dwarv.models.suite import resolve_model_paths as _resolve_model_paths
from dwarv.repo.context import detect_repo_context, snapshot_repo_files
from dwarv.repo.patch import Patch, apply_patch, make_patch
from dwarv.repo.worktree import disposable_worktree
from dwarv.resources.budget import BudgetManager
from dwarv.resources.monitor import ResourceMonitor
from dwarv.runtime.base import GenParams
from dwarv.runtime.llamacpp import LlamaCppRuntime
from dwarv.types import Decision
from dwarv.types import State as PolicyState
from dwarv.verify.failure import FailureClass, classify
from dwarv.verify.sandbox import run as sandbox_run
from dwarv.verify.sandbox import sandbox_tier

DEFAULT_TURN_TIME_LIMIT_S = 120.0
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_MAX_RELOADS_PER_TURN = 2
DEFAULT_MAX_TOKENS = 1024


class RuntimeLike(Protocol):
    def load(self, model_id: str, ctx_size: int) -> float: ...
    def unload(self) -> None: ...
    def generate(self, messages: list[dict[str, str]], params: GenParams): ...
    def pid(self) -> int | None: ...
    def current(self) -> tuple[str | None, int | None]: ...


class ChatSession:
    """The Step 6 conversational loop. Pass `runtime`/`models_config` to
    inject a fake runtime for tests; real usage passes `llama_server_path`/
    `cache_dir` and lets it build a real LlamaCppRuntime."""

    def __init__(
        self,
        llama_server_path: str | None = None,
        cache_dir: str | None = None,
        repo_dir: str = ".",
        print_fn=print,
        runtime: RuntimeLike | None = None,
        models_config: dict | None = None,
    ):
        self.repo_ctx = detect_repo_context(repo_dir)
        self.models_config = models_config if models_config is not None else load_models_config()
        if runtime is not None:
            self.runtime = runtime
        else:
            model_paths = _resolve_model_paths(self.models_config, cache_dir)
            self.runtime = LlamaCppRuntime(llama_server_path, model_paths)
        self.monitor = ResourceMonitor(pid_fn=self.runtime.pid)
        self.tier, self.tier_detail = sandbox_tier()
        self.history: list[dict[str, str]] = []
        self.current_model_id: str | None = None
        self.current_ctx_size: int = DEFAULT_CTX_SIZE
        self.last_decision: Decision | None = None
        self.print_fn = print_fn
        self._started = False

    def start(self) -> None:
        self.monitor.start()
        hw = Hardware(sys_available_mb=psutil.virtual_memory().available / (1024**2))
        choice = choose_model(
            hw, models=self.models_config.get("models", []), ctx_size=DEFAULT_CTX_SIZE
        )
        self.runtime.load(choice.model_id, ctx_size=DEFAULT_CTX_SIZE)
        self.current_model_id = choice.model_id
        self.current_ctx_size = DEFAULT_CTX_SIZE
        self.history.append(
            {
                "role": "system",
                "content": system_prompt(str(self.repo_ctx.root), self.repo_ctx.test_command),
            }
        )
        snapshot = snapshot_repo_files(self.repo_ctx.root)
        if snapshot:
            self.history.append(
                {"role": "system", "content": f"Current repo file contents:\n\n{snapshot}"}
            )
        self.print_fn(choice.explanation)
        self.print_fn(f"Sandbox tier {self.tier}: {self.tier_detail}")
        self._started = True

    def stop(self) -> None:
        self.monitor.stop()
        self.runtime.unload()

    def status_text(self) -> str:
        sample = self.monitor.sample_once()
        decision_text = (
            self.last_decision.narration if self.last_decision else "no decisions made yet"
        )
        return (
            f"model: {self.current_model_id} (ctx {self.current_ctx_size})\n"
            f"sandbox tier: {self.tier} ({self.tier_detail})\n"
            f"repo: {self.repo_ctx.root} (git: {self.repo_ctx.is_git_repo}, "
            f"test command: {self.repo_ctx.test_command or 'none discovered'})\n"
            f"server RSS: {sample.server_rss_mb:.0f}MB, system available: {sample.sys_available_mb:.0f}MB\n"
            f"last decision: {decision_text}"
        )

    def handle_message(self, user_text: str) -> str:
        if not self._started:
            raise RuntimeError("call start() first")
        if user_text.strip() == "/status":
            return self.status_text()
        self.history.append({"role": "user", "content": user_text})
        return self._turn()

    def _turn(self) -> str:
        budget = BudgetManager(
            ram_limit_mb=psutil.virtual_memory().available / (1024**2),
            time_limit_s=DEFAULT_TURN_TIME_LIMIT_S,
            max_attempts=DEFAULT_MAX_ATTEMPTS,
            rss_fn=lambda: self.monitor.sample_once().server_rss_mb,
            sys_available_fn=lambda: self.monitor.sample_once().sys_available_mb,
        )

        temperature = 0.2
        last_failure_class: str | None = None
        repeated_same_failure = 0
        reloads_used = 0

        while True:
            budget.record_attempt()
            result = self.runtime.generate(
                self.history, GenParams(temperature=temperature, max_tokens=DEFAULT_MAX_TOKENS)
            )
            blocks = extract_patch(result.text)

            if not blocks:
                self.history.append({"role": "assistant", "content": result.text})
                return result.text

            patches = [make_patch(self.repo_ctx.root, path, content) for path, content in blocks]
            diff_display = "\n".join(p.diff_text for p in patches)

            if self.repo_ctx.test_command is None:
                self._apply_for_real(patches)
                self.history.append({"role": "assistant", "content": result.text})
                return f"{diff_display}\n\nApplied -- UNVERIFIED (no test command discovered for this repo)."

            classified = self._verify_in_worktree(patches, self.repo_ctx.test_command)

            if classified.failure_class == FailureClass.PASS:
                self._apply_for_real(patches)
                self.history.append({"role": "assistant", "content": result.text})
                return f"{diff_display}\n\nApplied -- verified (tests pass)."

            repeated_same_failure = (
                repeated_same_failure + 1 if classified.failure_class == last_failure_class else 0
            )
            last_failure_class = classified.failure_class

            decision = decide(
                self._policy_state(budget, classified.failure_class, repeated_same_failure),
                reloads_used=reloads_used,
                max_reloads_per_turn=DEFAULT_MAX_RELOADS_PER_TURN,
            )
            self.last_decision = decision
            self.print_fn(decision.narration)

            if decision.action == Action.STOP_SAFELY:
                self.history.append({"role": "assistant", "content": result.text})
                return (
                    f"{diff_display}\n\nNOT applied -- {decision.narration} "
                    f"(last failure: {classified.failure_class}: {classified.feedback})"
                )

            if decision.action in RELOAD_ACTIONS:
                reloads_used += 1
                target_model_id, target_ctx = self._resolve_reload_target(decision.action)
                self.runtime.load(target_model_id, ctx_size=target_ctx)
                self.current_model_id = target_model_id
                self.current_ctx_size = target_ctx
            elif decision.action == Action.RETRY_LOWER_TEMP:
                temperature = max(0.0, temperature - 0.2)
            elif decision.action == Action.RETRY_HIGHER_TEMP:
                temperature = min(1.0, temperature + 0.3)

            self.history.append(
                {"role": "user", "content": repair_prompt(result.text, classified.feedback)}
            )

    def _verify_in_worktree(self, patches: list[Patch], test_command: list[str]):
        with disposable_worktree(self.repo_ctx.root, self.repo_ctx.is_git_repo) as wt:
            for p in patches:
                apply_patch(p, wt)
            sandbox_result = sandbox_run(test_command, cwd=wt, timeout_s=30.0)
        passed = sandbox_result.returncode == 0 and not sandbox_result.timed_out
        return classify(
            code="\n".join(p.new_content for p in patches),
            passed=passed,
            timed_out=sandbox_result.timed_out,
            returncode=sandbox_result.returncode or 0,
            stdout=sandbox_result.stdout,
            stderr=sandbox_result.stderr,
        )

    def _apply_for_real(self, patches: list[Patch]) -> None:
        for p in patches:
            apply_patch(p, self.repo_ctx.root)

    def _resolve_reload_target(self, action: Action) -> tuple[str, int]:
        idx = MODEL_ORDER.index(self.current_model_id)
        if action == Action.SWITCH_SMALLER_MODEL and idx > 0:
            return MODEL_ORDER[idx - 1], self.current_ctx_size
        if action == Action.SWITCH_LARGER_MODEL and idx + 1 < len(MODEL_ORDER):
            return MODEL_ORDER[idx + 1], self.current_ctx_size
        if action == Action.SHRINK_CONTEXT:
            return self.current_model_id, max(1024, self.current_ctx_size // 2)
        return self.current_model_id, self.current_ctx_size

    def _policy_state(
        self, budget: BudgetManager, last_failure: str, repeated_same_failure: int
    ) -> PolicyState:
        return PolicyState(
            attempt_idx=budget.max_attempts - budget.attempts_left(),
            attempts_left=budget.attempts_left(),
            time_left_s=budget.remaining_time_s(),
            ram_limit_mb=budget.ram_limit_mb,
            server_rss_mb=budget.server_rss_mb(),
            headroom_mb=budget.headroom_mb(),
            sys_available_mb=budget.sys_available_mb(),
            model_id=self.current_model_id,
            ctx_size=self.current_ctx_size,
            last_failure=last_failure,
            repeated_same_failure=repeated_same_failure,
            model_load_cost_s=self._load_cost_table(),
            est_rss_mb=self._rss_table(),
        )

    def _load_cost_table(self) -> dict[str, float]:
        return {
            m["id"]: (m.get("load_time_s") or {}).get(self.current_ctx_size, 0.0)
            for m in self.models_config.get("models", [])
        }

    def _rss_table(self) -> dict[tuple[str, int], float]:
        table: dict[tuple[str, int], float] = {}
        for m in self.models_config.get("models", []):
            for ctx, rss in (m.get("measured_rss_mb") or {}).items():
                table[(m["id"], ctx)] = rss
        return table


def run_repl(llama_server_path: str, cache_dir: str, repo_dir: str = ".") -> None:
    from rich.console import Console

    console = Console()
    session = ChatSession(llama_server_path, cache_dir, repo_dir, print_fn=console.print)
    session.start()
    try:
        while True:
            try:
                user_text = console.input("[bold cyan]you>[/bold cyan] ")
            except (EOFError, KeyboardInterrupt):
                break
            stripped = user_text.strip()
            if not stripped:
                continue
            if stripped in ("/exit", "/quit"):
                break
            console.print(session.handle_message(user_text))
    finally:
        session.stop()
