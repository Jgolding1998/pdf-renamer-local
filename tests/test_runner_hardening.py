import importlib.util
import json
import os
import struct
import sys
import time
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
from pdf_renamer_local.config import Settings
from pdf_renamer_local import runner

READ_REQUEST = "import sys,struct,json,os; header=sys.stdin.buffer.read(4); size=struct.unpack('>I',header)[0]; data=sys.stdin.buffer.read(size); "


def command(script):
    return [sys.executable, "-I", "-c", script]


def assert_clean(worker):
    assert worker.proc.poll() is not None
    assert not worker._reader.is_alive()
    assert worker._writer is None or not worker._writer.is_alive()


def test_nonreading_worker_large_payload_obeys_deadline():
    worker = runner._Worker(command("import time; time.sleep(60)"))
    started = time.monotonic()
    try:
        reply = worker.ask(b"x" * (2 * 1024 * 1024), 0.2)
        assert reply == {"status": "error", "code": "timeout", "_kill": True}
        assert time.monotonic() - started < 3
        assert_clean(worker)
    finally:
        worker.close(force=True)
    assert_clean(worker)


def test_batch_recovers_after_large_payload_timeout():
    started = time.monotonic()
    replies = runner.run_batch(
        [b"x" * (2 * 1024 * 1024), b"y" * (2 * 1024 * 1024)],
        "customer", Settings(file_timeout=0.2), command("import time; time.sleep(60)"),
    )
    assert replies == [{"status": "error", "code": "timeout"}, {"status": "error", "code": "timeout"}]
    assert time.monotonic() - started < 4


def test_worker_environment_excludes_parent_sentinel(monkeypatch):
    monkeypatch.setenv("PRIVATE_REVIEW_MARKER", "fixture-only")
    script = READ_REQUEST + "print(json.dumps({'status':'ok','sentinel_seen':'PRIVATE_REVIEW_MARKER' in os.environ}), flush=True)"
    worker = runner._Worker(command(script))
    try:
        assert worker.ask(b"fixture", 3) == {"status": "ok", "sentinel_seen": False}
    finally:
        worker.close()
    assert_clean(worker)


def test_environment_is_explicit_allowlist(monkeypatch):
    monkeypatch.setenv("PRIVATE_REVIEW_MARKER", "fixture-only")
    environment = runner.child_environment()
    assert "PRIVATE_REVIEW_MARKER" not in environment
    assert set(name.upper() for name in environment) <= runner._CHILD_ENV_ALLOWLIST
    if os.name == "nt":
        assert any(name.upper() == "SYSTEMROOT" for name in environment)


@pytest.mark.parametrize("reply", ["not json", "[]", '"text"', '{"status":"unexpected"}', '{"status":[]}', '{"status":"error","code":[]}', '{"status":"ok","value":{}}', '['*1500 + '0' + ']'*1500])
def test_malformed_reply_is_rejected_and_process_stopped(reply):
    worker = runner._Worker(command(READ_REQUEST + "print(" + repr(reply) + ", flush=True)"))
    try:
        assert worker.ask(b"fixture", 3) is None
        assert_clean(worker)
    finally:
        worker.close(force=True)


def test_unbounded_stdout_is_capped_and_process_stopped():
    worker = runner._Worker(command(READ_REQUEST + "sys.stdout.write('x'*1000000); sys.stdout.flush()"))
    try:
        assert worker.ask(b"fixture", 3) is None
        assert worker._protocol_failed.is_set()
        assert worker.replies.maxsize == 1
        assert_clean(worker)
    finally:
        worker.close(force=True)


def test_nonterminated_reply_is_rejected():
    worker = runner._Worker(command(READ_REQUEST + "sys.stdout.write('{\"status\":\"ok\"}'); sys.stdout.flush()"))
    try:
        assert worker.ask(b"fixture", 3) is None
    finally:
        worker.close(force=True)
    assert_clean(worker)


def test_worker_crash_restarts_for_next_document():
    script = READ_REQUEST + "os._exit(3) if data == b'crash' else print(json.dumps({'status':'ok','value':'RECOVERED1'}), flush=True)"
    results = runner.run_batch([b"crash", b"good"], "customer", Settings(), command(script))
    assert results == [{"status": "error", "code": "failed"}, {"status": "ok", "value": "RECOVERED1"}]


def test_full_request_budget_includes_blocked_write():
    started = time.monotonic()
    results = runner.run_batch(
        [b"x" * (2 * 1024 * 1024), b"second"], "customer",
        Settings(file_timeout=5, request_timeout=0.2), command("import time; time.sleep(60)"),
    )
    assert results == [{"status": "error", "code": "timeout"}, {"status": "error", "code": "budget"}]
    assert time.monotonic() - started < 3


def test_real_parser_runs_with_filtered_environment():
    factory_spec = importlib.util.spec_from_file_location("review_pdf_factory", APP_ROOT / "tests" / "pdf_factory.py")
    factory = importlib.util.module_from_spec(factory_spec)
    factory_spec.loader.exec_module(factory)
    results = runner.run_batch([factory.invoice("A1"), factory.scanned(), factory.invoice("B2")], "customer", Settings())
    assert [result.get("value") or result["code"] for result in results] == ["A1", "no_text", "B2"]


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf")])
def test_invalid_timeout_rejected_without_leaking_child(timeout):
    worker = runner._Worker(command("import time; time.sleep(60)"))
    try:
        with pytest.raises(ValueError):
            worker.ask(b"fixture", timeout)
    finally:
        worker.close(force=True)
    assert_clean(worker)

