from dataclasses import dataclass
from typing import Protocol


@dataclass
class GenParams:
    temperature: float = 0.2
    top_p: float = 0.95
    max_tokens: int = 1024
    seed: int | None = None
    stop: list[str] | None = None


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
