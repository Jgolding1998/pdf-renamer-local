"""Capture desktop and mobile screenshots of a preview built from synthetic PDFs.

Needs Playwright (pip install -r requirements-screenshot.txt) and either a
Playwright Chromium (python -m playwright install chromium) or a local Edge or
Chrome passed with --channel msedge / --channel chrome.
"""

from __future__ import annotations

import argparse
import socket
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import uvicorn  # noqa: E402

from pdf_renamer_local.app import create_app  # noqa: E402
from pdf_renamer_local.config import Settings  # noqa: E402
from scripts.make_sample_pdfs import SAMPLES  # noqa: E402


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def check_download(page) -> None:
    """Click Download ZIP in the real UI and confirm the archive matches the preview table."""
    expected = page.locator("#plan-rows td.new code").all_text_contents()
    with page.expect_download() as info:
        page.click("#download")
    with zipfile.ZipFile(info.value.path()) as archive:
        names = archive.namelist()
    if names != expected:
        raise SystemExit(f"ZIP {names} does not match preview {expected}")
    print(f"Browser download matches preview: {', '.join(names)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(ROOT / "docs"))
    parser.add_argument("--channel", default=None, help="msedge or chrome to use an installed browser")
    args = parser.parse_args()

    from playwright.sync_api import sync_playwright

    port = free_port()
    server = uvicorn.Server(uvicorn.Config(create_app(Settings()), host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp, sync_playwright() as pw:
        paths = []
        for name, build in SAMPLES.items():
            path = Path(tmp) / name
            path.write_bytes(build())
            paths.append(str(path))
        browser = pw.chromium.launch(channel=args.channel) if args.channel else pw.chromium.launch()
        for label, viewport in (("desktop", {"width": 1280, "height": 900}), ("mobile", {"width": 390, "height": 844})):
            page = browser.new_page(viewport=viewport, color_scheme="light")
            page.goto(f"http://127.0.0.1:{port}/")
            page.set_input_files("#files", paths)
            page.click("#preview")
            page.wait_for_selector("#plan-rows tr")
            page.screenshot(path=str(out / f"screenshot-{label}.png"), full_page=True)
            if label == "desktop":
                check_download(page)
            page.close()
        browser.close()
    server.should_exit = True
    print(f"Saved screenshots to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
