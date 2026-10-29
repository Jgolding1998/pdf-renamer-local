"""Isolated parser process.

Protocol: stdin carries frames of a 4 byte big-endian length followed by the PDF
bytes. For every frame one JSON line is written to stdout. EOF ends the process.
"""

from __future__ import annotations

import json
import struct
import sys


def _limit_memory(megabytes: int) -> None:
    try:
        import resource
    except ImportError:
        return
    limit = megabytes * 1024 * 1024
    try:
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    except (ValueError, OSError):
        pass


def _read_exact(stream, size: int) -> bytes | None:
    buf = bytearray()
    while len(buf) < size:
        chunk = stream.read(size - len(buf))
        if not chunk:
            return None
        buf += chunk
    return bytes(buf)


def main(argv: list[str]) -> int:
    options = json.loads(argv[0])
    _limit_memory(options["memory_mb"])
    from pdf_renamer_local.extract import analyze

    stdin, stdout = sys.stdin.buffer, sys.stdout.buffer
    dependency_missing = False
    while True:
        header = _read_exact(stdin, 4)
        if header is None:
            return 0
        (size,) = struct.unpack(">I", header)
        data = _read_exact(stdin, size)
        if data is None:
            return 0
        try:
            result = ({"status": "error", "code": "dependency_missing"} if dependency_missing
                      else analyze(data, options["mode"], options["max_pages"], options["max_text_chars"]))
        except ImportError:
            dependency_missing = True
            result = {"status": "error", "code": "dependency_missing"}
        except MemoryError:
            result = {"status": "error", "code": "failed"}
        del data
        stdout.write(json.dumps(result).encode() + b"\n")
        stdout.flush()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
