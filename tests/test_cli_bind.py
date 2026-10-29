import pytest
from fastapi.testclient import TestClient

from pdf_renamer_local.__main__ import main


@pytest.mark.parametrize("host", ["127.0.0.2", "::1", "0:0:0:0:0:0:0:1", "::FFFF:127.0.0.1"])
def test_bound_loopback_host_is_allowed_by_default_and_printed_as_a_url(monkeypatch, capsys, host):
    monkeypatch.delenv("PDFR_ALLOWED_HOSTS", raising=False)
    launched = []
    monkeypatch.setattr("pdf_renamer_local.__main__.uvicorn.run", lambda app, **kwargs: launched.append((app, kwargs)))
    assert main(["--host", host]) == 0
    authority = f"[{host}]:8000" if ":" in host else f"{host}:8000"
    assert f"http://{authority}/" in capsys.readouterr().out
    app, options = launched[0]
    assert options["host"] == host
    client = TestClient(app, base_url="http://" + authority)
    assert client.get("/healthz").status_code == 200
    assert client.get("/").status_code == 200
    assert client.get("/", headers={"Host": "untrusted.example"}).status_code == 400


def test_explicit_host_allowlist_is_preserved_for_reverse_proxy(monkeypatch):
    monkeypatch.setenv("PDFR_ALLOWED_HOSTS", "pdf.example.test")
    launched = []
    monkeypatch.setattr("pdf_renamer_local.__main__.uvicorn.run", lambda app, **kwargs: launched.append(app))
    assert main(["--host", "127.0.0.2"]) == 0
    client = TestClient(launched[0], base_url="http://127.0.0.2:8000")
    assert client.get("/healthz").status_code == 400
    assert client.get("/healthz", headers={"Host": "pdf.example.test"}).status_code == 200
