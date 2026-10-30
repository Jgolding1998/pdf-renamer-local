import io
import zipfile

from pdf_renamer_local.config import MB, Settings

from . import pdf_factory as pdf
from .conftest import make_session


def mixed_batch():
    return [
        ("first.pdf", pdf.invoice("C10042")),
        ("../../etc/passwd.pdf", pdf.invoice("C10042")),
        ("scan.pdf", pdf.scanned()),
        ("broken.pdf", pdf.corrupt()),
        ("locked.pdf", pdf.encrypted()),
        ("readme.pdf", b"just text, not a pdf"),
        ("thanks.pdf", pdf.make_pdf([["Thank you for your order"]])),
        ("CON.pdf", pdf.invoice("C-77")),
        ("empty.pdf", b""),
    ]


def test_index_page_sets_csrf_cookie_and_headers(session):
    page = session.client.get("/")
    assert page.status_code == 200
    assert "PDF Renamer" in page.text
    assert "{{" not in page.text
    cookie = page.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    csp = page.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "frame-ancestors 'none'" in csp
    assert page.headers["x-content-type-options"] == "nosniff"
    assert page.headers["cache-control"] == "no-store"


def test_static_assets_served(session):
    assert session.client.get("/static/app.js").status_code == 200
    assert session.client.get("/static/styles.css").status_code == 200


def test_mixed_preview(session):
    response = session.preview(mixed_batch())
    assert response.status_code == 200
    plan = response.json()
    by_index = {i["index"]: i for i in plan["items"]}
    assert plan["summary"] == {"total": 9, "renamed": 3, "failed": 6}
    assert by_index[0]["new_name"] == "Customer-C10042.pdf"
    assert by_index[1]["new_name"] == "Customer-C10042-2.pdf"
    assert by_index[1]["renamed_duplicate"] is True
    assert by_index[1]["original"] == "passwd.pdf"
    assert by_index[7]["new_name"] == "Customer-C-77.pdf"
    codes = {i["index"]: i.get("code") for i in plan["items"] if i["status"] == "error"}
    assert codes == {2: "no_text", 3: "corrupt", 4: "encrypted", 5: "not_pdf", 6: "no_match", 8: "empty"}
    assert all(i["message"] for i in plan["items"] if i["status"] == "error")


def test_zip_matches_preview_exactly(session):
    batch = mixed_batch()
    plan = session.preview(batch).json()
    response = session.zip(batch, plan["plan_id"])
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert response.headers["x-plan-id"] == plan["plan_id"]
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    expected = [i["new_name"] for i in plan["items"] if i["status"] == "ok"]
    assert archive.namelist() == expected
    for item in plan["items"]:
        if item["status"] == "ok":
            assert archive.read(item["new_name"]) == batch[item["index"]][1]


def test_text_limit_is_reported_and_download_matches_preview():
    session = make_session(Settings(max_text_chars=1000))
    filler = ["Line of statement text " + str(n) for n in range(400)]
    batch = [("long.pdf", pdf.make_pdf([filler, ["Customer No: C10042"]])), ("short.pdf", pdf.invoice("C7"))]
    plan = session.preview(batch).json()
    long_item, short_item = plan["items"]
    assert long_item["status"] == "error" and long_item["code"] == "text_limit"
    assert "first 1,000 characters" in long_item["message"] and "C10042" not in long_item["message"]
    assert short_item["new_name"] == "Customer-C7.pdf"
    assert session.preview(batch).json() == plan
    response = session.zip(batch, plan["plan_id"])
    assert response.status_code == 200
    assert zipfile.ZipFile(io.BytesIO(response.content)).namelist() == ["Customer-C7.pdf"]


def test_plan_is_deterministic(session):
    batch = mixed_batch()
    first = session.preview(batch).json()
    second = session.preview(batch).json()
    assert first == second
    zip_a = session.zip(batch, first["plan_id"]).content
    zip_b = session.zip(batch, first["plan_id"]).content
    assert zip_a == zip_b


def test_zip_rejects_changed_files(session):
    batch = [("a.pdf", pdf.invoice("A1")), ("b.pdf", pdf.invoice("B2"))]
    plan = session.preview(batch).json()
    swapped = list(reversed(batch))
    assert session.zip(swapped, plan["plan_id"]).status_code == 409
    assert session.zip(batch, plan["plan_id"], prefix="Other-").status_code == 409
    assert session.zip(batch, "0" * 64).status_code == 409
    assert session.zip(batch, plan["plan_id"]).status_code == 200


def test_zip_with_nothing_renamable(session):
    batch = [("scan.pdf", pdf.scanned())]
    plan = session.preview(batch).json()
    assert plan["summary"]["renamed"] == 0
    assert session.zip(batch, plan["plan_id"]).status_code == 422


def test_invoice_mode_and_empty_prefix(session):
    plan = session.preview([("x.pdf", pdf.invoice(invoice_no="INV-9"))], mode="invoice", prefix="").json()
    assert plan["items"][0]["new_name"] == "INV-9.pdf"


def test_reserved_name_without_prefix(session):
    plan = session.preview([("x.pdf", pdf.make_pdf([["Invoice No: COM1"]]))], mode="invoice", prefix="").json()
    assert plan["items"][0]["new_name"] == "_COM1.pdf"


def test_bad_options_rejected(session):
    batch = [("a.pdf", pdf.invoice())]
    assert session.preview(batch, prefix="../evil").status_code == 422
    assert session.preview(batch, prefix="a" * 33).status_code == 422
    assert session.preview(batch, mode="ssn").status_code == 422
    no_files = session.client.post(
        "/api/preview",
        content=b'--abc\r\nContent-Disposition: form-data; name="mode"\r\n\r\ncustomer\r\n--abc--\r\n',
        headers={
            "Origin": "http://127.0.0.1:8000",
            "X-CSRF-Token": session.token,
            "Content-Type": "multipart/form-data; boundary=abc",
        },
    )
    assert no_files.status_code == 422


def test_limits_file_count(small_limits):
    session = make_session(small_limits)
    batch = [(f"{n}.pdf", pdf.invoice(f"C{n}")) for n in range(4)]
    response = session.preview(batch)
    assert response.status_code == 413
    assert "Too many files" in response.json()["detail"]


def test_limits_per_file_size_is_per_item_error(small_limits):
    session = make_session(small_limits)
    big = pdf.invoice() + b"%" + b"0" * (MB + 10)
    plan = session.preview([("big.pdf", big), ("ok.pdf", pdf.invoice("C5"))]).json()
    assert plan["items"][0]["code"] == "too_large"
    assert plan["items"][0]["sha256"] is None
    assert plan["items"][1]["new_name"] == "Customer-C5.pdf"


def test_limits_total_size(small_limits):
    session = make_session(small_limits)
    chunk = b"%PDF-1.4\n" + b"0" * (MB - 100)
    response = session.preview([("a.pdf", chunk), ("b.pdf", chunk), ("c.pdf", chunk)])
    assert response.status_code == 413


def test_declared_content_length_rejected_early(small_limits):
    session = make_session(small_limits)
    response = session.client.post(
        "/api/preview",
        content=b"x",
        headers={
            "Origin": "http://127.0.0.1:8000",
            "X-CSRF-Token": session.token,
            "Content-Type": "multipart/form-data; boundary=abc",
            "Content-Length": str(10 * MB),
        },
    )
    assert response.status_code == 413


def test_page_limit(small_limits):
    session = make_session(small_limits)
    plan = session.preview([("long.pdf", pdf.many_pages(6))]).json()
    assert plan["items"][0]["code"] == "too_many_pages"


def test_parse_timeout_is_reported_per_file():
    session = make_session(Settings(file_timeout=0.001))
    plan = session.preview([("a.pdf", pdf.invoice()), ("b.pdf", pdf.invoice("C2"))]).json()
    assert [i["code"] for i in plan["items"]] == ["timeout", "timeout"]


def test_non_multipart_rejected(session):
    response = session.client.post(
        "/api/preview",
        json={"files": []},
        headers={"Origin": "http://127.0.0.1:8000", "X-CSRF-Token": session.token},
    )
    assert response.status_code == 415


def test_malformed_multipart(session):
    response = session.client.post(
        "/api/preview",
        content=b"--abc\r\nContent-Disposition: form-data\r\n\r\nx\r\n--abc--\r\n",
        headers={
            "Origin": "http://127.0.0.1:8000",
            "X-CSRF-Token": session.token,
            "Content-Type": "multipart/form-data; boundary=abc",
        },
    )
    assert response.status_code == 400


def test_unexpected_file_field_rejected(session):
    response = session.client.post(
        "/api/preview",
        files=[("other", ("a.pdf", pdf.invoice(), "application/pdf"))],
        headers={"Origin": "http://127.0.0.1:8000", "X-CSRF-Token": session.token},
    )
    assert response.status_code == 400


def test_client_filename_is_never_used_for_output(session):
    nasty = '<img src=x onerror=alert(1)>\\..\\..\\x.pdf'
    plan = session.preview([(nasty, pdf.invoice("Z9"))]).json()
    item = plan["items"][0]
    assert item["new_name"] == "Customer-Z9.pdf"
    assert "\\" not in item["original"] and "/" not in item["original"]
