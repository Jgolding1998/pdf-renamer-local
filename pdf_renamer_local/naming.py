from __future__ import annotations

import re
import unicodedata

PREFIX_RE = re.compile(r"(?:[A-Za-z0-9_][A-Za-z0-9_-]{0,31})?")
STEM_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_-]{0,99}")
MAX_DISPLAY = 120

WINDOWS_RESERVED = frozenset(
    ["CON", "PRN", "AUX", "NUL", "CLOCK$", "CONIN$", "CONOUT$"]
    + [f"COM{i}" for i in range(10)]
    + [f"LPT{i}" for i in range(10)]
)


def valid_prefix(prefix: str) -> bool:
    return PREFIX_RE.fullmatch(prefix) is not None


def safe_stem(prefix: str, value: str) -> str | None:
    """Build an allowlisted file stem, or None when no safe stem exists."""
    if not valid_prefix(prefix):
        return None
    stem = prefix + value
    if not STEM_RE.fullmatch(stem):
        return None
    if stem.upper() in WINDOWS_RESERVED:
        stem = "_" + stem
    return stem


def assign_names(stems: list[str | None]) -> list[str | None]:
    """Give every stem a unique .pdf name; duplicates get -2, -3, ... in input order.

    Comparison is case-insensitive so the archive extracts cleanly on Windows and macOS.
    """
    taken: set[str] = set()
    names: list[str | None] = []
    for stem in stems:
        if stem is None:
            names.append(None)
            continue
        candidate, n = stem, 2
        while candidate.casefold() in taken:
            candidate = f"{stem}-{n}"
            n += 1
        taken.add(candidate.casefold())
        names.append(candidate + ".pdf")
    return names


def display_name(raw: str | None) -> str:
    """Client filename reduced to a short label. Never used as a path."""
    if not raw:
        return "(unnamed)"
    base = re.split(r"[\\/]", raw)[-1]
    cleaned = "".join(ch for ch in base if unicodedata.category(ch)[0] != "C").strip()
    return (cleaned or "(unnamed)")[:MAX_DISPLAY]
