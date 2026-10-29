import pytest
from fastapi.testclient import TestClient

from pdf_renamer_local.app import create_app
from pdf_renamer_local.config import Settings

from . import pdf_factory as pdf

HOST = "pdf.example.test"


def test_explicit_https_proxy_scheme_accepts_uploads_and_sets_a_secure_cookie():
    app = create_app(Settings(allowed_hosts=(HOST,), public_scheme="https"))
    client = TestClient(app, base_url=f"http://{HOST}")
    page = client.get("/")
    assert "secure" in page.headers["set-cookie"].lower()
    token = page.cookies["pdfr_csrf"]
    response = client.post(
        "/api/preview", data={"mode": "customer"},
        files={"files": ("source.pdf", pdf.invoice(), "application/pdf")},
        headers={"Origin": f"https://{HOST}", "X-CSRF-Token": token,
                 "Cookie": f"pdfr_csrf={token}", "Sec-Fetch-Site": "same-origin"},
    )
    assert response.status_code == 200
    assert response.json()["items"][0]["value"] == "C10042"
    rejected = client.post(
        "/api/preview", data={"mode": "customer"},
        files={"files": ("source.pdf", pdf.invoice(), "application/pdf")},
        headers={"Origin": f"http://{HOST}", "X-CSRF-Token": token,
                 "Cookie": f"pdfr_csrf={token}", "Sec-Fetch-Site": "same-origin"},
    )
    assert rejected.status_code == 403


def test_forwarded_proto_does_not_change_the_scheme_without_explicit_configuration():
    client = TestClient(create_app(Settings(allowed_hosts=(HOST,))), base_url=f"http://{HOST}")
    page = client.get("/")
    token = page.cookies["pdfr_csrf"]
    response = client.post(
        "/api/preview", data={"mode": "customer"},
        files={"files": ("source.pdf", pdf.invoice(), "application/pdf")},
        headers={"Origin": f"https://{HOST}", "X-Forwarded-Proto": "https", "X-CSRF-Token": token,
                 "Cookie": f"pdfr_csrf={token}", "Sec-Fetch-Site": "same-origin"},
    )
    assert response.status_code == 403
    assert "secure" not in client.get("/").headers["set-cookie"].lower()


@pytest.mark.parametrize("value", ["", "ftp", "HTTPS", True, ["https"]])
def test_public_scheme_configuration_is_strict(value):
    with pytest.raises(ValueError):
        Settings(public_scheme=value)


@pytest.mark.parametrize("value,expected", [("https", "https"), ("", None)])
def test_public_scheme_environment_setting(monkeypatch, value, expected):
    monkeypatch.setenv("PDFR_PUBLIC_SCHEME", value)
    assert Settings.from_env().public_scheme == expected
