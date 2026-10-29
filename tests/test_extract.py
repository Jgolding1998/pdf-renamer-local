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


@pytest.mark.parametrize("value", ["A1_02", "A1/02", "A1.02", "A1:02", "A1,02", "A1\\02", "A1\u200b02", "A1" + "2" * 39])
def test_identifier_must_be_a_complete_supported_token(value):
    assert find_value(f"Customer No: {value}", "customer") is None


@pytest.mark.parametrize("text", [
    "Customer No\nCustomer No: B2",
    "Customer No: see below\nCustomer No: B2",
    "Customer No: A1/02\nCustomer No: B2",
    "Customer No: A1_02\nCustomer No: B2",
])
def test_invalid_or_empty_occurrence_does_not_hide_a_later_identifier(text):
    assert find_value(text, "customer") == "B2"


@pytest.mark.parametrize("ending", [".", ",", ";", ". ", ",\nTotal: 12", ";Invoice No: INV-2", ".Customer No: C2"])
def test_single_terminating_punctuation_is_a_delimiter(ending):
    assert find_value("Customer No: A1" + ending, "customer") == "A1"


@pytest.mark.parametrize("ending", [".02", ",02", ";02", ":02", "..", ".,", ";Invoice", ";unknown label"])
def test_punctuation_inside_or_continuing_an_identifier_is_not_truncated(ending):
    assert find_value("Customer No: A1" + ending, "customer") is None


def test_labels_are_not_joined_across_lines():
    assert find_value("Customer\nNo: A1", "customer") is None


def test_many_empty_labels_still_find_the_complete_value():
    assert find_value("Customer No\n" * 10000 + "Customer No: B2", "customer") == "B2"


def test_identifier_is_delimited_by_whitespace_or_end_of_text():
    assert find_value("Customer No: A1\nTotal: 12.00", "customer") == "A1"
    assert find_value("Customer No: A1" + "2" * 38, "customer") == "A1" + "2" * 38


def test_invoice_mode():
    assert run(pdf.invoice(invoice_no="INV-5"), mode="invoice")["value"] == "INV-5"


def test_value_on_second_page():
    data = pdf.make_pdf([["Statement"], ["Customer No: P2-001"]])
    assert run(data) == {"status": "ok", "value": "P2-001", "pages": 2}


def test_empty_label_on_first_page_does_not_hide_second_page_value():
    data = pdf.make_pdf([["Customer No" + " " * 30000 + "-"], ["Customer No: C10042"]])
    assert run(data)["value"] == "C10042"


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
