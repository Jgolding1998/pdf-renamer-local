# Security

## Intended use

PDF Renamer Local is a single-user tool that runs on your own computer and listens on the loopback interface only. It has **no login and no password**, on purpose. A shared password in a web form gives a false sense of protection, so there isn't one.

The CLI refuses to bind a non-loopback address unless you set `PDFR_ALLOW_NON_LOOPBACK=1`.

## If you deploy it anyway

Do not expose it directly. Before serving it to anyone else:

1. Put it behind a reverse proxy that enforces real authentication (SSO, OIDC, mTLS or similar) and terminates TLS.
2. Set `PDFR_ALLOWED_HOSTS` to the public hostname(s) the proxy forwards. Requests with any other `Host` header are rejected.
3. Keep the proxy's request body limit at or below `PDFR_MAX_TOTAL_MB` and set its own read timeout. The app also enforces `PDFR_UPLOAD_TIMEOUT` and rejects saturated uploads before reading their bodies.
4. Run it as an unprivileged user, ideally in a container with a memory limit.

## What the app defends against

- **DNS rebinding and other hostnames:** every request must carry an allowed `Host` header (loopback names by default).
- **Cross-site requests:** POST endpoints require an `Origin` that matches the `Host`, reject `Sec-Fetch-Site` other than `same-origin`, and require a CSRF token sent both as a `SameSite=Strict`, `HttpOnly` cookie and as an `X-CSRF-Token` header. No CORS headers are ever sent, and the custom header forces a preflight that the server does not approve.
- **Hostile PDFs:** parsing happens in a separate Python process with per-file and per-request time limits, a page limit, a text length cap, and (on POSIX) a memory cap. A stuck or crashing parser is killed.
- **Worker boundary:** pipe input and replies have deadlines, output is bounded, and inherited environment variables are restricted. Process isolation does not prevent a compromised parser from accessing the filesystem or network as the current user. Use an OS sandbox or a restricted container for hostile documents.
- **Oversized uploads:** total body size, per-file size, file count, field count and header sizes are enforced while the body streams in.
- **Resource admission:** a processing slot is reserved before reading the body and retained through sending the result. Extra uploads receive `503`; malformed framing, duplicate security headers, incomplete multipart bodies, and slow uploads are rejected.
- **Path tricks:** uploaded filenames are never used to build paths or ZIP entries. Output names come from a strict character allowlist, avoid Windows reserved device names, and are deduplicated case-insensitively.
- **Script injection:** the UI renders all server data with `textContent`, and a strict Content Security Policy blocks inline scripts.

## Data handling

- Uploads live in memory for the duration of one request and are not written to disk, cached or stored.
- The parser process receives bytes over a pipe and exits when the request ends.
- Logs contain only standard access lines (method, path, status). Filenames, extracted numbers and document text are never logged.
- No telemetry, no external network calls, no API keys.
- Exported PDF bytes are unchanged. Renaming does not strip metadata, remove embedded content, or establish that a document is safe to share.

## Reporting a problem

Please open a private security advisory on the repository rather than a public issue.
