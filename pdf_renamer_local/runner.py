"""Parser process isolation with bounded I/O and cleanup. This is not an OS sandbox."""

from __future__ import annotations

import json
import math
import os
import queue
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

from .config import Settings

_BOOTSTRAP = (
    "import sys; sys.path.insert(0, sys.argv[1]); "
    "from pdf_renamer_local.worker import main; raise SystemExit(main(sys.argv[2:]))"
)
_PACKAGE_PARENT = str(Path(__file__).resolve().parent.parent)
MAX_REPLY_BYTES = 4096
_CHILD_ENV_ALLOWLIST = frozenset({"SYSTEMROOT", "WINDIR", "TEMP", "TMP", "TMPDIR", "LANG", "LC_ALL", "TZ"})


def child_environment() -> dict[str, str]:
    return {name: value for name, value in os.environ.items() if name.upper() in _CHILD_ENV_ALLOWLIST}


def worker_command(options: dict) -> list[str]:
    return [sys.executable, "-I", "-c", _BOOTSTRAP, _PACKAGE_PARENT, json.dumps(options)]


class _Worker:
    def __init__(self, command: list[str]) -> None:
        self.proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            env=child_environment(),
            bufsize=0,
        )
        self.replies: queue.Queue[bytes | None] = queue.Queue(maxsize=1)
        self._protocol_failed = threading.Event()
        self._writer: threading.Thread | None = None
        self._reader = threading.Thread(target=self._pump, name="pdf-parser-reader", daemon=True)
        self._reader.start()

    def _stop_process(self) -> None:
        if self.proc.poll() is None:
            try:
                self.proc.kill()
            except OSError:
                pass

    def _offer_end(self) -> None:
        try:
            self.replies.put_nowait(None)
        except queue.Full:
            pass

    def _pump(self) -> None:
        try:
            while True:
                line = self.proc.stdout.readline(MAX_REPLY_BYTES + 1)
                if not line:
                    break
                if len(line) > MAX_REPLY_BYTES or not line.endswith(b"\n"):
                    self._protocol_failed.set()
                    self._stop_process()
                    break
                try:
                    self.replies.put_nowait(line)
                except queue.Full:
                    self._protocol_failed.set()
                    self._stop_process()
                    break
        except (OSError, ValueError):
            pass
        finally:
            self._offer_end()

    def _abort(self) -> None:
        self._stop_process()
        self.proc.wait(timeout=2)
        if self._writer is not None:
            self._writer.join(timeout=2)
        self._reader.join(timeout=2)
        if (self._writer is not None and self._writer.is_alive()) or self._reader.is_alive():
            raise RuntimeError("Parser pipe thread did not stop after terminating its process")

    def ask(self, data: bytes, timeout: float) -> dict | None:
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("Timeout must be positive and finite")
        deadline = time.monotonic() + timeout
        finished = threading.Event()
        write_failed = threading.Event()

        def write_request() -> None:
            try:
                for block in (struct.pack(">I", len(data)), data):
                    remaining = memoryview(block)
                    while remaining:
                        written = self.proc.stdin.write(remaining)
                        if not written:
                            raise BrokenPipeError("Parser input closed")
                        remaining = remaining[written:]
                self.proc.stdin.flush()
            except (OSError, ValueError):
                write_failed.set()
            finally:
                finished.set()

        self._writer = threading.Thread(target=write_request, name="pdf-parser-writer", daemon=True)
        self._writer.start()
        if not finished.wait(max(0, deadline - time.monotonic())):
            self._abort()
            return {"status": "error", "code": "timeout", "_kill": True}
        self._writer.join()
        if write_failed.is_set() or self._protocol_failed.is_set():
            self._abort()
            return None
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            self._abort()
            return {"status": "error", "code": "timeout", "_kill": True}
        try:
            line = self.replies.get(timeout=remaining)
        except queue.Empty:
            self._abort()
            return {"status": "error", "code": "timeout", "_kill": True}
        if line is None or self._protocol_failed.is_set():
            self._abort()
            return None
        try:
            reply = json.loads(line)
        except (ValueError, UnicodeError, RecursionError, OverflowError):
            self._abort()
            return None
        if not isinstance(reply, dict) or not isinstance(reply.get("status"), str) or reply["status"] not in {"ok", "error"}:
            self._abort()
            return None
        if any(name in reply and (not isinstance(reply[name], str) or len(reply[name]) > limit)
               for name, limit in (("code", 64), ("value", 100))):
            self._abort()
            return None
        return reply

    def close(self, force: bool = False) -> None:
        try:
            if force or (self._writer is not None and self._writer.is_alive()):
                self._abort()
            else:
                self.proc.stdin.close()
                try:
                    self.proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self._abort()
        finally:
            if self.proc.poll() is None:
                self._abort()
            for thread in (self._writer, self._reader):
                if thread is not None:
                    thread.join(timeout=2)
            for stream in (self.proc.stdin, self.proc.stdout):
                try:
                    stream.close()
                except OSError:
                    pass


def run_batch(
    documents: list[bytes],
    mode: str,
    settings: Settings,
    command: list[str] | None = None,
) -> list[dict]:
    options = {
        "mode": mode,
        "max_pages": settings.max_pages,
        "max_text_chars": settings.max_text_chars,
        "memory_mb": settings.worker_memory_mb,
    }
    command = command or worker_command(options)
    deadline = time.monotonic() + settings.request_timeout
    results: list[dict] = []
    worker: _Worker | None = None
    try:
        for data in documents:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                results.append({"status": "error", "code": "budget"})
                continue
            if worker is None:
                worker = _Worker(command)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                worker.close(force=True)
                worker = None
                results.append({"status": "error", "code": "budget"})
                continue
            reply = worker.ask(data, min(settings.file_timeout, remaining))
            if reply is None or reply.pop("_kill", False):
                worker.close(force=True)
                worker = None
                reply = reply or {"status": "error", "code": "failed"}
            results.append(reply)
    finally:
        if worker is not None:
            worker.close()
    return results
