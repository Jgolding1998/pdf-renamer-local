"""Turns uploads plus parser results into a deterministic rename plan and ZIP."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile

from .config import MB, Settings
from .extract import FIELDS
from .naming import assign_names, display_name, safe_stem
from .runner import run_batch
from .uploads import UploadedFile

ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


def messages(settings: Settings, mode: str) -> dict[str, str]:
    label = FIELDS[mode][0]
    return {
        "empty": "The file is empty.",
        "not_pdf": "This is not a PDF file.",
        "too_large": f"The file is larger than the {settings.max_file_bytes // MB} MB per-file limit.",
        "encrypted": "The PDF is password protected. Remove the password and try again.",
        "corrupt": "The PDF could not be read. It may be damaged or incomplete.",
        "too_many_pages": f"The PDF has more than {settings.max_pages} pages.",
        "no_text": "No selectable text was found. Scanned PDFs need OCR before they can be renamed.",
        "no_match": f"No {label} was found in the text.",
        "invalid_name": f"The {label} found cannot form a safe file name.",
        "timeout": "Reading this PDF took too long and was stopped.",
        "budget": "Skipped because the request ran out of processing time.",
        "failed": "The PDF parser stopped unexpectedly on this file.",
    }


def build_plan(files: list[UploadedFile], mode: str, prefix: str, settings: Settings) -> dict:
    precheck: list[str | None] = []
    for f in files:
        if f.oversized:
            precheck.append("too_large")
        elif f.size == 0:
            precheck.append("empty")
        elif b"%PDF-" not in bytes(f.data[:1024]):
            precheck.append("not_pdf")
        else:
            precheck.append(None)

    to_parse = [memoryview(f.data) for f, code in zip(files, precheck) if code is None]
    parsed = iter(run_batch(to_parse, mode, settings) if to_parse else [])

    results: list[dict] = []
    stems: list[str | None] = []
    for code in precheck:
        result = {"status": "error", "code": code} if code else next(parsed)
        stem = None
        if result.get("status") == "ok":
            stem = safe_stem(prefix, str(result.get("value", "")))
            if stem is None:
                result = {"status": "error", "code": "invalid_name"}
        results.append(result)
        stems.append(stem)

    names = assign_names(stems)
    text = messages(settings, mode)
    items = []
    for index, (f, result, name) in enumerate(zip(files, results, names)):
        ok = name is not None
        item = {
            "index": index,
            "original": display_name(f.filename),
            "size": f.size,
            "sha256": hashlib.sha256(f.data).hexdigest() if not f.oversized else None,
            "status": "ok" if ok else "error",
            "new_name": name,
            "value": result.get("value") if ok else None,
            "renamed_duplicate": ok and name != f"{stems[index]}.pdf",
        }
        if not ok:
            item["code"] = result.get("code", "failed")
            item["message"] = text.get(item["code"], text["failed"])
        items.append(item)

    fingerprint = json.dumps(
        {
            "mode": mode,
            "prefix": prefix,
            "items": [[i["sha256"], i["size"], i["status"], i["new_name"]] for i in items],
        },
        separators=(",", ":"),
    )
    renamed = sum(1 for i in items if i["status"] == "ok")
    return {
        "plan_id": hashlib.sha256(fingerprint.encode()).hexdigest(),
        "mode": mode,
        "field_label": FIELDS[mode][0],
        "prefix": prefix,
        "summary": {"total": len(items), "renamed": renamed, "failed": len(items) - renamed},
        "items": items,
    }


def build_zip(files: list[UploadedFile], plan: dict) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for item in plan["items"]:
            if item["status"] != "ok":
                continue
            info = zipfile.ZipInfo(item["new_name"], date_time=ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, files[item["index"]].data)
    return buffer.getvalue()
