from . import pdf_factory as pdf


def test_zip_rejects_malformed_plan_id_before_parser(session, monkeypatch):
    def unexpected_parser(*_args, **_kwargs):
        raise AssertionError("A malformed preview id must not invoke the PDF parser")

    monkeypatch.setattr("pdf_renamer_local.app.build_plan", unexpected_parser)
    batch = [("sample.pdf", pdf.invoice())]
    assert session.post("/api/zip", batch).status_code == 422
    for plan_id in ("", chr(233), "0" * 63, "0" * 65, "g" * 64, "A" * 64):
        response = session.zip(batch, plan_id)
        assert response.status_code == 422
        assert "Preview the current files" in response.json()["detail"]
