from __future__ import annotations

import argparse
import ipaddress
import os
import sys
from dataclasses import replace

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

    try:
        address = ipaddress.ip_address(args.host)
    except ValueError:
        address = None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        print("IPv4-mapped IPv6 bind addresses are unsupported. Use 127.0.0.1 or ::1 for loopback.", file=sys.stderr)
        return 2

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
        if is_loopback(args.host) and not os.environ.get("PDFR_ALLOWED_HOSTS"):
            settings = replace(settings, allowed_hosts=tuple(dict.fromkeys((*settings.allowed_hosts, args.host.lower()))))
        if not 1 <= args.port <= 65535:
            raise ValueError("Port must be between 1 and 65535.")
    except ValueError as exc:
        print(f"Invalid configuration: {exc}", file=sys.stderr)
        return 2
    url_host = f"[{args.host}]" if ":" in args.host else args.host
    print(f"PDF Renamer Local on http://{url_host}:{args.port}/ (no external processing services)")
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
