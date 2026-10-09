import atexit
import socket
import subprocess
import time
from pathlib import Path

import httpx

from dwarv.runtime.base import GenParams, GenResult, RuntimeCrashed


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class LlamaCppRuntime:
    """Launches llama-server as a subprocess per DWARV_PLAN.md Step 2. One model
    resident at a time; load() for a different model/ctx stops and relaunches."""

    def __init__(
        self,
        llama_server_path: str | Path,
        model_paths: dict[str, str],
        extra_args: list[str] | None = None,
        ready_timeout_s: float = 120.0,
    ):
        self._llama_server_path = str(llama_server_path)
        self._model_paths = model_paths
        self._extra_args = extra_args or []
        self._ready_timeout_s = ready_timeout_s
        self._process: subprocess.Popen | None = None
        self._model_id: str | None = None
        self._ctx_size: int | None = None
        self._port: int | None = None
        self._client: httpx.Client | None = None
        atexit.register(self.unload)

    def load(self, model_id: str, ctx_size: int) -> float:
        if model_id not in self._model_paths:
            raise KeyError(f"unknown model_id {model_id!r}; known: {sorted(self._model_paths)}")
        self.unload()
        self._port = _free_port()
        args = [
            self._llama_server_path,
            "-m",
            self._model_paths[model_id],
            "-c",
            str(ctx_size),
            "--port",
            str(self._port),
            "--host",
            "127.0.0.1",
            *self._extra_args,
        ]
        start = time.monotonic()
        self._process = subprocess.Popen(
            args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
        )
        self._wait_until_ready()
        load_s = time.monotonic() - start
        self._model_id = model_id
        self._ctx_size = ctx_size
        self._client = httpx.Client(base_url=f"http://127.0.0.1:{self._port}", timeout=180.0)
        return load_s

    def _wait_until_ready(self) -> None:
        deadline = time.monotonic() + self._ready_timeout_s
        url = f"http://127.0.0.1:{self._port}/health"
        while time.monotonic() < deadline:
            if self._process.poll() is not None:
                tail = self._process.stdout.read()[-2000:] if self._process.stdout else ""
                raise RuntimeCrashed(
                    f"llama-server exited with code {self._process.returncode} "
                    f"before becoming ready. Last output:\n{tail}"
                )
            try:
                if httpx.get(url, timeout=1.0).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.2)
        self.unload()
        raise RuntimeCrashed(f"llama-server did not become ready within {self._ready_timeout_s}s")

    def unload(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
        if self._process is not None:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=5)
            self._process = None
        self._model_id = None
        self._ctx_size = None
        self._port = None

    def generate(self, messages: list[dict[str, str]], params: GenParams) -> GenResult:
        if self._client is None:
            raise RuntimeError("no model loaded -- call load() first")
        payload = {
            "messages": messages,
            "temperature": params.temperature,
            "top_p": params.top_p,
            "max_tokens": params.max_tokens,
        }
        if params.seed is not None:
            payload["seed"] = params.seed
        if params.stop:
            payload["stop"] = params.stop
        start = time.monotonic()
        resp = self._client.post("/v1/chat/completions", json=payload)
        resp.raise_for_status()
        data = resp.json()
        wall_s = time.monotonic() - start
        choice = data["choices"][0]
        usage = data.get("usage", {})
        return GenResult(
            text=choice["message"]["content"],
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            wall_s=wall_s,
            finish_reason=choice.get("finish_reason", "unknown"),
        )

    def pid(self) -> int | None:
        return self._process.pid if self._process is not None else None

    def current(self) -> tuple[str | None, int | None]:
        return self._model_id, self._ctx_size
