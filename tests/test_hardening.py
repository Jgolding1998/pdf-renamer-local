import asyncio

import pytest

import pdf_renamer_local.app as app_module
from pdf_renamer_local.__main__ import main
from pdf_renamer_local.app import GuardMiddleware
from pdf_renamer_local.config import MB, Settings

from .conftest import make_session


def raw_upload(session, body, boundary="abc", extra_headers=None):
    return session.client.post(
        "/api/preview", content=body,
        headers={"Origin": "http://127.0.0.1:8000", "X-CSRF-Token": session.token,
                 "Content-Type": f"multipart/form-data; boundary={boundary}", **(extra_headers or {})},
    )


@pytest.mark.parametrize("name,value", [
    ("max_files", 0), ("max_files", 101), ("max_files", True),
    ("max_parallel_jobs", 0), ("max_parallel_jobs", 33),
    ("max_file_bytes", 0), ("max_file_bytes", 100 * MB + 1),
    ("max_total_bytes", MB), ("max_total_bytes", 500 * MB + 1),
    ("max_pages", 0), ("max_pages", 501),
    ("max_text_chars", 0), ("max_text_chars", 1_000_001),
    ("worker_memory_mb", 127), ("worker_memory_mb", 2049),
    ("file_timeout", 0), ("file_timeout", float("nan")), ("file_timeout", float("inf")),
    ("request_timeout", -1), ("request_timeout", 601),
    ("upload_timeout", 0), ("upload_timeout", float("inf")), ("upload_timeout", 121),
    ("allowed_hosts", ()), ("allowed_hosts", ("*",)), ("allowed_hosts", ("localhost:8000",)),
])
def test_invalid_resource_settings_fail_at_creation(name, value):
    with pytest.raises(ValueError):
        Settings(**{name: value})


def test_maximum_valid_settings_and_environment(monkeypatch):
    Settings(max_files=100, max_parallel_jobs=32, max_file_bytes=100 * MB, max_total_bytes=500 * MB,
             max_pages=500, max_text_chars=1_000_000, worker_memory_mb=2048)
    Settings(max_file_bytes=1, max_total_bytes=1)
    monkeypatch.setenv("PDFR_UPLOAD_TIMEOUT", "2.5")
    monkeypatch.setenv("PDFR_MAX_TEXT_CHARS", "1234")
    assert Settings.from_env().upload_timeout == 2.5
    assert Settings.from_env().max_text_chars == 1234


@pytest.mark.parametrize("name,value", [("Host", "127.0.0.1:8000"), ("Origin", "http://127.0.0.1:8000"),
                                       ("Content-Length", "0"), ("Content-Type", "application/json")])
def test_duplicate_framing_headers_rejected(session, name, value):
    assert session.client.get("/healthz", headers=[(name, value), (name, value)]).status_code == 400


def test_transfer_encoding_rejected(session):
    assert session.client.get("/healthz", headers={"Transfer-Encoding": "chunked"}).status_code == 400


@pytest.mark.parametrize("origin", ["https://127.0.0.1:8000", "http://127.0.0.1:8000/",
                                    "http://127.0.0.1:8000/path", "http://127.0.0.1:8000?query",
                                    "http://127.0.0.1:8000#fragment", "http://[invalid"])
def test_origin_requires_exact_scheme_and_authority(session, origin):
    response = session.client.post("/api/preview", content=b"", headers={"Origin": origin})
    assert response.status_code == 403


@pytest.mark.parametrize("host", ["localhost:bad", "localhost:0", "localhost:65536", "localhost/path", "localhost#fragment"])
def test_malformed_host_rejected(session, host):
    assert session.client.get("/healthz", headers={"Host": host}).status_code == 400


@pytest.mark.parametrize("body", [
    b'--abc\r\nContent-Disposition: form-data; name="mode"\r\n\r\ncustomer',
    b'--abc\r\nContent-Disposition: form-data; name="mode"\r\n\r\ncustomer\r\n--abc',
    b'--abc\r\nContent-Disposition: form-data; name="mode"\r\n\r\ncustomer\r\n--abc\r\n',
])
def test_truncated_multipart_rejected(session, body):
    assert raw_upload(session, body).status_code == 400


def test_duplicate_scalar_form_field_rejected(session):
    part = b'--abc\r\nContent-Disposition: form-data; name="mode"\r\n\r\ncustomer\r\n'
    assert raw_upload(session, part + part + b"--abc--\r\n").status_code == 400


def test_duplicate_multipart_header_rejected(session):
    body = (b'--abc\r\nContent-Disposition: form-data; name="mode"\r\n'
            b'Content-Disposition: form-data; name="prefix"\r\n\r\nx\r\n--abc--\r\n')
    assert raw_upload(session, body).status_code == 400


@pytest.mark.parametrize("headers", [b"X" * 65 + b": x\r\n", b"X-Test: " + b"x" * 4097 + b"\r\n",
                                     b"".join(f"X-{i}: value\r\n".encode() for i in range(9))])
def test_multipart_header_limits(session, headers):
    body = b"--abc\r\n" + headers + b'Content-Disposition: form-data; name="mode"\r\n\r\ncustomer\r\n--abc--\r\n'
    assert raw_upload(session, body).status_code == 400


def test_long_boundary_and_wrong_content_length(session):
    assert raw_upload(session, b"", boundary="x" * 71).status_code == 400
    body = b'--abc\r\nContent-Disposition: form-data; name="mode"\r\n\r\ncustomer\r\n--abc--\r\n'
    assert raw_upload(session, body, extra_headers={"Content-Length": str(len(body) + 1)}).status_code == 400


def test_upload_timeout_releases_capacity(monkeypatch):
    async def stall(*args):
        await asyncio.Event().wait()
    monkeypatch.setattr(app_module, "read_upload", stall)
    session = make_session(Settings(upload_timeout=0.02, max_parallel_jobs=1))
    assert raw_upload(session, b"").status_code == 408

    async def empty(*args):
        return {}, []
    monkeypatch.setattr(app_module, "read_upload", empty)
    assert raw_upload(session, b"").status_code == 422


def test_capacity_rejects_before_receive_and_is_held_until_response_finishes():
    async def scenario():
        entered, finish_response = asyncio.Event(), asyncio.Event()
        requests_read = []
        messages = []

        async def downstream(scope, receive, send):
            await receive()
            entered.set()
            await finish_response.wait()
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"zip-response"})

        guard = GuardMiddleware(downstream, Settings(max_parallel_jobs=1))
        scope = {"type": "http", "method": "POST", "path": "/api/zip",
                 "headers": [(b"host", b"localhost:8000")], "http_version": "1.1"}

        async def receive():
            requests_read.append(True)
            return {"type": "http.request", "body": b"synthetic", "more_body": False}

        async def send(message):
            messages.append(message)

        first = asyncio.create_task(guard(scope, receive, send))
        await entered.wait()
        await asyncio.wait_for(guard(scope, receive, send), 0.2)
        assert len(requests_read) == 1
        assert next(m for m in messages if m["type"] == "http.response.start")["status"] == 503
        finish_response.set()
        await first
        assert not guard.jobs.locked()
    asyncio.run(scenario())


def test_cli_reports_invalid_configuration_and_remote_processing_boundary(monkeypatch, capsys):
    monkeypatch.setenv("PDFR_MAX_PARALLEL_JOBS", "0")
    assert main([]) == 2
    assert "Invalid configuration" in capsys.readouterr().err
    monkeypatch.delenv("PDFR_MAX_PARALLEL_JOBS")
    monkeypatch.setenv("PDFR_ALLOW_NON_LOOPBACK", "1")
    monkeypatch.setattr("pdf_renamer_local.__main__.uvicorn.run", lambda *args, **kwargs: None)
    assert main(["--host", "0.0.0.0"]) == 0
    message = capsys.readouterr().out
    assert "no external processing services" in message
    assert "never leave" not in message
