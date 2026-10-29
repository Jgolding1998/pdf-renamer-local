"""Builds small synthetic PDFs so tests never depend on real documents."""

from __future__ import annotations

import io


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _content(lines: list[str], draw_box: bool) -> bytes:
    ops = []
    if draw_box:
        ops.append("0.6 g 72 72 468 648 re f")
    if lines:
        ops.append("BT /F1 12 Tf 16 TL 72 720 Td")
        for line in lines:
            ops.append(f"({_escape(line)}) Tj T*")
        ops.append("ET")
    return "\n".join(ops).encode("latin-1")


def make_pdf(pages: list[list[str]], draw_box: bool = False) -> bytes:
    """One inner list of text lines per page. An empty list gives a page without text."""
    objects: dict[int, bytes] = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        3: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    }
    kids = []
    next_id = 4
    for lines in pages:
        page_id, content_id = next_id, next_id + 1
        next_id += 2
        stream = _content(lines, draw_box)
        objects[content_id] = b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream)
        objects[page_id] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content_id} 0 R >>"
        ).encode()
        kids.append(page_id)
    refs = " ".join(f"{k} 0 R" for k in kids)
    objects[2] = f"<< /Type /Pages /Kids [{refs}] /Count {len(kids)} >>".encode()

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = {}
    for num in range(1, next_id):
        offsets[num] = len(out)
        out += f"{num} 0 obj\n".encode() + objects[num] + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {next_id}\n0000000000 65535 f \n".encode()
    for num in range(1, next_id):
        out += f"{offsets[num]:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {next_id} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def invoice(customer: str = "C10042", invoice_no: str = "INV-20931", extra: list[str] | None = None) -> bytes:
    lines = [
        "Example Supply Co.",
        f"Invoice No: {invoice_no}",
        "Date: 2026-01-15",
        f"Customer No: {customer}",
        "Customer Name: Sample Customer",
        "Total due: 120.00",
    ]
    return make_pdf([lines + (extra or [])])


def scanned() -> bytes:
    return make_pdf([[]], draw_box=True)


def encrypted(user_password: str = "secret", algorithm: str = "RC4-128") -> bytes:
    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(invoice())))
    writer.encrypt(user_password=user_password, owner_password="owner", algorithm=algorithm)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def corrupt() -> bytes:
    good = invoice()
    return good[: len(good) // 3] + b"\x00garbage\xff" * 20


def many_pages(count: int) -> bytes:
    return make_pdf([[f"Page {n}"] for n in range(count)])


def kerned_customer(suffix: str = "42") -> bytes:
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import DecodedStreamObject, NameObject

    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(make_pdf([[]]))))
    stream = DecodedStreamObject()
    stream.set_data(("BT /F1 12 Tf 72 720 Td [(Customer No: C100) -150 (" + _escape(suffix) + ")] TJ ET").encode("ascii"))
    writer.pages[0][NameObject("/Contents")] = writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()
