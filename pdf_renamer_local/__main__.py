from __future__ import annotations

import argparse
import ipaddress
import os
import sys

import uvicorn

from .app import create_app
from .config import Settings


def is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pdf_renamer_local", description="Run the local PDF renamer.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)

    if not is_loopback(args.host) and os.environ.get("PDFR_ALLOW_NON_LOOPBACK") != "1":
        print(
            "Refusing to bind a non-loopback address. This app has no authentication.\n"
            "Put it behind an authenticating reverse proxy and set PDFR_ALLOW_NON_LOOPBACK=1 "
            "and PDFR_ALLOWED_HOSTS to proceed. See SECURITY.md.",
            file=sys.stderr,
        )
        return 2

    try:
        settings = Settings.from_env()
        if not 1 <= args.port <= 65535:
            raise ValueError("Port must be between 1 and 65535.")
    except ValueError as exc:
        print(f"Invalid configuration: {exc}", file=sys.stderr)
        return 2
    print(f"PDF Renamer Local on http://{args.host}:{args.port}/ (no external processing services)")
    uvicorn.run(
        create_app(settings),
        host=args.host,
        port=args.port,
        server_header=False,
        proxy_headers=False,
        limit_concurrency=32,
        timeout_keep_alive=5,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
