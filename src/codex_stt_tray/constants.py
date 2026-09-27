"""Internal, unsupported Codex contract. See docs/compatibility.md."""

ENDPOINT = "https://chatgpt.com/backend-api/transcribe"
AUTHORIZATION_HEADER = b"Authorization"
ACCOUNT_HEADER = b"ChatGPT-Account-Id"
ORIGINATOR_HEADER = b"originator"
ORIGINATOR = b"Codex Desktop"
FILE_FIELD = "file"
TEXT_FIELD = "text"
MAX_AUDIO_BYTES = 32 * 1024 * 1024
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_AUTH_BYTES = 256 * 1024
MAX_REFRESH_BYTES = 1024 * 1024
MAX_RECORDING_SECONDS = 300
REQUEST_DEADLINE_MS = 120_000
REFRESH_DEADLINE_MS = 30_000
