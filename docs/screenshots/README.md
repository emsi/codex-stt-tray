# Application screenshots

These PNGs capture the actual `SettingsDialog`, `DoctorDialog`, and tray `QMenu`
from this checkout, using Qt Fusion, DejaVu Sans 10, and 2× scaling. They use
example preferences, an anonymous `/home/demo` path, and illustrative readiness
results. They do not represent a live transcription or an actual login check.
X11 controls are enabled in the preview without connecting to an X11 display.

The Recording screenshot uses Ctrl+Alt+R as an example toggle shortcut. The
Delivery screenshot shows both copy destinations and Ctrl+Shift+V automatic
paste enabled. Actual defaults have no recording shortcut, automatic paste
disabled, and CLIPBOARD as the only copy destination.

Regenerate from the project root:

```sh
uv run --frozen python scripts/capture_screenshots.py
```

The script forces offscreen rendering, does not instantiate real Settings or
Doctor services, and stubs microphone enumeration. It does not capture the
user's desktop, read credentials, register shortcuts, or publish clipboard data.
No image generation or post-processing is involved.
