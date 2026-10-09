from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from dwarv.agent.prompts import (
    DWARV_RESPONSE_SCHEMA,
    MalformedStructuredResponse,
    parse_structured_response,
    salvage_message,
    structured_repair_prompt,
    structured_system_prompt,
)
from dwarv.controller.actions import Action
from dwarv.controller.policy import RELOAD_ACTIONS, decide
from dwarv.models.suite import (
    DEFAULT_CTX_SIZE,
    MODEL_ORDER,
    Hardware,
    choose_gpu_layers,
    choose_model,
    load_models_config,
    load_quant_choices,
)
from dwarv.models.suite import resolve_model_paths as _resolve_model_paths
from dwarv.repo.context import detect_repo_context
from dwarv.repo.graph import RepoGraph, build_graph, query_relevant_context
from dwarv.repo.patch import Patch, apply_patch, make_patch
from dwarv.repo.worktree import disposable_worktree
from dwarv.resources.budget import BudgetManager, BudgetStatus
from dwarv.resources.monitor import ResourceMonitor
from dwarv.resources.squeeze import SqueezeEvent, SqueezeScheduler
from dwarv.runtime.base import GenParams
from dwarv.runtime.llamacpp import LlamaCppRuntime
from dwarv.telemetry.logger import EventLogger, NullEventLogger
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
    def load(self, model_id: str, ctx_size: int, gpu_layers: int = 0) -> float: ...
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
        rss_fn: Callable[[], float] | None = None,
        sys_available_fn: Callable[[], float] | None = None,
        log_dir: str | Path | None = None,
        vram_mb: float | None = None,
    ):
        if log_dir is not None:
            self.logger = EventLogger(log_dir)
        elif cache_dir is not None:
            self.logger = EventLogger(Path(cache_dir) / "sessions")
        else:
            self.logger = NullEventLogger()
        self.repo_ctx = detect_repo_context(repo_dir)
        self.models_config = models_config if models_config is not None else load_models_config()
        # DWARV_PLAN.md section 11.6: which quant level setup-offline
        # actually downloaded per tier, if any tier isn't at its default --
        # makes both path resolution and RSS-based tier selection reflect
        # what's really on disk.
        self.quant_choices = load_quant_choices(cache_dir) if cache_dir is not None else {}
        # DWARV_PLAN.md section 11.3: real file size per model_id, used by
        # _gpu_layers_for() to decide GPU offload -- None when a fake
        # runtime is injected (tests), which also means vram_mb has no
        # effect there, matching today's CPU-only behavior exactly.
        self.model_paths: dict[str, str] | None = None
        self._vram_mb = vram_mb
        if runtime is not None:
            self.runtime = runtime
        else:
            self.model_paths = _resolve_model_paths(
                self.models_config, cache_dir, quant_choices=self.quant_choices
            )
            self.runtime = LlamaCppRuntime(llama_server_path, self.model_paths)
        self._monitor_sample_count = 0
        self.monitor = ResourceMonitor(pid_fn=self.runtime.pid, on_sample=self._on_monitor_sample)
        # Overridable so tests can simulate resource pressure deterministically
        # (a fake runtime has no real process whose RSS could realistically be
        # squeezed against GB-scale bundled-model numbers); real usage reads
        # the live monitor.
        self._rss_fn = rss_fn or (lambda: self.monitor.sample_once().server_rss_mb)
        self._sys_available_fn = sys_available_fn or (
            lambda: self.monitor.sample_once().sys_available_mb
        )
        self.tier, self.tier_detail = sandbox_tier()
        self.history: list[dict[str, str]] = []
        self.current_model_id: str | None = None
        self.current_ctx_size: int = DEFAULT_CTX_SIZE
        self.last_decision: Decision | None = None
        self.print_fn = print_fn
        self.repo_graph: RepoGraph | None = None  # built in start(), see DWARV_PLAN.md 11.7
        self.squeeze = SqueezeScheduler()
        self._turns_completed = 0
        self._started = False

    def start(self) -> None:
        self.logger.start()
        self.logger.log(
            "run_started",
            "run_started",
            repo_root=str(self.repo_ctx.root),
            is_git_repo=self.repo_ctx.is_git_repo,
            test_command=self.repo_ctx.test_command,
            sandbox_tier=self.tier,
            sandbox_tier_detail=self.tier_detail,
        )
        self.monitor.start()
        hw = Hardware(sys_available_mb=self._sys_available_fn())
        choice = choose_model(
            hw,
            models=self.models_config.get("models", []),
            ctx_size=DEFAULT_CTX_SIZE,
            quant_choices=self.quant_choices,
        )
        self._load_runtime(choice.model_id, ctx_size=DEFAULT_CTX_SIZE)
        self.current_model_id = choice.model_id
        self.current_ctx_size = DEFAULT_CTX_SIZE
        self.logger.log(
            "model_loaded",
            "model_loaded",
            model_id=choice.model_id,
            ctx_size=DEFAULT_CTX_SIZE,
            explanation=choice.explanation,
            sys_available_mb=hw.sys_available_mb,
        )
        self.history.append(
            {
                "role": "system",
                "content": structured_system_prompt(
                    str(self.repo_ctx.root), self.repo_ctx.test_command
                ),
            }
        )
        # DWARV_PLAN.md section 11.7: build the repo's code graph once at
        # start (cheap -- AST parsing, no LLM calls) rather than dumping
        # the whole repo into history as a standing system message. Each
        # turn queries this graph for only what that turn's question
        # actually needs -- see _turn()'s per-call context, which is
        # therefore not a permanent, ever-growing part of self.history.
        self.repo_graph = build_graph(self.repo_ctx.root)
        self.print_fn(choice.explanation)
        self.print_fn(f"Sandbox tier {self.tier}: {self.tier_detail}")
        self._started = True

    def stop(self) -> None:
        self.monitor.stop()
        self.runtime.unload()
        self.logger.log("model_unloaded", "model_unloaded", model_id=self.current_model_id)
        self.logger.log("run_finished", "run_finished", turns_completed=self._turns_completed)
        self.logger.stop()

    def schedule_squeeze(
        self,
        new_ram_limit_mb: float,
        reason: str = "memory squeeze",
        after_turns: int | None = None,
        after_s: float | None = None,
    ) -> SqueezeEvent:
        """Step 8: pre-register a deterministic RAM-budget cut, for the live
        demo ("watch what happens when I squeeze available RAM mid-
        conversation") or the Step 9 internal eval's ablations."""
        return self.squeeze.schedule(
            new_ram_limit_mb, reason=reason, after_turns=after_turns, after_s=after_s
        )

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
        return self._turn(user_text)

    def _on_monitor_sample(self, sample) -> None:
        # Downsampled -- ResourceMonitor's default interval is 0.5s, which
        # would otherwise flood the JSONL log; every 4th sample is ~2s
        # resolution, plenty for the Step 10A live resource chart.
        self._monitor_sample_count += 1
        if self._monitor_sample_count % 4 != 0:
            return
        self.logger.log(
            "monitor_sample",
            "monitor_sample",
            server_rss_mb=sample.server_rss_mb,
            sys_available_mb=sample.sys_available_mb,
            sys_total_mb=sample.sys_total_mb,
            swap_used_mb=sample.swap_used_mb,
        )

    def _messages_with_relevant_context(self, user_text: str) -> list[dict[str, str]]:
        """DWARV_PLAN.md section 11.7: queries the repo graph for only
        what `user_text` actually needs and appends it as one extra
        message for *this* generate() call -- deliberately not written
        into self.history, so a long conversation doesn't accumulate an
        ever-growing stack of past turns' context blocks. Re-queried fresh
        every attempt within a turn (including repair retries) using the
        turn's original question, not the repair-prompt text, since the
        underlying task hasn't changed."""
        if self.repo_graph is None:
            return self.history
        context = query_relevant_context(self.repo_graph, user_text)
        if not context:
            return self.history
        return [
            *self.history,
            {"role": "system", "content": f"Relevant code for this question:\n\n{context}"},
        ]

    def _log_decision(self, decision: Decision) -> None:
        self.logger.log(
            "decision",
            "decision",
            action=decision.action.name,
            reason=decision.reason,
            narration=decision.narration,
            inputs_snapshot=decision.inputs_snapshot,
        )

    def _turn(self, user_text: str) -> str:
        turn_index = self._turns_completed
        self._turns_completed += 1
        self.logger.log("turn_started", "turn_started", turn_index=turn_index)

        # Real bug found live running the demo script: psutil's "available"
        # RAM already excludes whatever our own already-loaded model is
        # using, so using it bare as ram_limit_mb compared the model's own
        # RSS against a figure that had already subtracted that same RSS
        # -- server_rss_mb() > ram_limit_mb was true almost immediately
        # for any model bigger than the smallest tier, causing an instant
        # step-down on turn 1 regardless of how much RAM choose_model()
        # correctly judged was free. Adding the model's own current RSS
        # back in restores the real invariant: the budget is "how much RAM
        # Dwarv may use in total," not "how much is free on top of what
        # Dwarv is already using." Reduces to plain sys_available_mb()
        # before any model is loaded (rss_fn() is 0 then), matching
        # choose_model()'s own pre-load calculation exactly. Still reacts
        # correctly to real external pressure (another process eating RAM
        # shrinks sys_available_mb without the model's RSS changing) and
        # to a genuine /squeeze override (which sets ram_limit_mb
        # directly, bypassing this entirely).
        budget = BudgetManager(
            ram_limit_mb=self._sys_available_fn() + self._rss_fn(),
            time_limit_s=DEFAULT_TURN_TIME_LIMIT_S,
            max_attempts=DEFAULT_MAX_ATTEMPTS,
            rss_fn=self._rss_fn,
            sys_available_fn=self._sys_available_fn,
        )

        temperature = 0.2
        last_failure_class: str | None = None
        repeated_same_failure = 0
        reloads_used = 0

        while True:
            squeeze_event = self.squeeze.maybe_fire(turn_index)
            if squeeze_event is not None:
                budget.set_ram_limit(squeeze_event.new_ram_limit_mb, squeeze_event.reason)
                self.logger.log(
                    "budget_change",
                    "budget_change",
                    reason=squeeze_event.reason,
                    new_ram_limit_mb=squeeze_event.new_ram_limit_mb,
                )
                self.print_fn(
                    f"(demo) squeeze: {squeeze_event.reason} -- RAM budget cut to "
                    f"{squeeze_event.new_ram_limit_mb:.0f}MB"
                )

            budget_status = budget.check()
            if budget_status != BudgetStatus.OK:
                # Proactive check, run before generating -- catches a squeeze
                # (or genuine resource pressure) even on a turn that would
                # otherwise succeed on the first try. Rule 2 (budget shrank)
                # only ever returns STOP_SAFELY/SHRINK_CONTEXT/
                # SWITCH_SMALLER_MODEL here, never a retry/escalate action.
                event = (
                    "budget_violation" if budget_status == BudgetStatus.VIOLATION else "budget_warn"
                )
                self.logger.log(
                    event,
                    event,
                    server_rss_mb=budget.server_rss_mb(),
                    ram_limit_mb=budget.ram_limit_mb,
                )
                decision = decide(
                    self._policy_state(budget, last_failure_class, repeated_same_failure),
                    reloads_used=reloads_used,
                    max_reloads_per_turn=DEFAULT_MAX_RELOADS_PER_TURN,
                )
                self.last_decision = decision
                self._log_decision(decision)
                self.print_fn(decision.narration)

                if decision.action == Action.STOP_SAFELY:
                    reply = f"NOT continuing -- {decision.narration}"
                    self.history.append({"role": "assistant", "content": f"[{reply}]"})
                    return reply

                if decision.action in RELOAD_ACTIONS:
                    reloads_used += 1
                    target_model_id, target_ctx = self._resolve_reload_target(decision.action)
                    self._load_runtime(target_model_id, ctx_size=target_ctx)
                    self.current_model_id = target_model_id
                    self.current_ctx_size = target_ctx
                continue  # re-check resources before generating

            budget.record_attempt()
            result = self.runtime.generate(
                self._messages_with_relevant_context(user_text),
                GenParams(
                    temperature=temperature,
                    max_tokens=DEFAULT_MAX_TOKENS,
                    json_schema=DWARV_RESPONSE_SCHEMA,
                ),
            )
            try:
                response = parse_structured_response(result.text)
            except MalformedStructuredResponse:
                # Schema-constrained generation should make this unreachable
                # in practice (see agent/prompts.py's own docstring); if it
                # ever happens anyway -- a crashed/killed server, a
                # max_tokens truncation mid-JSON (live-observed with the
                # small model, see GenParams.repeat_penalty's own comment),
                # a future llama.cpp regression -- degrade gracefully
                # rather than crashing the turn or showing a raw, unclosed
                # JSON blob as if it were the real answer.
                self.history.append({"role": "assistant", "content": result.text})
                return salvage_message(result.text) or result.text

            if response.kind == "direct_answer" or not response.files:
                self.history.append({"role": "assistant", "content": result.text})
                return response.message

            patches = [
                make_patch(self.repo_ctx.root, path, content) for path, content in response.files
            ]
            diff_display = "\n".join(p.diff_text for p in patches)
            self.logger.log(
                "patch_proposed",
                "patch_proposed",
                files=[str(p.file_path) for p in patches],
            )

            if self.repo_ctx.test_command is None:
                self._apply_for_real(patches)
                self.history.append({"role": "assistant", "content": result.text})
                return (
                    f"{response.message}\n\n{diff_display}\n\n"
                    "Applied -- UNVERIFIED (no test command discovered for this repo)."
                )

            classified = self._verify_in_worktree(patches, self.repo_ctx.test_command)
            self.logger.log(
                "verified",
                "verified",
                failure_class=classified.failure_class,
                feedback=classified.feedback[:2000],
            )

            if classified.failure_class == FailureClass.PASS:
                self._apply_for_real(patches)
                self.history.append({"role": "assistant", "content": result.text})
                return f"{response.message}\n\n{diff_display}\n\nApplied -- verified (tests pass)."

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
            self._log_decision(decision)
            self.print_fn(decision.narration)

            if decision.action == Action.STOP_SAFELY:
                self.history.append({"role": "assistant", "content": result.text})
                return (
                    f"{response.message}\n\n{diff_display}\n\nNOT applied -- {decision.narration} "
                    f"(last failure: {classified.failure_class}: {classified.feedback})"
                )

            if decision.action in RELOAD_ACTIONS:
                reloads_used += 1
                target_model_id, target_ctx = self._resolve_reload_target(decision.action)
                self._load_runtime(target_model_id, ctx_size=target_ctx)
                self.current_model_id = target_model_id
                self.current_ctx_size = target_ctx
            elif decision.action == Action.RETRY_LOWER_TEMP:
                temperature = max(0.0, temperature - 0.2)
            elif decision.action == Action.RETRY_HIGHER_TEMP:
                temperature = min(1.0, temperature + 0.3)

            self.history.append(
                {
                    "role": "user",
                    "content": structured_repair_prompt(result.text, classified.feedback),
                }
            )

    def _verify_in_worktree(self, patches: list[Patch], test_command: list[str]):
        with disposable_worktree(self.repo_ctx.root, self.repo_ctx.is_git_repo) as wt:
            for p in patches:
                apply_patch(p, wt)
            # skip_docker: the repo's own test command needs the host/user's
            # actual environment (installed deps, the right interpreter) --
            # Docker's generic image can't see those. See verify/sandbox.py.
            # extra_env PYTHONPATH: bare `pytest` (unlike `python -m pytest`)
            # does not add the invocation cwd to sys.path, so a flat layout
            # (code at root, tests/ with no __init__.py) fails to import the
            # module under test. See docs/DECISIONS.md.
            sandbox_result = sandbox_run(
                test_command,
                cwd=wt,
                timeout_s=30.0,
                skip_docker=True,
                extra_env={"PYTHONPATH": str(wt)},
            )
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

    def _gpu_layers_for(self, model_id: str) -> int:
        """DWARV_PLAN.md section 11.3: 0 (CPU-only, llama.cpp's own
        default) unless both a real GPU was detected (vram_mb set by
        run_repl) and the real on-disk file for model_id fits in it with
        margin -- see models.suite.choose_gpu_layers. Reads the file's
        real size, not the configured expected size, so a quant
        substitution (section 11.6) is reflected accurately."""
        if self._vram_mb is None or self.model_paths is None:
            return 0
        path = self.model_paths.get(model_id)
        if path is None:
            return 0
        try:
            file_size_bytes = Path(path).stat().st_size
        except OSError:
            return 0
        return choose_gpu_layers(file_size_bytes, self._vram_mb)

    def _load_runtime(self, model_id: str, ctx_size: int) -> None:
        """Thin wrapper around runtime.load() that only passes gpu_layers
        when nonzero, so a fake runtime in tests (whose load() never
        needed a gpu_layers param) keeps working unchanged."""
        gpu_layers = self._gpu_layers_for(model_id)
        if gpu_layers:
            self.runtime.load(model_id, ctx_size=ctx_size, gpu_layers=gpu_layers)
        else:
            self.runtime.load(model_id, ctx_size=ctx_size)

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


# Pure ASCII, deliberately -- generated via scripts/generate_banner.py from
# docs/assets/logo.png. A Unicode block character here would risk the exact
# class of bug render.py's own styling already hit and fixed: UnicodeEncodeError
# on a legacy Windows console (cp1252), which has no block-drawing characters.
_BANNER = """\
              ###
            ### ###    ##  #
           #### #### ########
        #############  ## ###
     #######  # #  ######  #
    ##########   #########
    ######################
     #### ###########  ##
         #####   ####  #

  DWARV -- a local, conversational coding assistant
"""

_HELP_TEXT = (
    "/status           current model, sandbox tier, repo info, live RSS, last decision\n"
    "/squeeze <MB>      demo/dev only: cut the RAM budget to <MB> on the next turn, to\n"
    "                   show the narrated step-down (DWARV_PLAN.md Step 8)\n"
    "/help              this message\n"
    "/exit, /quit       end the session"
)


def run_repl(
    llama_server_path: str, cache_dir: str, repo_dir: str = ".", vram_mb: float | None = None
) -> None:
    from rich.console import Console

    from dwarv.agent.render import style_narration, style_reply

    console = Console()
    # #3b9eff matches the lighter accent blue used in docs/assets/logo.svg
    # and the GUI panel's --accent token, so the banner reads as the same
    # brand color everywhere the logo appears.
    console.print(f"[#3b9eff]{_BANNER}[/#3b9eff]")
    # ChatSession's print_fn contract stays plain text (unchanged, still
    # trivially fake-able in tests) -- styling is applied here, at the
    # display boundary, not inside the session's own logic.
    session = ChatSession(
        llama_server_path,
        cache_dir,
        repo_dir,
        print_fn=lambda text: console.print(style_narration(text)),
        vram_mb=vram_mb,
    )
    session.start()
    try:
        while True:
            try:
                # Plain ">" -- not a fancier Unicode prompt character --
                # deliberately: matches Claude Code's own real prompt
                # convention, and a Unicode character here crashed outright
                # on a legacy Windows console (cp1252), confirmed live.
                user_text = console.input("[bold magenta]>[/bold magenta] ")
            except (EOFError, KeyboardInterrupt):
                break
            stripped = user_text.strip()
            if not stripped:
                continue
            if stripped in ("/exit", "/quit"):
                break
            if stripped == "/help":
                console.print(_HELP_TEXT)
                continue
            if stripped.startswith("/squeeze"):
                parts = stripped.split()
                if len(parts) != 2 or not parts[1].replace(".", "", 1).isdigit():
                    console.print("usage: /squeeze <new_ram_limit_mb>")
                    continue
                mb = float(parts[1])
                session.schedule_squeeze(mb, reason="manual /squeeze demo trigger", after_turns=0)
                console.print(
                    f"(demo) squeeze scheduled: RAM budget will cut to {mb:.0f}MB on the next turn."
                )
                continue
            with console.status("[cyan]Dwarv is thinking...[/cyan]", spinner="dots"):
                reply = session.handle_message(user_text)
            console.print(style_reply(reply))
    finally:
        session.stop()
