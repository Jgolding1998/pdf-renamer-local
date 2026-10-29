import io
import zipfile

import pytest

from pdf_renamer_local.naming import safe_stem, valid_prefix

from . import pdf_factory as pdf


@pytest.mark.parametrize("value", ["A1/02", "A1_02"])
def test_unsupported_identifier_is_not_partially_renamed_or_downloaded(session, value):
    batch = [("source.pdf", pdf.invoice(value))]
    plan = session.preview(batch).json()
    assert plan["summary"]["renamed"] == 0
    assert plan["items"][0]["new_name"] is None
    assert session.zip(batch, plan["plan_id"]).status_code == 422


@pytest.mark.parametrize("prefix", ["-", "-x", "--"])
def test_leading_hyphen_prefix_is_rejected_before_the_parser(session, monkeypatch, prefix):
    def unexpected_parser(*args, **kwargs):
        raise AssertionError("Invalid prefixes must not launch the parser")
    monkeypatch.setattr("pdf_renamer_local.app.build_plan", unexpected_parser)
    response = session.preview([("source.pdf", pdf.invoice())], prefix=prefix)
    assert response.status_code == 422


def test_later_complete_identifier_is_previewed_and_downloaded(session):
    batch = [("source.pdf", pdf.make_pdf([["Customer No", "Customer No: A1/02", "Customer No: B2."]]))]
    plan = session.preview(batch).json()
    assert plan["items"][0]["new_name"] == "Customer-B2.pdf"
    assert session.zip(batch, plan["plan_id"]).status_code == 200


@pytest.mark.parametrize("mode,label", [("customer", "Customer"), ("invoice", "Invoice")])
def test_nonbreaking_space_label_is_previewed_and_exported_unchanged(session, mode, label):
    source = pdf.make_pdf([[f"{label}\u00a0No: Q42"]])
    batch = [("source.pdf", source)]
    plan = session.preview(batch, mode=mode, prefix="").json()
    assert plan["items"][0]["new_name"] == "Q42.pdf"
    response = session.zip(batch, plan["plan_id"], mode=mode, prefix="")
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.namelist() == ["Q42.pdf"]
        assert archive.read("Q42.pdf") == source


@pytest.mark.parametrize("algorithm", ["AES-128", "AES-256"])
def test_owner_only_aes_export_preserves_the_original_encrypted_bytes(session, algorithm):
    source = pdf.encrypted(user_password="", algorithm=algorithm)
    batch = [("owner-only.pdf", source)]
    plan = session.preview(batch).json()
    assert plan["items"][0]["new_name"] == "Customer-C10042.pdf"
    response = session.zip(batch, plan["plan_id"])
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.read("Customer-C10042.pdf") == source


@pytest.mark.parametrize("following", ["2026-01-15", "12 Widgets"])
def test_cross_page_number_is_not_renamed_but_later_explicit_label_is(session, following):
    source = pdf.make_pdf([["Customer No:"], [following]])
    batch = [("source.pdf", source)]
    plan = session.preview(batch).json()
    assert plan["summary"]["renamed"] == 0
    assert session.zip(batch, plan["plan_id"]).status_code == 422
    source = pdf.make_pdf([["Customer No:"], [following], ["Customer No: B2"]])
    batch = [("source.pdf", source)]
    plan = session.preview(batch).json()
    assert plan["items"][0]["new_name"] == "Customer-B2.pdf"
    response = session.zip(batch, plan["plan_id"])
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.read("Customer-B2.pdf") == source


@pytest.mark.parametrize("prefix", ["-", "-x", "--"])
def test_leading_hyphen_cannot_form_a_supported_stem(prefix):
    assert not valid_prefix(prefix)
    assert safe_stem(prefix, "C1") is None


@pytest.mark.parametrize("prefix", ["", "_", "0-", "Customer-", "x" * 32])
def test_supported_prefixes_are_valid_for_the_resulting_stem(prefix):
    assert valid_prefix(prefix)
    assert safe_stem(prefix, "C1") == prefix + "C1"
