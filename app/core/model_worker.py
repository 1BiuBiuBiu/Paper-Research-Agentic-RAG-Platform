"""Reusable subprocess lifecycle for local model workers.

Workers receive one JSON command per stdin line and acknowledge completion by atomically
writing a small response file.  The file response avoids treating third-party stdout as a
protocol while the parent retains hard timeout and process-isolation guarantees.
"""

import json
import os
import subprocess
import sys
import threading
import time
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any, Protocol


class WorkerStopped(RuntimeError):
    pass


class WorkerTimedOut(TimeoutError):
    pass


class WorkerClient(Protocol):
    def request(self, payload: dict[str, Any], response_path: Path, timeout: float) -> dict: ...

    def close(self) -> None: ...


def isolated_environment(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return an environment without provider credentials for a local model worker."""
    result = {
        key: value
        for key, value in os.environ.items()
        if not any(word in key.upper() for word in ("API_KEY", "MODEL_KEY", "SECRET", "TOKEN"))
    }
    if extra:
        result.update(extra)
    return result


def commands() -> Iterator[dict[str, Any]]:
    """Yield valid commands until the parent closes stdin."""
    for line in sys.stdin:
        try:
            value = json.loads(line)
        except (TypeError, ValueError):
            continue
        if isinstance(value, dict):
            yield value


def respond(path: Path, body: dict[str, Any]) -> None:
    """Atomically publish a worker result so the parent never reads partial JSON."""
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


class PersistentWorker:
    """Keep one isolated worker alive and terminate it after an idle interval."""

    def __init__(
        self,
        module: str,
        idle_seconds: float,
        *,
        environment: Mapping[str, str] | None = None,
        command: list[str] | None = None,
    ) -> None:
        self.module = module
        self.idle_seconds = max(0.0, idle_seconds)
        self.environment = dict(environment or isolated_environment())
        self.command = command or [sys.executable, "-u", "-m", module]
        self.root = Path(__file__).resolve().parents[2]
        self._lock = threading.Lock()
        self._process: subprocess.Popen[str] | None = None
        self._timer: threading.Timer | None = None
        self._generation = 0

    @property
    def pid(self) -> int | None:
        with self._lock:
            process = self._process
            return process.pid if process is not None and process.poll() is None else None

    def request(self, payload: dict[str, Any], response_path: Path, timeout: float) -> dict:
        with self._lock:
            self._cancel_timer()
            response_path.unlink(missing_ok=True)
            process = self._ensure_started()
            command = {**payload, "response_path": str(response_path)}
            try:
                if process.stdin is None:
                    raise BrokenPipeError
                process.stdin.write(json.dumps(command, ensure_ascii=False) + "\n")
                process.stdin.flush()
            except (BrokenPipeError, OSError):
                self._stop_locked()
                raise WorkerStopped(f"{self.module} stopped before accepting work") from None

            deadline = time.monotonic() + timeout
            try:
                while time.monotonic() < deadline:
                    if response_path.is_file():
                        try:
                            value = json.loads(response_path.read_text(encoding="utf-8"))
                        except (OSError, ValueError):
                            self._stop_locked()
                            raise WorkerStopped(
                                f"{self.module} returned an invalid result"
                            ) from None
                        if not isinstance(value, dict):
                            self._stop_locked()
                            raise WorkerStopped(f"{self.module} returned an invalid result")
                        return value
                    if process.poll() is not None:
                        self._stop_locked()
                        raise WorkerStopped(f"{self.module} exited while processing")
                    time.sleep(0.05)
                self._stop_locked()
                raise WorkerTimedOut(f"{self.module} exceeded {timeout} seconds")
            finally:
                response_path.unlink(missing_ok=True)
                if self._process is not None:
                    self._schedule_expiry()

    def close(self) -> None:
        with self._lock:
            self._stop_locked()

    def _ensure_started(self) -> subprocess.Popen[str]:
        process = self._process
        if process is not None and process.poll() is None:
            return process
        self._stop_locked()
        self._generation += 1
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        self._process = subprocess.Popen(
            self.command,
            cwd=self.root,
            env=self.environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            bufsize=1,
            creationflags=creationflags,
        )
        return self._process

    def _schedule_expiry(self) -> None:
        self._cancel_timer()
        generation = self._generation
        timer = threading.Timer(self.idle_seconds, self._expire, args=(generation,))
        timer.daemon = True
        self._timer = timer
        timer.start()

    def _expire(self, generation: int) -> None:
        with self._lock:
            if generation == self._generation:
                self._stop_locked()

    def _cancel_timer(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def _stop_locked(self) -> None:
        self._cancel_timer()
        process, self._process = self._process, None
        if process is None:
            return
        if process.stdin is not None:
            try:
                process.stdin.close()
            except OSError:
                pass
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
