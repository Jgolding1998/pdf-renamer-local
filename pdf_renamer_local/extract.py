"""PDF text extraction. Runs inside the isolated worker process."""

from __future__ import annotations

import io
import logging
import re

FIELDS = {
    "customer": ("Customer No", r"Customer"),
    "invoice": ("Invoice No", r"Invoice"),
}

_VALUE = re.compile(r"(?=[A-Za-z-]*\d)[A-Za-z0-9][A-Za-z0-9-]{0,39}")
_TOKEN = re.compile(r"[A-Za-z0-9-]{1,41}")
_GAP = re.compile(r"\s*")
_LABEL = r"\b{word}[ \t]*(?:No\b\.?|Number\b|#|ID\b)[ \t]*[:#.]?[ \t]*"

PATTERNS = {
    mode: re.compile(_LABEL.format(word=word), re.IGNORECASE)
    for mode, (_, word) in FIELDS.items()
}
_ANY_LABEL = re.compile("|".join(_LABEL.format(word=word) for _, word in FIELDS.values()), re.IGNORECASE)


def find_value(text: str, mode: str) -> str | None:
    for label in PATTERNS[mode].finditer(text):
        start = _GAP.match(text, label.end()).end()
        token = _TOKEN.match(text, start)
        if token is None or _VALUE.fullmatch(token.group()) is None:
            continue
        end = token.end()
        if end == len(text) or text[end].isspace():
            return token.group()
        if text[end] in ".,;":
            following = end + 1
            if following == len(text) or text[following].isspace() or _ANY_LABEL.match(text, following):
                return token.group()
    return None


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
