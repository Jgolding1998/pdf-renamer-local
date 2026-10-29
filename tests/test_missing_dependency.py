import io
import json
import struct
from types import SimpleNamespace
from unittest.mock import Mock

from pdf_renamer_local import extract, worker
from pdf_renamer_local.config import Settings
from pdf_renamer_local.plan import messages


def test_missing_parser_dependency_returns_bounded_replies_without_restart(monkeypatch):
    source = io.BytesIO((struct.pack(">I", 4) + b"%PDF") * 2)
    destination = io.BytesIO()
    analyze = Mock(side_effect=ModuleNotFoundError("private-installation-canary"))
    monkeypatch.setattr(extract, "analyze", analyze)
    monkeypatch.setattr(worker, "_limit_memory", lambda size: None)
    monkeypatch.setattr(worker, "sys", SimpleNamespace(stdin=SimpleNamespace(buffer=source),
                                                     stdout=SimpleNamespace(buffer=destination)))
    options = {"memory_mb": 768, "mode": "invoice", "max_pages": 10, "max_text_chars": 1000}
    assert worker.main([json.dumps(options)]) == 0
    replies = [json.loads(line) for line in destination.getvalue().splitlines()]
    assert replies == [{"status": "error", "code": "dependency_missing"}] * 2
    assert analyze.call_count == 1
    assert b"private-installation-canary" not in destination.getvalue()


def test_dependency_error_explains_the_supported_installation():
    assert "virtual environment" in messages(Settings(), "invoice")["dependency_missing"]
