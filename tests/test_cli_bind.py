import ipaddress

import pytest
from fastapi.testclient import TestClient

from pdf_renamer_local.__main__ import main


@pytest.fixture(params=[False, True], ids=["legacy", "mapped-aware"])
def ipv6_loopback_semantics(monkeypatch, request):
    def loopback(address):
        mapped = address.ipv4_mapped
        return mapped.is_loopback if request.param and mapped is not None else int(address) == 1
    monkeypatch.setattr(ipaddress.IPv6Address, "is_loopback", property(loopback))


@pytest.mark.parametrize("host", ["127.0.0.2", "::1", "0:0:0:0:0:0:0:1"])
def test_bound_loopback_host_is_allowed_by_default_and_printed_as_a_url(monkeypatch, capsys, host):
    monkeypatch.delenv("PDFR_ALLOW_NON_LOOPBACK", raising=False)
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
    for other_host in ("127.0.0.3:8000", "[::ffff:127.0.0.3]:8000"):
        assert client.get("/healthz", headers={"Host": other_host}).status_code == 400


@pytest.mark.parametrize("host", ["::FFFF:127.0.0.1", "::ffff:7f00:1", "::ffff:8.8.8.8", "::ffff:192.168.1.10"])
def test_mapped_bind_spellings_are_refused_with_guidance(monkeypatch, capsys, host, ipv6_loopback_semantics):
    monkeypatch.delenv("PDFR_ALLOW_NON_LOOPBACK", raising=False)
    launched = []
    monkeypatch.setattr("pdf_renamer_local.__main__.uvicorn.run", lambda app, **kwargs: launched.append(app))
    assert main(["--host", host]) == 2
    assert "Use 127.0.0.1 or ::1 for loopback" in capsys.readouterr().err
    assert launched == []


def test_nonloopback_override_does_not_enable_an_unsupported_mapped_bind(monkeypatch, capsys):
    monkeypatch.setenv("PDFR_ALLOW_NON_LOOPBACK", "1")
    launched = []
    monkeypatch.setattr("pdf_renamer_local.__main__.uvicorn.run", lambda app, **kwargs: launched.append(app))
    assert main(["--host", "::ffff:127.0.0.1"]) == 2
    assert "IPv4-mapped IPv6 bind addresses are unsupported" in capsys.readouterr().err
    assert launched == []


@pytest.mark.parametrize("host", ["::", "2001:db8::1"])
def test_native_nonloopback_binds_remain_refused(monkeypatch, capsys, host):
    monkeypatch.delenv("PDFR_ALLOW_NON_LOOPBACK", raising=False)
    launched = []
    monkeypatch.setattr("pdf_renamer_local.__main__.uvicorn.run", lambda app, **kwargs: launched.append(app))
    assert main(["--host", host]) == 2
    assert "Refusing to bind a non-loopback address" in capsys.readouterr().err
    assert launched == []


def test_explicit_host_allowlist_is_preserved_for_reverse_proxy(monkeypatch):
    monkeypatch.setenv("PDFR_ALLOWED_HOSTS", "pdf.example.test")
    launched = []
    monkeypatch.setattr("pdf_renamer_local.__main__.uvicorn.run", lambda app, **kwargs: launched.append(app))
    assert main(["--host", "127.0.0.2"]) == 0
    client = TestClient(launched[0], base_url="http://127.0.0.2:8000")
    assert client.get("/healthz").status_code == 400
    assert client.get("/healthz", headers={"Host": "pdf.example.test"}).status_code == 200
