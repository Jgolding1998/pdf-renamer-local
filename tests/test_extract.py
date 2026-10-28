import pytest

from pdf_renamer_local.extract import analyze, find_value

from . import pdf_factory as pdf


def run(data, mode="customer", max_pages=50):
    return analyze(data, mode, max_pages, 200_000)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Customer No: C10042", "C10042"),
        ("Customer No. 77812", "77812"),
        ("CUSTOMER NUMBER # AB-12", "AB-12"),
        ("Customer #4411", "4411"),
        ("Customer ID:\n  X900", "X900"),
        ("Customer Name: Bob\nCustomer No: K-7", "K-7"),
    ],
)
def test_customer_patterns(text, expected):
    assert find_value(text, "customer") == expected


@pytest.mark.parametrize("text", ["Customer Name: Sample", "Customer No: none", "Customers: 12", "No customer here"])
def test_customer_pattern_requires_label_and_digit(text):
    assert find_value(text, "customer") is None


def test_invoice_mode():
    assert run(pdf.invoice(invoice_no="INV-5"), mode="invoice")["value"] == "INV-5"


def test_value_on_second_page():
    data = pdf.make_pdf([["Statement"], ["Customer No: P2-001"]])
    assert run(data) == {"status": "ok", "value": "P2-001", "pages": 2}


def test_error_codes():
    assert run(b"")["code"] == "empty"
    assert run(b"MZ\x90\x00not a pdf")["code"] == "not_pdf"
    assert run(pdf.scanned())["code"] == "no_text"
    assert run(pdf.make_pdf([["Thanks for your order"]]))["code"] == "no_match"
    assert run(pdf.corrupt())["code"] == "corrupt"
    assert run(pdf.encrypted())["code"] == "encrypted"
    assert run(pdf.many_pages(6), max_pages=5)["code"] == "too_many_pages"


def test_encrypted_with_empty_user_password_is_readable():
    assert run(pdf.encrypted(user_password=""))["value"] == "C10042"


def test_header_only_pdf_is_corrupt():
    assert run(b"%PDF-1.7\n%%EOF")["code"] == "corrupt"
