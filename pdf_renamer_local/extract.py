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
_HSPACE = r"[^\S\r\n\v\f\x1c-\x1f\x85\u2028\u2029]"
_GAP = re.compile(_HSPACE + "*")
_LABEL = rf"\b{{word}}{_HSPACE}*(?:No\b\.?|Number\b|#|ID\b){_HSPACE}*[:#.]?{_HSPACE}*"

PATTERNS = {
    mode: re.compile(_LABEL.format(word=word), re.IGNORECASE)
    for mode, (_, word) in FIELDS.items()
}
_ANY_LABEL = re.compile("|".join(_LABEL.format(word=word) for _, word in FIELDS.values()), re.IGNORECASE)


def find_value(text: str, mode: str, truncated: bool = False) -> str | None:
    return _scan(text, mode, 0, truncated)[0]


def _scan(text: str, mode: str, start: int, truncated: bool) -> tuple[str | None, int]:
    """Return the first complete value at or after start, and where the next scan must resume.

    Only horizontal whitespace may separate a label from its value. The end of truncated
    text is not a delimiter, because the identifier may continue past it.
    """
    for label in PATTERNS[mode].finditer(text, start):
        start = _GAP.match(text, label.end()).end()
        if start == len(text):
            return None, label.start()
        token = _TOKEN.match(text, start)
        if token is None or _VALUE.fullmatch(token.group()) is None:
            continue
        end = token.end()
        if end == len(text):
            return (None if truncated else token.group()), end
        if text[end].isspace():
            return token.group(), end
        if text[end] in ".,;":
            following = end + 1
            if following == len(text):
                return (None if truncated else token.group()), end
            if text[following].isspace() or _ANY_LABEL.match(text, following):
                return token.group(), end
    return None, len(text)


def analyze(data: bytes, mode: str, max_pages: int, max_text_chars: int) -> dict:
    if not data:
        return {"status": "error", "code": "empty"}
    if b"%PDF-" not in data[:1024]:
        return {"status": "error", "code": "not_pdf"}

    logging.getLogger("pypdf").setLevel(logging.CRITICAL)
    from pypdf import PdfReader
    from pypdf.errors import DependencyError

    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted:
            try:
                if not reader.decrypt(""):
                    return {"status": "error", "code": "encrypted"}
            except DependencyError:
                return {"status": "error", "code": "unsupported_encryption"}
            except Exception:
                return {"status": "error", "code": "encrypted"}
        page_count = len(reader.pages)
    except DependencyError:
        return {"status": "error", "code": "unsupported_encryption"}
    except Exception:
        return {"status": "error", "code": "corrupt"}

    if page_count == 0:
        return {"status": "error", "code": "corrupt"}
    if page_count > max_pages:
        return {"status": "error", "code": "too_many_pages", "pages": page_count}

    text = ""
    resume = 0
    for page in reader.pages:
        try:
            chunk = page.extract_text() or ""
        except DependencyError:
            return {"status": "error", "code": "unsupported_encryption", "pages": page_count}
        except Exception:
            return {"status": "error", "code": "corrupt", "pages": page_count}
        joined = text + "\n" + chunk
        text = joined[:max_text_chars]
        truncated = len(joined) > max_text_chars and not joined[max_text_chars].isspace()
        value, resume = _scan(text, mode, resume, truncated)
        if value:
            return {"status": "ok", "value": value, "pages": page_count}
        if len(text) >= max_text_chars:
            break

    code = "no_match" if text.strip() else "no_text"
    return {"status": "error", "code": code, "pages": page_count}
