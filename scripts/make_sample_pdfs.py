"""Write a folder of synthetic sample PDFs for trying the app by hand."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests import pdf_factory as pdf  # noqa: E402

SAMPLES = {
    "march-statement.pdf": lambda: pdf.invoice("C10042", "INV-20931"),
    "march-statement-copy.pdf": lambda: pdf.invoice("C10042", "INV-20932"),
    "april-invoice.pdf": lambda: pdf.invoice("C20077", "INV-21004"),
    "scanned-receipt.pdf": pdf.scanned,
    "password-protected.pdf": pdf.encrypted,
    "damaged-download.pdf": pdf.corrupt,
    "packing-slip.pdf": lambda: pdf.make_pdf([["Packing slip", "Thank you for your order"]]),
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", nargs="?", default="samples", help="output folder (default: samples)")
    out = Path(parser.parse_args().out)
    out.mkdir(parents=True, exist_ok=True)
    for name, build in SAMPLES.items():
        (out / name).write_bytes(build())
    print(f"Wrote {len(SAMPLES)} synthetic PDFs to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
