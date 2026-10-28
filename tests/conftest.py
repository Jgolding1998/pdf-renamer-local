from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from pdf_renamer_local.app import create_app
from pdf_renamer_local.config import MB, Settings

BASE = "http://127.0.0.1:8000"
ORIGIN = BASE


class Session:
    def __init__(self, client: TestClient) -> None:
        self.client = client
        page = client.get("/")
        self.token = re.search(r'name="csrf-token" content="([^"]+)"', page.text).group(1)

    def post(self, path: str, files: list[tuple[str, bytes]], headers: dict | None = None, **fields):
        data = {"mode": "customer", "prefix": "Customer-", **fields}
        parts = [("files", (name, body, "application/pdf")) for name, body in files]
        merged = {"Origin": ORIGIN, "X-CSRF-Token": self.token, **(headers or {})}
        return self.client.post(path, data=data, files=parts, headers=merged)

    def preview(self, files, **fields):
        return self.post("/api/preview", files, **fields)

    def zip(self, files, plan_id, **fields):
        return self.post("/api/zip", files, plan_id=plan_id, **fields)


def make_session(settings: Settings | None = None) -> Session:
    return Session(TestClient(create_app(settings or Settings()), base_url=BASE))


@pytest.fixture
def session() -> Session:
    return make_session()


@pytest.fixture
def small_limits() -> Settings:
    return Settings(max_files=3, max_file_bytes=1 * MB, max_total_bytes=2 * MB, max_pages=5)
