import io
import zipfile

import pytest
from pypdf import PdfReader

from pdf_renamer_local.extract import analyze, find_value
from . import pdf_factory as pdf


@pytest.mark.parametrize("space", [" ", "\t", "\u00a0", "\u2009", "\u202f"])
@pytest.mark.parametrize("first,second", [("C100", "42"), ("1234", "5678"), ("INV-1", "2026-01-15"), ("C100", "4" * 60)])
def test_same_line_numeric_continuation_is_not_shortened(space, first, second):
    assert find_value(f"Customer No: {first}{space}{second}", "customer") is None


@pytest.mark.parametrize("following", ["due", "Date: 2026-01-15", "Invoice No: INV-2", "\n42", "\f42"])
def test_prose_labelled_fields_and_later_lines_do_not_invalidate_whole_identifiers(following):
    assert find_value("Customer No: C10042 " + following, "customer") == "C10042"


def test_ambiguous_occurrence_does_not_hide_a_later_explicit_identifier():
    assert find_value("Customer No: C100 42\nCustomer No: C10077", "customer") == "C10077"


def test_real_pdf_kerning_is_reported_as_ambiguous():
    data = pdf.kerned_customer()
    assert "C100 42" in PdfReader(io.BytesIO(data)).pages[0].extract_text()
    assert analyze(data, "customer", 50, 200000)["code"] == "ambiguous_identifier"


@pytest.mark.parametrize("tail", ["42", "A2", "5678", " " * 200 + "42", "A" * 200 + "2"])
def test_text_limit_never_turns_split_identifiers_into_a_shortened_value(tail):
    line = "Customer No: C100 " + tail
    data = pdf.make_pdf([[line]])
    for limit in range(1, len(line) + 3):
        result = analyze(data, "customer", 50, limit)
        assert result["status"] != "ok", (limit, result)


def test_preview_and_zip_exclude_both_kerned_identifiers_and_preserve_valid_bytes(session):
    batch = [("one.pdf", pdf.kerned_customer("42")), ("two.pdf", pdf.kerned_customer("77")),
             ("valid.pdf", pdf.make_pdf([["Customer No: C10042 Date: 2026-01-15"]]))]
    plan = session.preview(batch).json()
    assert plan["summary"] == {"total": 3, "renamed": 1, "failed": 2}
    for item in plan["items"][:2]:
        assert item["code"] == "ambiguous_identifier"
        assert "Review" in item["message"]
        assert item["new_name"] is None
    response = session.zip(batch, plan["plan_id"])
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.namelist() == ["Customer-C10042.pdf"]
        assert archive.read("Customer-C10042.pdf") == batch[2][1]
