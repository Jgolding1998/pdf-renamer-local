"""Run only from the isolated installed-wheel check, with fake processing."""
import hashlib
from importlib import import_module, resources
from importlib.metadata import distribution
import io
import json
from pathlib import Path
import re
import sys
import time
from unittest.mock import patch
import zipfile

SOURCE = Path(sys.argv[1]).resolve()
CHECKS = []


def check(condition, name):
    if not condition:
        raise AssertionError(name)
    CHECKS.append(name)


def installed(package, assets):
    module = import_module(package)
    location = Path(module.__file__).resolve()
    check(location.is_relative_to(Path(sys.prefix).resolve()) and not location.is_relative_to(SOURCE),
          "imports from installed site-packages outside checkout")
    files = resources.files(package)
    metadata = distribution(package.replace("_", "-"))
    dependencies = {item.split("==", 1)[0] for item in metadata.requires or []}
    check(dependencies == {"fastapi", "starlette", "uvicorn", "python-multipart", "pypdf", "cryptography"}, "installed wheel declares every direct runtime dependency")
    hashes = {}
    for asset in assets:
        data = files.joinpath(asset).read_bytes()
        check(bool(data), "packaged resource " + asset)
        hashes[asset] = hashlib.sha256(data).hexdigest()
    return hashes


def headers(response):
    check(response.headers.get("cache-control") == "no-store", "responses are not cached")
    check("frame-ancestors 'none'" in response.headers.get("content-security-policy", ""),
          "frame protection retained")


from fastapi.testclient import TestClient
from pdf_renamer_local.app import create_app
from pdf_renamer_local import plan as plan_module

hashes = installed("pdf_renamer_local", ("index.html", "static/app.js", "static/styles.css"))
payload = b"%PDF-1.4\nSYNTHETIC-NEVER-PARSED\n%%EOF"
calls = []


def fake_parser(documents, mode, settings):
    calls.append(mode)
    check(all(bytes(document) == payload for document in documents), "fake parser receives only synthetic bytes")
    return [{"status": "ok", "value": "C10042"} for _ in documents]


with patch.object(plan_module, "run_batch", fake_parser), TestClient(create_app(), base_url="http://127.0.0.1:8000") as client:
    page = client.get("/")
    check(page.status_code == 200, "installed homepage works")
    headers(page)
    check(client.get("/static/app.js").status_code == client.get("/static/styles.css").status_code == 200,
          "installed static routes work")
    check(client.get("/", headers={"Host": "foreign.invalid"}).status_code == 400, "foreign host refused")
    token = re.search(r'name="csrf-token" content="([^"]+)"', page.text).group(1)
    parts = [("files", ("../../CON.pdf", payload, "application/pdf"))]
    fields = {"mode": "customer", "prefix": "Customer-"}
    check(client.post("/api/preview", data=fields, files=parts).status_code == 403 and calls == [],
          "unauthorized preview refuses before parser")
    client.headers.update({"Origin": "http://127.0.0.1:8000", "X-CSRF-Token": token})
    response = client.post("/api/preview", data=fields, files=parts)
    check(response.status_code == 200 and len(calls) == 1, "fake preview completes without parsing PDFs")
    preview = response.json()
    check(client.post("/api/zip", data={**fields, "plan_id": "0" * 64}, files=parts).status_code == 409,
          "stale preview id cannot authorize export")
    exported = client.post("/api/zip", data={**fields, "plan_id": preview["plan_id"]}, files=parts)
    check(exported.status_code == 200, "matching preview authorizes export")
    with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
        check(archive.namelist() == ["Customer-C10042.pdf"] and archive.read("Customer-C10042.pdf") == payload,
              "safe archive name preserves exact uploaded bytes")

print(json.dumps({"installed": True, "checks": CHECKS, "resources_sha256": hashes,
                  "native_tools_executed": False, "network_requests": False}))

