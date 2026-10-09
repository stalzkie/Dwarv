import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

import psutil


@dataclass
class Sample:
    ts: float
    server_rss_mb: float
    sys_available_mb: float
    sys_total_mb: float
    swap_used_mb: float


class ResourceMonitor:
    """Background sampler for the llama-server process's RSS and system memory.
    `pid_fn` returns the current server pid (or None), so the monitor keeps
    working across model reloads without being re-created."""

    def __init__(
        self,
        pid_fn: Callable[[], int | None],
        interval_s: float = 0.5,
        buffer_size: int = 7200,
    ):
        self._pid_fn = pid_fn
        self._interval_s = interval_s
        self._buffer: deque[Sample] = deque(maxlen=buffer_size)
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=self._interval_s * 2)
            self._thread = None

    def _run(self) -> None:
        while not self._stop_event.is_set():
            sample = self.sample_once()
            with self._lock:
                self._buffer.append(sample)
            self._stop_event.wait(self._interval_s)

    def sample_once(self) -> Sample:
        vm = psutil.virtual_memory()
        swap = psutil.swap_memory()
        return Sample(
            ts=time.time(),
            server_rss_mb=self._server_rss_mb(),
            sys_available_mb=vm.available / (1024 * 1024),
            sys_total_mb=vm.total / (1024 * 1024),
            swap_used_mb=swap.used / (1024 * 1024),
        )

    def _server_rss_mb(self) -> float:
        pid = self._pid_fn()
        if pid is None:
            return 0.0
        try:
            proc = psutil.Process(pid)
            total = proc.memory_info().rss
            for child in proc.children(recursive=True):
                try:
                    total += child.memory_info().rss
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return 0.0
        return total / (1024 * 1024)

    def latest(self) -> Sample | None:
        with self._lock:
            return self._buffer[-1] if self._buffer else None

    def peak_rss_mb(self) -> float:
        with self._lock:
            if not self._buffer:
                return 0.0
            return max(s.server_rss_mb for s in self._buffer)
