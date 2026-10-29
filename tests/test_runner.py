import sys
import time

from pdf_renamer_local.config import Settings
from pdf_renamer_local.runner import run_batch

from . import pdf_factory as pdf


def test_batch_runs_in_subprocess_and_keeps_order():
    docs = [pdf.invoice("A1"), pdf.scanned(), pdf.invoice("B2")]
    results = run_batch(docs, "customer", Settings())
    assert [r.get("value") or r["code"] for r in results] == ["A1", "no_text", "B2"]


def test_hung_worker_is_killed_per_file():
    hang = [sys.executable, "-I", "-c", "import time; time.sleep(60)"]
    started = time.monotonic()
    results = run_batch([b"%PDF-x", b"%PDF-y"], "customer", Settings(file_timeout=0.5), command=hang)
    assert [r["code"] for r in results] == ["timeout", "timeout"]
    assert time.monotonic() - started < 10


def test_upload_views_larger_than_a_pipe_buffer_are_released_after_a_hung_worker():
    upload = bytearray(b"%PDF-" + b"x" * (4 * 1024 * 1024))
    hang = [sys.executable, "-I", "-c", "import time; time.sleep(60)"]
    results = run_batch([memoryview(upload)], "customer", Settings(file_timeout=0.5), command=hang)
    assert results == [{"status": "error", "code": "timeout"}]
    upload.extend(b"x")


def test_crashing_worker_reports_failure():
    crash = [sys.executable, "-I", "-c", "import sys; sys.stdin.buffer.read(4); sys.exit(3)"]
    results = run_batch([b"%PDF-x"], "customer", Settings(), command=crash)
    assert results == [{"status": "error", "code": "failed"}]


def test_garbage_reply_reports_failure():
    liar = [sys.executable, "-I", "-c", "import sys; sys.stdin.buffer.read(10); print('not json', flush=True)"]
    results = run_batch([b"%PDF-x"], "customer", Settings(), command=liar)
    assert results[0]["code"] == "failed"


def test_request_budget_skips_remaining_files():
    hang = [sys.executable, "-I", "-c", "import time; time.sleep(60)"]
    settings = Settings(file_timeout=5, request_timeout=0.5)
    results = run_batch([b"%PDF-1", b"%PDF-2", b"%PDF-3"], "customer", settings, command=hang)
    assert results[0]["code"] == "timeout"
    assert [r["code"] for r in results[1:]] == ["budget", "budget"]
