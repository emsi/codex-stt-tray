"""Internal, unsupported Codex contract. See docs/compatibility.md."""

ENDPOINT = "https://chatgpt.com/backend-api/transcribe"
AUTHORIZATION_HEADER = b"Authorization"
ACCOUNT_HEADER = b"ChatGPT-Account-Id"
ORIGINATOR_HEADER = b"originator"
ORIGINATOR = b"Codex Desktop"
FILE_FIELD = "file"
TEXT_FIELD = "text"
DEFAULT_RECORDING_SECONDS = 300
MAX_RECORDING_SECONDS = 3000
MAX_RECORDING_RATE = 48000
# Enough for the longest mono Int16 capture at the highest supported rate, plus WAV header.
MAX_AUDIO_BYTES = MAX_RECORDING_SECONDS * MAX_RECORDING_RATE * 2 + 44
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_AUTH_BYTES = 256 * 1024
MAX_REFRESH_BYTES = 1024 * 1024
REQUEST_DEADLINE_MS = 120_000
REFRESH_DEADLINE_MS = 30_000
