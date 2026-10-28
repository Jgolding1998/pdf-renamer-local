import pytest
from fastapi.testclient import TestClient

from pdf_renamer_local.__main__ import is_loopback, main
from pdf_renamer_local.app import create_app, split_host
from pdf_renamer_local.config import Settings

from . import pdf_factory as pdf
from .conftest import make_session

BATCH = [("a.pdf", pdf.invoice())]


@pytest.mark.parametrize("host", ["evil.example", "127.0.0.1.nip.io", "attacker.localhost.example", ""])
def test_unknown_host_rejected(host):
    client = TestClient(create_app(Settings()), base_url="http://127.0.0.1:8000")
    response = client.get("/", headers={"Host": host})
    assert response.status_code == 400


@pytest.mark.parametrize("host", ["127.0.0.1:8000", "localhost:8000", "[::1]:8000", "LOCALHOST"])
def test_loopback_hosts_allowed(host):
    client = TestClient(create_app(Settings()), base_url="http://127.0.0.1:8000")
    assert client.get("/healthz", headers={"Host": host}).status_code == 200


def test_split_host():
    assert split_host("[::1]:8000") == "::1"
    assert split_host("localhost:1") == "localhost"
    assert split_host("::1") == "::1"


def test_missing_origin_rejected(session):
    response = session.post("/api/preview", BATCH, headers={"Origin": ""})
    assert response.status_code == 403


@pytest.mark.parametrize(
    "origin",
    ["http://evil.example", "null", "http://127.0.0.1:9999", "http://localhost:8000", "file://", "ftp://127.0.0.1:8000"],
)
def test_cross_origin_rejected(session, origin):
    assert session.post("/api/preview", BATCH, headers={"Origin": origin}).status_code == 403


def test_cross_site_fetch_metadata_rejected(session):
    response = session.post("/api/preview", BATCH, headers={"Sec-Fetch-Site": "cross-site"})
    assert response.status_code == 403


def test_csrf_token_required(session):
    assert session.post("/api/preview", BATCH, headers={"X-CSRF-Token": ""}).status_code == 403
    assert session.post("/api/preview", BATCH, headers={"X-CSRF-Token": "A" * 43}).status_code == 403


def test_csrf_cookie_required():
    session = make_session()
    session.client.cookies.clear()
    assert session.post("/api/preview", BATCH).status_code == 403


def test_valid_request_passes(session):
    assert session.post("/api/preview", BATCH).status_code == 200


def test_no_cors_headers_and_no_preflight(session):
    response = session.client.options(
        "/api/preview",
        headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "POST"},
    )
    assert "access-control-allow-origin" not in {k.lower() for k in response.headers}
    response = session.post("/api/preview", BATCH)
    assert "access-control-allow-origin" not in {k.lower() for k in response.headers}


def test_get_on_api_not_allowed(session):
    assert session.client.get("/api/zip").status_code == 405


def test_api_docs_disabled(session):
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert session.client.get(path).status_code == 404


def test_static_path_traversal(session):
    for path in ("/static/../app.py", "/static/..%2fapp.py", "/static/%2e%2e/config.py"):
        response = session.client.get(path)
        assert response.status_code == 404
        assert b"Settings" not in response.content


def test_csrf_token_reused_across_tabs(session):
    first = session.token
    page = session.client.get("/")
    assert first in page.text


def test_cli_refuses_public_bind(monkeypatch, capsys):
    monkeypatch.delenv("PDFR_ALLOW_NON_LOOPBACK", raising=False)
    assert main(["--host", "0.0.0.0"]) == 2
    assert "Refusing" in capsys.readouterr().err


def test_is_loopback():
    assert is_loopback("127.0.0.1") and is_loopback("::1") and is_loopback("localhost")
    assert not is_loopback("0.0.0.0") and not is_loopback("192.168.1.10")
