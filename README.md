# Codex STT Tray

A small Python 3.13+ / PySide6 application for dictation on KDE Plasma.
Click the red tray icon to record; click again to transcribe. The final text
is copied to the clipboard, followed by a green check and a quiet chime.

## Development

```sh
uv sync --python 3.13
uv run codex-stt-tray
QT_QPA_PLATFORM=offscreen uv run pytest
uv run ruff check .
uv run ruff format --check .
uv build
```

Python is pinned to 3.13 for development; the package requires Python >=3.13.
The lockfile records the dependency versions. PySide6 is the only runtime dependency.
Linux desktop Qt libraries and a working audio server are also required.

## Authentication

Install Codex CLI, configure `cli_auth_credentials_store = "file"` in its
configuration, and sign in with your own ChatGPT account using `codex login`.
The existing credentials must be an owned regular file with mode `0600`.
The app does not create, copy, or edit them. Select the Codex configuration
directory in **Settings → Codex home**. The saved value takes precedence over
`CODEX_HOME`; with no saved value, the environment or `~/.codex` is used.
Clear the field and save to restore that fallback. `CODEX_CLI_PATH` can specify
the Codex executable. Keyring-only credentials are not yet supported.

This uses an **internal, unsupported** Codex Desktop endpoint. Access and
compatibility are not guaranteed. There is no Platform API-key or local-model
fallback. See [the contract](docs/compatibility.md).

## Desktop behavior

- Static red circle: ready. Pulsing red stop icon: recording.
- Amber animation: uploading/transcribing/copying. Green check: copied.
- Warning icon: failure; use the context menu to retry or discard.
- Right-click: start/stop, cancel, retry, **Settings…**, **Run doctor…**, **Exit**.
- Settings configures Codex home, microphone, recording limit, and chime volume.
- Closing Settings, finishing a recording, or encountering an error keeps the
  tray running. Exit explicitly from the menu (or send SIGINT/SIGTERM).
- One recording at a time; maximum five minutes and 32 MiB.
- Audio stays in bounded memory. No recording or transcript files are written.
- On retryable failure, the pending audio or transcript is kept in memory for
  up to five minutes, or until discarded, replaced, or the app exits.
- The normal clipboard is replaced only after successful transcription.
  Clipboard managers such as Klipper may save copied text in their history.
- KDE Wayland requires the Clipboard applet (Klipper). Its native D-Bus API
  publishes the clipboard without opening a window or running a clipboard CLI.
  X11 uses Qt's clipboard directly.
- Cancel aborts an active upload but cannot retract audio already sent. A
  clipboard request already delivered to Klipper cannot be recalled.
- Completion uses an icon and the app's chime; no transcript is put in a
  desktop notification. Sound failure does not invalidate a successful copy.

Preferences live in `~/.config/codex-stt-tray/settings.ini`, or
`$XDG_CONFIG_HOME/codex-stt-tray/settings.ini` when configured. This dedicated
directory contains application preferences, never copied Codex credentials.

Doctor runs on startup, after **Save and check**, and on request. It checks
configuration, credential-file safety, the Codex ChatGPT session, microphone
availability/format, tray, clipboard service, and HTTPS reachability. Errors
open Settings for repair and keep the app running. Checks are asynchronous
where they involve network or child processes, and have bounded timeouts.
Doctor does not record, overwrite the clipboard, or upload audio: a successful
recording is still needed to verify microphone capture and transcription access.
Use **Copy report** in Settings to copy all displayed doctor results as plain
text for pasting into a message or issue.

The only child process is Codex's app-server, used by doctor to check the login
without forcing refresh, and after an HTTP 401 to refresh expired authentication.
Both use the selected Codex home. All HTTP work is asynchronous Qt networking.

## Installation and optional autostart

```sh
uv tool install --python 3.13 .
codex-stt-tray
```

After updating this checkout, exit the running app and reinstall with
`uv tool install --force --python 3.13 .`, then launch it again. During development,
`uv run codex-stt-tray` runs directly from the checkout.

`packaging/codex-stt-tray.desktop` is a launcher template. Install it in
`~/.local/share/applications/` after setting `Exec` to the absolute installed
executable path if your desktop PATH does not include uv's tool directory.
For opt-in login startup, place the configured launcher in `~/.config/autostart/`.
The app does not enable autostart itself.

## Validation

Automated tests use synthetic audio, dummy credentials, fake transports, and
local child processes. They do not read your Codex login or contact the STT
server. Live microphone/desktop/backend validation is a separate manual step:
see [architecture and acceptance checks](docs/architecture.md).

Inspired by [codex-stt-bridge](https://github.com/ai-babai/codex-stt-bridge) and
[codex-voice](https://github.com/anthnykr/codex-voice). This is an independent
implementation, not an official OpenAI application.
