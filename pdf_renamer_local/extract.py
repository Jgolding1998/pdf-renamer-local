"""PDF text extraction. Runs inside the isolated worker process."""

from __future__ import annotations

import io
import logging
import re

FIELDS = {
    "customer": ("Customer No", r"Customer"),
    "invoice": ("Invoice No", r"Invoice"),
}

_VALUE = r"(?=[A-Za-z-]*\d)([A-Za-z0-9][A-Za-z0-9-]{0,39})(?![A-Za-z0-9-])"
_LABEL = r"\b{word}\s*(?:No\b\.?|Number\b|#|ID\b)\s*[:#.]?\s*"

PATTERNS = {
    mode: re.compile(_LABEL.format(word=word) + _VALUE, re.IGNORECASE)
    for mode, (_, word) in FIELDS.items()
}


def find_value(text: str, mode: str) -> str | None:
    match = PATTERNS[mode].search(text)
    return match.group(1) if match else None


def analyze(data: bytes, mode: str, max_pages: int, max_text_chars: int) -> dict:
    if not data:
        return {"status": "error", "code": "empty"}
    if b"%PDF-" not in data[:1024]:
        return {"status": "error", "code": "not_pdf"}

    logging.getLogger("pypdf").setLevel(logging.CRITICAL)
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted:
            try:
                if not reader.decrypt(""):
                    return {"status": "error", "code": "encrypted"}
            except Exception:
                return {"status": "error", "code": "encrypted"}
        page_count = len(reader.pages)
    except Exception:
        return {"status": "error", "code": "corrupt"}

    if page_count == 0:
        return {"status": "error", "code": "corrupt"}
    if page_count > max_pages:
        return {"status": "error", "code": "too_many_pages", "pages": page_count}

    text = ""
    for page in reader.pages:
        try:
            chunk = page.extract_text() or ""
        except Exception:
            chunk = ""
        text = (text + "\n" + chunk)[:max_text_chars]
        value = find_value(text, mode)
        if value:
            return {"status": "ok", "value": value, "pages": page_count}
        if len(text) >= max_text_chars:
            break

    code = "no_match" if text.strip() else "no_text"
    return {"status": "error", "code": code, "pages": page_count}
