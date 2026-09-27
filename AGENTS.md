# Development rules

- Use uv and Python 3.13+. PySide6 owns the event loop.
- Keep audio, transcript, and credentials out of logs, diagnostics, and fixtures.
- Never read real credentials during automated tests. Use synthetic data only.
- Auth files are read-only, owned regular files with mode 0600. Codex owns refresh.
- Send credentials only to the fixed HTTPS endpoint; reject redirects and TLS errors.
- Keep audio and response memory bounded. No implicit backend fallback.
- Network and child-process handling must be asynchronous and cancellable.
- Read docs/compatibility.md before changing the backend contract.
- Live transcription tests require explicit authorization and non-sensitive audio.
- Validate with offscreen pytest, Ruff, and uv build.
