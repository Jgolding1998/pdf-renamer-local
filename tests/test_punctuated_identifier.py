import io
import zipfile

import pytest

from pdf_renamer_local.extract import analyze, find_value
from . import pdf_factory as pdf


@pytest.mark.parametrize("punctuation", list(".,;"))
@pytest.mark.parametrize("mode,label,first,tail", [
    ("customer", "Customer No", "C100", "42"),
    ("invoice", "Invoice No", "INV-1", "2026-01-15"),
])
def test_punctuation_does_not_hide_a_numeric_continuation(punctuation, mode, label, first, tail):
    line = f"{label}: {first}{punctuation} {tail}"
    assert find_value(line, mode) is None
    result = analyze(pdf.make_pdf([[line]]), mode, 50, 200000)
    assert result == {"status": "error", "code": "ambiguous_identifier", "pages": 1}


@pytest.mark.parametrize("punctuation", list(".,;"))
def test_punctuated_numeric_continuation_stays_rejected_at_every_text_cutoff(punctuation):
    line = f"Customer No: C100{punctuation} 42"
    data = pdf.make_pdf([[line]])
    for limit in range(1, len(line) + 3):
        assert analyze(data, "customer", 50, limit)["status"] != "ok", limit


@pytest.mark.parametrize("punctuation", list(".,;"))
def test_punctuation_preserves_complete_ids_prose_labels_and_later_lines(punctuation):
    for following in ("", " due", " Date: 2026-01-15", " Invoice No: INV-2", "Invoice No: INV-2", "\n42"):
        text = f"Customer No: C10042{punctuation}{following}"
        assert find_value(text, "customer") == "C10042", following


@pytest.mark.parametrize("punctuation", list(".,;"))
def test_a_later_explicit_id_survives_punctuated_ambiguity(punctuation):
    text = f"Customer No: C100{punctuation} 42\nCustomer No: C10077"
    assert find_value(text, "customer") == "C10077"


def test_preview_and_zip_exclude_punctuated_partial_ids_and_preserve_original_bytes(session):
    batch = [(f"ambiguous-{index}.pdf", pdf.make_pdf([[f"Customer No: C100{mark} 42"]]))
             for index, mark in enumerate(".,;")]
    batch.append(("valid.pdf", pdf.make_pdf([["Customer No: C10042, Date: 2026-01-15"]])))
    plan = session.preview(batch).json()
    assert plan["summary"] == {"total": 4, "renamed": 1, "failed": 3}
    for item in plan["items"][:3]:
        assert item["new_name"] is None
        assert item["code"] == "ambiguous_identifier"
        assert "Review" in item["message"]
    response = session.zip(batch, plan["plan_id"])
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.namelist() == ["Customer-C10042.pdf"]
        assert archive.read("Customer-C10042.pdf") == batch[-1][1]
