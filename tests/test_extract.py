import random

import pytest

from pdf_renamer_local.extract import _scan, analyze, find_value

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
        ("Customer Name: Bob\nCustomer No: K-7", "K-7"),
    ],
)
def test_customer_patterns(text, expected):
    assert find_value(text, "customer") == expected


@pytest.mark.parametrize("text", ["Customer Name: Sample", "Customer No: none", "Customers: 12", "No customer here"])
def test_customer_pattern_requires_label_and_digit(text):
    assert find_value(text, "customer") is None


@pytest.mark.parametrize("space", ["\u00a0", "\u2009", "\u202f"])
@pytest.mark.parametrize("mode,label", [("customer", "Customer"), ("invoice", "Invoice")])
def test_horizontal_unicode_spaces_in_supported_labels(space, mode, label):
    assert find_value(f"{label}{space}No: C10042", mode) == "C10042"


@pytest.mark.parametrize("separator", ["\r", "\v", "\f", "\x1c", "\x1f", "\x85", "\u2028", "\u2029"])
def test_vertical_unicode_separators_do_not_join_a_label(separator):
    assert find_value(f"Customer{separator}No: C10042", "customer") is None


@pytest.mark.parametrize("following", ["2026-01-15", "0042 Main Street", "12 Widgets", "  X900"])
def test_blank_label_does_not_take_an_unlabeled_number_from_the_next_line(following):
    assert find_value(f"Customer No:\n{following}", "customer") is None


@pytest.mark.parametrize("value", ["2026-01-15", "0042", "12"])
def test_explicit_same_line_numeric_identifiers_remain_supported(value):
    assert find_value(f"Customer No: {value}", "customer") == value


def test_blank_label_before_a_date_does_not_hide_a_later_explicit_identifier():
    assert find_value("Customer No:\n2026-01-15\nCustomer No: C10042", "customer") == "C10042"


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


@pytest.mark.parametrize("text", ["Customer No: A1", "Customer No: A1.", "Customer No: A1;"])
def test_end_of_truncated_text_does_not_delimit_an_identifier(text):
    assert find_value(text, "customer") == "A1"
    assert find_value(text, "customer", truncated=True) is None
    assert find_value(text + " Customer No: B2 ", "customer", truncated=True) == "A1"


def test_text_limit_never_yields_a_shortened_identifier():
    data = pdf.make_pdf([["Customer No: C10042777"]])
    results = {analyze(data, "customer", 5, limit).get("value") for limit in range(1, 40)}
    assert results == {None, "C10042777"}


@pytest.mark.parametrize("line,kept", [("Customer No: C10042777 due", "C10042777"), ("Customer No: C10042777. due", "C10042777.")])
def test_identifier_ending_exactly_at_the_text_limit_is_complete(line, kept):
    limit = len("\n" + line.split(kept)[0] + kept)
    data = pdf.make_pdf([[line]])
    assert analyze(data, "customer", 5, limit)["value"] == "C10042777"
    assert analyze(data, "customer", 5, limit - 1).get("value") is None


def test_label_at_page_end_does_not_take_an_unlabeled_value_from_next_page():
    data = pdf.make_pdf([["Total 4", "Customer No:"], ["C77 due"]])
    assert run(data)["code"] == "no_match"


def test_incremental_page_scan_matches_full_rescan():
    pieces = ["Customer", "Invoice", " ", "\t", "\n", "No", "No.", "Number", "#", "ID", ":",
              "A1", "B2-3", "x", ".", ",", ";", "none", "_", "Customer No", "Invoice #"]
    rng = random.Random(5)
    for _ in range(5000):
        mode = rng.choice(["customer", "invoice"])
        text, resume, value = "", 0, None
        for _ in range(rng.randint(1, 5)):
            text += "\n" + "".join(rng.choice(pieces) for _ in range(rng.randint(0, 8)))
            value, resume = _scan(text, mode, resume, False)
            if value:
                break
        assert value == find_value(text, mode), repr(text)


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


@pytest.mark.parametrize("algorithm", ["AES-128", "AES-256"])
def test_owner_only_aes_is_readable_but_open_passwords_are_rejected(algorithm):
    assert run(pdf.encrypted(user_password="", algorithm=algorithm))["value"] == "C10042"
    assert run(pdf.encrypted(algorithm=algorithm))["code"] == "encrypted"


@pytest.mark.parametrize("failure_stage", ["reader", "decrypt", "page"])
def test_missing_aes_backend_is_not_misreported_as_a_scan(monkeypatch, failure_stage):
    import pypdf
    from pypdf.errors import DependencyError

    class Page:
        def extract_text(self):
            raise DependencyError("Synthetic missing backend")

    class Reader:
        is_encrypted = True
        pages = [Page()]

        def __init__(self, *args, **kwargs):
            if failure_stage == "reader":
                raise DependencyError("Synthetic missing backend")

        def decrypt(self, value):
            if failure_stage == "decrypt":
                raise DependencyError("Synthetic missing backend")
            return 1

    monkeypatch.setattr(pypdf, "PdfReader", Reader)
    assert run(pdf.invoice())["code"] == "unsupported_encryption"


def test_unreadable_page_is_not_reported_as_a_scan_or_renamed_from_later_pages():
    import io
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import EncodedStreamObject, NameObject

    writer = PdfWriter()
    page = writer.add_page(PdfReader(io.BytesIO(pdf.invoice())).pages[0])
    stream = EncodedStreamObject()
    stream._data = b"GG>"
    stream[NameObject("/Filter")] = NameObject("/ASCIIHexDecode")
    page[NameObject("/Contents")] = writer._add_object(stream)
    writer.add_page(PdfReader(io.BytesIO(pdf.invoice())).pages[0])
    buffer = io.BytesIO()
    writer.write(buffer)
    assert run(buffer.getvalue())["code"] == "corrupt"


def test_header_only_pdf_is_corrupt():
    assert run(b"%PDF-1.7\n%%EOF")["code"] == "corrupt"
