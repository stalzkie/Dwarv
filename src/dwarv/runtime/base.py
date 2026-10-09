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

    def generate(self, prompt: str, params: GenParams) -> GenResult: ...

    def pid(self) -> int | None: ...  # server pid for RSS reads

    def current(self) -> tuple[str | None, int | None]: ...  # (model_id, ctx_size)
