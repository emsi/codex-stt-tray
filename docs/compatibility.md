# Internal Codex transcription contract

This application implements the completed-recording contract described by
[codex-stt-bridge](https://github.com/ai-babai/codex-stt-bridge) and
[codex-voice](https://github.com/anthnykr/codex-voice). The server is internal,
unsupported, and unversioned. This application's backend has not yet had a
live smoke test. Upstream's tests do not constitute validation of this app.

| Component | Contract |
| --- | --- |
| URL | `https://chatgpt.com/backend-api/transcribe` |
| Method | POST, multipart/form-data |
| Headers | Bearer authorization, ChatGPT-Account-Id, originator Codex Desktop |
| Input | `file` part, generated PCM WAV, logical filename recording.wav |
| Success | JSON object with nonempty string `text` |
| Refresh | Codex app-server: initialize, initialized, account/read with refreshToken true |

Constants live in `constants.py`. Limits are application safeguards, not
claims about server limits: 32 MiB audio, 1 MiB response, 120-second total
transcription deadline, 30-second refresh deadline, and one refresh/retry
after HTTP 401. Redirects are errors, including redirects to the same host.
Cookies are neither loaded nor saved. TLS validation stays enabled.

Refresh uses the [official app-server protocol](https://learn.chatgpt.com/docs/app-server).
The child receives the same CODEX_HOME used to read credentials. Credential
files are never edited by this application. Only file-backed Codex ChatGPT
credentials are supported. There is no API-key fallback or keychain extraction.

Doctor also uses app-server's account/read, with `refreshToken: false`, to check
that the selected home has a ChatGPT account. It does not force credential
refresh. The child runs with that home as its working directory and CODEX_HOME.
Doctor's separate HTTPS probe is an unauthenticated HEAD to the fixed endpoint;
it sends no bearer/account headers, follows no redirects, and does not establish
that a subsequent authenticated transcription will succeed.

Before changing the contract, compare upstream behavior and explicitly verify
using user-owned non-sensitive audio. Diagnostics must exclude request headers,
response bodies, credentials, recordings, and transcript contents. Stop rather
than bypass new authentication requirements.

No streaming server capability is assumed. In-memory capture and asynchronous
upload do not imply live server transcription.

KDE Wayland clipboard integration uses Klipper's `org.kde.klipper` service,
`/klipper` object, `org.kde.klipper.klipper` interface and `setClipboardContents`
method. See [KDE's interface](https://invent.kde.org/plasma/plasma-workspace/-/blob/master/klipper/klipper.h).
This method acknowledges the request; actual cross-application pasting must
be checked on the target desktop. Klipper's history policy remains in effect.
