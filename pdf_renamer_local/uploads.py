"""In-memory streaming multipart parser with hard limits enforced while reading."""

from __future__ import annotations

from dataclasses import dataclass, field
import re

from python_multipart.exceptions import FormParserError
from python_multipart.multipart import MultipartParser, parse_options_header
from starlette.requests import ClientDisconnect, Request

from .config import Settings

MAX_FIELD_BYTES = 1024
MAX_FIELDS = 8
MAX_PART_HEADERS = 8
MAX_HEADER_NAME_BYTES = 64
MAX_HEADER_VALUE_BYTES = 4096
MAX_PART_HEADER_BYTES = 8192


class UploadError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass
class UploadedFile:
    filename: str
    data: bytearray = field(default_factory=bytearray)
    size: int = 0
    oversized: bool = False


@dataclass
class _Part:
    name: str = ""
    file: UploadedFile | None = None
    value: bytearray = field(default_factory=bytearray)
    disposition: bytes = b""


async def read_upload(request: Request, settings: Settings) -> tuple[dict[str, str], list[UploadedFile]]:
    content_type = request.headers.get("content-type", "")
    if len(content_type) > 512:
        raise UploadError(400, "Multipart Content-Type too long.")
    kind, params = parse_options_header(content_type)
    if kind != b"multipart/form-data" or b"boundary" not in params:
        raise UploadError(415, "Expected a multipart/form-data upload.")
    boundary = params[b"boundary"]
    if not 1 <= len(boundary) <= 70 or not re.fullmatch(rb"[ -~]+", boundary) or boundary.endswith(b" "):
        raise UploadError(400, "Invalid multipart boundary.")
    length = request.headers.get("content-length")
    if length is not None:
        if len(length) > 20 or not length.isdigit():
            raise UploadError(400, "Invalid Content-Length.")
        if int(length) > settings.max_body_bytes:
            raise UploadError(413, "Upload exceeds the total size limit.")

    fields: dict[str, str] = {}
    files: list[UploadedFile] = []
    state = {"part": _Part(), "header_name": b"", "header_value": b"", "parts": 0,
             "headers": set(), "header_bytes": 0, "ended": False}
    total = {"bytes": 0}

    def on_part_begin() -> None:
        state["parts"] += 1
        if state["parts"] > settings.max_files + MAX_FIELDS:
            raise UploadError(413, "Too many multipart parts.")
        state["part"] = _Part()
        state["headers"] = set()
        state["header_bytes"] = 0

    def on_header_field(data: bytes, start: int, end: int) -> None:
        state["header_name"] += data[start:end]
        state["header_bytes"] += end - start
        if len(state["header_name"]) > MAX_HEADER_NAME_BYTES or state["header_bytes"] > MAX_PART_HEADER_BYTES:
            raise UploadError(400, "Multipart header too long.")

    def on_header_value(data: bytes, start: int, end: int) -> None:
        state["header_value"] += data[start:end]
        state["header_bytes"] += end - start
        if len(state["header_value"]) > MAX_HEADER_VALUE_BYTES or state["header_bytes"] > MAX_PART_HEADER_BYTES:
            raise UploadError(400, "Multipart header too long.")

    def on_header_end() -> None:
        name = state["header_name"].lower()
        if not name or name in state["headers"] or len(state["headers"]) >= MAX_PART_HEADERS:
            raise UploadError(400, "Duplicate or excessive multipart headers.")
        state["headers"].add(name)
        if name == b"content-disposition":
            state["part"].disposition = state["header_value"]
        state["header_name"] = b""
        state["header_value"] = b""

    def on_headers_finished() -> None:
        part = state["part"]
        disposition, options = parse_options_header(part.disposition)
        name = options.get(b"name")
        if disposition != b"form-data" or name is None or len(name) > 32:
            raise UploadError(400, "Malformed multipart part.")
        part.name = name.decode("utf-8", "replace")
        if b"filename" in options:
            if part.name != "files":
                raise UploadError(400, "Unexpected file field.")
            if len(files) >= settings.max_files:
                raise UploadError(413, f"Too many files. The limit is {settings.max_files}.")
            if len(options[b"filename"]) > 512:
                raise UploadError(400, "Filename too long.")
            part.file = UploadedFile(filename=options[b"filename"].decode("utf-8", "replace"))
            files.append(part.file)
        else:
            if part.name not in {"mode", "prefix", "plan_id"}:
                raise UploadError(400, "Unexpected form field.")
            if part.name in fields:
                raise UploadError(400, "Duplicate form field.")
            if len(fields) >= MAX_FIELDS:
                raise UploadError(400, "Too many form fields.")

    def on_part_data(data: bytes, start: int, end: int) -> None:
        part = state["part"]
        chunk = data[start:end]
        if part.file is None:
            part.value += chunk
            if len(part.value) > MAX_FIELD_BYTES:
                raise UploadError(413, "Form field too large.")
            return
        upload = part.file
        upload.size += len(chunk)
        total["bytes"] += len(chunk)
        if total["bytes"] > settings.max_total_bytes:
            raise UploadError(413, "Upload exceeds the total size limit.")
        if upload.size > settings.max_file_bytes:
            upload.oversized = True
            upload.data = bytearray()
        else:
            upload.data += chunk

    def on_part_end() -> None:
        part = state["part"]
        if part.file is None and part.name:
            fields[part.name] = part.value.decode("utf-8", "replace")

    def on_end() -> None:
        state["ended"] = True

    parser = MultipartParser(
        boundary,
        {
            "on_part_begin": on_part_begin,
            "on_part_data": on_part_data,
            "on_part_end": on_part_end,
            "on_header_field": on_header_field,
            "on_header_value": on_header_value,
            "on_header_end": on_header_end,
            "on_headers_finished": on_headers_finished,
            "on_end": on_end,
        },
        max_header_count=MAX_PART_HEADERS,
        max_header_size=MAX_PART_HEADER_BYTES,
    )
    received = 0
    try:
        async for chunk in request.stream():
            received += len(chunk)
            if received > settings.max_body_bytes:
                raise UploadError(413, "Upload exceeds the total size limit.")
            parser.write(chunk)
        parser.finalize()
        if not state["ended"] or (length is not None and received != int(length)):
            raise UploadError(400, "Incomplete multipart upload.")
    except FormParserError as exc:
        raise UploadError(400, "Malformed multipart upload.") from exc
    except ClientDisconnect as exc:
        raise UploadError(400, "Upload disconnected before completion.") from exc
    return fields, files
