from __future__ import annotations

import os
import ipaddress
import math
import re
from dataclasses import dataclass, field

MB = 1024 * 1024
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return int(raw) if raw else default


def _float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    return float(raw) if raw else default


@dataclass(frozen=True)
class Settings:
    max_files: int = 50
    max_file_bytes: int = 10 * MB
    max_total_bytes: int = 50 * MB
    max_pages: int = 50
    max_text_chars: int = 200_000
    file_timeout: float = 10.0
    request_timeout: float = 60.0
    upload_timeout: float = 15.0
    max_parallel_jobs: int = 2
    worker_memory_mb: int = 768
    allowed_hosts: tuple[str, ...] = field(default=LOOPBACK_HOSTS)

    def __post_init__(self) -> None:
        bounds = {
            "max_files": (1, 100),
            "max_file_bytes": (1, 100 * MB),
            "max_total_bytes": (self.max_file_bytes, 500 * MB),
            "max_pages": (1, 500),
            "max_text_chars": (1, 1_000_000),
            "max_parallel_jobs": (1, 32),
            "worker_memory_mb": (128, 2048),
        }
        for name, (minimum, maximum) in bounds.items():
            value = getattr(self, name)
            if type(value) is not int or not minimum <= value <= maximum:
                raise ValueError(f"{name} must be an integer between {minimum} and {maximum}.")
        for name, maximum in (("file_timeout", 120), ("request_timeout", 600), ("upload_timeout", 120)):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or not 0 < value <= maximum:
                raise ValueError(f"{name} must be positive, finite and at most {maximum} seconds.")
        if not isinstance(self.allowed_hosts, tuple) or not self.allowed_hosts:
            raise ValueError("allowed_hosts must be a nonempty tuple of hostnames or IP addresses.")
        for host in self.allowed_hosts:
            if not isinstance(host, str) or host != host.lower() or not host:
                raise ValueError("allowed_hosts must contain lowercase hostnames or IP addresses.")
            try:
                ipaddress.ip_address(host)
            except ValueError:
                if len(host) > 253 or not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host):
                    raise ValueError("allowed_hosts must not include ports, wildcards or URL components.")

    @property
    def max_body_bytes(self) -> int:
        return self.max_total_bytes + 256 * 1024

    @classmethod
    def from_env(cls) -> "Settings":
        hosts = os.environ.get("PDFR_ALLOWED_HOSTS")
        return cls(
            max_files=_int("PDFR_MAX_FILES", cls.max_files),
            max_file_bytes=_int("PDFR_MAX_FILE_MB", cls.max_file_bytes // MB) * MB,
            max_total_bytes=_int("PDFR_MAX_TOTAL_MB", cls.max_total_bytes // MB) * MB,
            max_pages=_int("PDFR_MAX_PAGES", cls.max_pages),
            file_timeout=_float("PDFR_FILE_TIMEOUT", cls.file_timeout),
            request_timeout=_float("PDFR_REQUEST_TIMEOUT", cls.request_timeout),
            upload_timeout=_float("PDFR_UPLOAD_TIMEOUT", cls.upload_timeout),
            max_text_chars=_int("PDFR_MAX_TEXT_CHARS", cls.max_text_chars),
            max_parallel_jobs=_int("PDFR_MAX_PARALLEL_JOBS", cls.max_parallel_jobs),
            worker_memory_mb=_int("PDFR_WORKER_MEMORY_MB", cls.worker_memory_mb),
            allowed_hosts=tuple(h.strip().lower() for h in hosts.split(",") if h.strip())
            if hosts
            else LOOPBACK_HOSTS,
        )
