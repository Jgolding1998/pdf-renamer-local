"""Exercise busy controls and stale responses in a headless browser with synthetic data."""

import argparse
import io
import json
import socket
import sys
import threading
import time
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import uvicorn
from playwright.sync_api import sync_playwright
from pdf_renamer_local.app import create_app
from pdf_renamer_local.config import Settings


def main():
    args = argparse.ArgumentParser()
    args.add_argument("--channel", default="msedge")
    channel = args.parse_args().channel
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(Settings()), host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("Test server did not start")
        time.sleep(0.02)
    plan = {"plan_id": "a" * 64, "summary": {"renamed": 1, "failed": 0, "total": 1},
            "items": [{"index": 0, "status": "ok", "original": "sample.pdf",
                       "new_name": "Customer-C1.pdf", "renamed_duplicate": False}]}
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=channel, headless=True)
            page = browser.new_page(accept_downloads=True)
            pending = []
            page.route("**/api/*", lambda route: pending.append(route))
            page.goto(f"http://127.0.0.1:{port}/")
            page.set_input_files("#files", {"name": "sample.pdf", "mimeType": "application/pdf", "buffer": b"%PDF-synthetic"})

            for value in ("-", "-x", "--"):
                page.fill("#prefix", value)
                assert page.locator("#prefix").get_attribute("aria-invalid") == "true"
                assert not page.evaluate("document.querySelector('#prefix').checkValidity()")
                page.click("#preview")
                assert not pending
            for value in ("", "_", "Customer-"):
                page.fill("#prefix", value)
                assert page.locator("#prefix").get_attribute("aria-invalid") == "false"
                assert page.evaluate("document.querySelector('#prefix').checkValidity()")

            def assert_busy():
                for selector in ("#prefix", "#files", "#clear", "#preview", "#download"):
                    assert page.locator(selector).is_disabled(), selector
                assert page.locator('input[name="mode"]:disabled').count() == 2
                assert page.locator("#drop").get_attribute("aria-disabled") == "true"

            page.click("#preview")
            page.wait_for_function("document.querySelector('#rename-form').getAttribute('aria-busy') === 'true'")
            assert_busy()
            page.evaluate("""() => {
                document.querySelector('#clear').dispatchEvent(new Event('click'));
                const transfer = new DataTransfer();
                transfer.items.add(new File(['synthetic'], 'ignored.pdf'));
                document.querySelector('#drop').dispatchEvent(new DragEvent('drop', {dataTransfer: transfer, bubbles:true}));
                const prefix = document.querySelector('#prefix');
                prefix.value = 'Changed-'; prefix.dispatchEvent(new Event('input'));
            }""")
            assert page.locator("#file-list li").count() == 1
            assert len(pending) == 1
            pending.pop(0).fulfill(status=200, content_type="application/json", body=json.dumps(plan))
            page.wait_for_function("document.querySelector('#status').textContent.includes('changed during the preview')")
            assert page.locator("#download").is_disabled()
            assert page.locator("#prefix").is_enabled()

            page.fill("#prefix", "Customer-")
            page.click("#preview")
            page.wait_for_function("document.querySelector('#status').textContent.includes('Reading')")
            page.wait_for_timeout(30)
            pending.pop(0).fulfill(status=200, content_type="application/json", body=json.dumps(plan))
            page.wait_for_function("!document.querySelector('#download').disabled")
            page.click("#download")
            page.wait_for_function("document.querySelector('#status').textContent.includes('Building ZIP')")
            assert_busy()
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w") as archive:
                archive.writestr("Customer-C1.pdf", b"%PDF-synthetic")
            page.wait_for_timeout(30)
            with page.expect_download():
                pending.pop(0).fulfill(status=200, content_type="application/zip", headers={"x-plan-id": plan["plan_id"]}, body=buffer.getvalue())
            page.wait_for_function("document.querySelector('#status').textContent.includes('Downloaded')")
            assert page.locator("#prefix").is_enabled()
            browser.close()
        print("Browser regression passed: busy controls, ignored clear/drop, stale preview rejection, ZIP completion.")
    finally:
        server.should_exit = True
        thread.join(timeout=5)


if __name__ == "__main__":
    main()
