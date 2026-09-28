# Application screenshots

These PNGs capture the actual `SettingsDialog`, `DoctorDialog`, and tray `QMenu`
from this checkout, using Qt Fusion, DejaVu Sans 10, and 2× scaling. They use
example preferences, an anonymous `/home/demo` path, and illustrative readiness
results. They do not represent a live transcription or an actual login check.
X11 controls are enabled in the preview without connecting to an X11 display.

The screenshots show the new X11 defaults: Meta+C toggles recording, the limit
is 3,000 seconds, and automatic Ctrl+Shift+V paste is enabled with CLIPBOARD as
the copy destination. The Codex screenshot demonstrates the recommended separate
`~/.codex-try` home for users whose usual Codex login uses keyring. Doctor's
results are illustrative, not the output of a live readiness check.

Regenerate from the project root:

```sh
uv run --frozen python scripts/capture_screenshots.py
```

The script forces offscreen rendering, does not instantiate real Settings or
Doctor services, and stubs microphone enumeration. It does not capture the
user's desktop, read credentials, register shortcuts, or publish clipboard data.
No image generation or post-processing is involved.
