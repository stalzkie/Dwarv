from dataclasses import dataclass
from typing import Protocol


@dataclass
class GenParams:
    temperature: float = 0.2
    top_p: float = 0.95
    max_tokens: int = 1024
    seed: int | None = None
    stop: list[str] | None = None
    # DWARV_PLAN.md section 11.8: constrains generation to a JSON Schema via
    # llama-server's OpenAI-compatible response_format field (confirmed real
    # in the pinned build -- llama-server --help lists -j/--json-schema).
    # None means unconstrained free-form generation, today's behavior.
    json_schema: dict | None = None
    # Found live-running the demo script on the small model: an unpenalized
    # low-temperature generation degenerated into looping the same sentence
    # until it hit max_tokens, truncating the JSON mid-string (see
    # MalformedStructuredResponse's handling in agent/session.py).
    # llama.cpp's own long-established default (1.1) was confirmed live to
    # reduce but not eliminate this on the real multi-turn demo scenario
    # (squeeze-triggered step-down + accumulated history) -- 1.3 was then
    # confirmed live, on that exact same scenario (re-ran the full demo
    # script), to produce a short, coherent, non-repeating answer instead.
    # See docs/DECISIONS.md.
    repeat_penalty: float = 1.3


@dataclass
class GenResult:
    text: str
    prompt_tokens: int
    completion_tokens: int
    wall_s: float
    finish_reason: str


class RuntimeCrashed(Exception):
    pass


class RuntimeAdapter(Protocol):
    def load(self, model_id: str, ctx_size: int) -> float: ...  # returns load seconds

    def unload(self) -> None: ...

    # messages: OpenAI-style [{"role": "user"/"assistant"/"system", "content": str}, ...] --
    # multi-turn, needed for Step 6's conversation (evolved from a single-prompt signature
    # once that requirement was added; see docs/DECISIONS.md).
    def generate(self, messages: list[dict[str, str]], params: GenParams) -> GenResult: ...

    def pid(self) -> int | None: ...  # server pid for RSS reads

    def current(self) -> tuple[str | None, int | None]: ...  # (model_id, ctx_size)
