# Architecture

One QApplication owns all services. Qt event-driven audio, HTTP, D-Bus, and
QProcess signals run on its event loop. No transcription worker threads,
Python child interpreters, clipboard commands, or second asyncio loop.
Small bounded credential reads and in-memory WAV finalization are synchronous;
network requests and credential refresh never block the GUI waiting for I/O.

`recorder` captures supported mono Int16 PCM and finalizes an AudioClip in
memory. `transcription` accepts that value directly, owns a multipart body
until the request ends, limits response accumulation, and returns a Transcript.
One manager permits connection reuse across jobs. `auth` performs a bounded
incremental JSON-lines handshake and asynchronously terminates/reaps its child.

Optional silence trimming tracks 20 ms frame energy during capture, retaining
only first/last above-threshold sample boundaries and one incomplete frame.
Stop slices one continuous PCM interval with configurable padding. It never
removes internal pauses or auto-stops on silence. Edges shorter than 500 ms are
kept; a wholly below-threshold capture fails locally without an upload. The
default threshold is −55 dBFS with 300 ms padding; quiet speech can be protected
by lowering it or disabling trimming. This is conservative energy gating, not
a claim to distinguish every type of speech from background noise.

`controller` owns state, retry data and job generations. Each job has one terminal
outcome; stale callbacks cannot copy an old result. The controller discards audio
after transcription and its transcript reference after successful copy. Failed
retryable operations expire after five minutes. Releasing Python references
does not promise cryptographic erasure of process memory or swap.

Transcription cancellation calls QNetworkReply.abort. All attempts share the
same deadline. Shutdown keeps Qt alive until refresh children finish, including
terminate/kill escalation. Clipboard publication already delivered over D-Bus
cannot be undone; the UI disables cancellation during the short copy stage.

State progression: Idle → Recording → Transcribing → Copying → Copied → Idle.
Errors permit explicit retry where useful, or discard/new recording. Only one
recording is active. Start/stop toggles are ignored during transcription/copy.
Recorder limits finalize and submit automatically, equivalent to pressing Stop.

`settings` stores preferences in the dedicated XDG configuration directory.
`settings_dialog` edits them in Recording, Delivery, and Codex tabs;
`doctor_dialog` independently presents live results and a copyable report. Saving
configuration invalidates old retry data; each transcription snapshots the
selected Codex home, including any subsequent refresh of that request.

`doctor` owns cancellable readiness jobs with an overall deadline. It runs at
startup and after settings changes; superseded jobs cannot publish stale results.
Recording is disabled while checks run. Checks never open the microphone,
change the clipboard, or send authenticated transcription probes. App-server
checks use account/read without forced refresh. An unauthenticated HEAD probes
the fixed endpoint for HTTPS reachability only. Shutdown waits for diagnostic
children as well as transcription children to finish.

The application does not quit when Settings or Doctor closes, a recording
finishes, or an operation fails. Unexpected Python callback exceptions cancel
the active operation and present a safe error without printing exception data.
Microphone startup handles synchronous Qt error signals without destroying the
audio source inside its start callback; delayed errors ignore obsolete sources.
Audio state/error comparisons use `QtAudio`, matching the current PySide6 return
types. The legacy `QAudio` enums compare unequal in PySide6 6.11, even for
`NoError`. Microphone selection compares saved device ID bytes in Python;
QComboBox's QVariant lookup does not reliably compare those bytes by value.
The native audio state signal still advertises `QAudio::State`. Its receiver is
an explicit argument-free Qt slot which reads `source.state()` instead of asking
PySide to convert the signal argument. A recorder-only smoke test that imports
legacy `QAudio` can mask the binding failure; test through application composition
without that import as well.

AudioClip and Transcript hide their payloads from repr. Structured AppError
contains a stable code, safe message, stage, retryability and optional HTTP status.
Backend response/error bodies and native error strings are not shown or logged.

## Acceptance checks

Automated tests must cover success, malformed/empty/oversized responses, HTTP
errors, redirects, refresh/retry, total deadlines, cancellation, late callbacks,
partial refresh lines, child cleanup, WAV framing, and clipboard failure.
Use synthesized bytes and dummy credentials; never personal audio or auth.

Manual checks, separately from the offline suite:

1. KDE X11 and Wayland: tray icon visible, primary click toggles once, context
   menu works, light/dark themes and scaling remain readable.
2. Select microphones; unplug one mid-recording; verify a useful failure.
3. Check silent/short audio and automatic duration-limit stop.
4. With explicitly authorized non-sensitive audio, check transcript delivery,
   cancel during upload, offline networking, and expired-login behavior.
5. Paste into a native Wayland application and an XWayland application, both
   with the tray unfocused. Disable Klipper and verify retry-copy behavior.
6. Check quiet/muted chime, repeat recording, quit while recording/uploading/
   refreshing, and rejection of a second app instance.
7. Change Codex home in Settings; verify doctor reports missing/invalid setup
   without exiting, then save a working home and record again. Restart and check
   persistence. Close Doctor after a failed check and reopen it from the tray.
8. X11: copy synthetic text to CLIPBOARD only, PRIMARY only, and both; verify
   each selected destination and account for clipboard-manager synchronization.
   Test keyboard paste with the receiving application's configured binding.
9. X11 middle-click: point at a text area in the focused application, verify
   PRIMARY insertion, and check cancellation on focus/pointer/selection changes.
   Check held mouse buttons, window decorations, multiple windows, and terminals
   with mouse reporting enabled. No pointer movement should be generated.

Clipboard jobs snapshot a typed copy target: CLIPBOARD, PRIMARY, or both.
Qt publishes only those selections. PRIMARY is explicitly limited to X11;
Wayland CLIPBOARD publication continues to use Klipper asynchronously. A partial
copy failure is retryable and suppresses automatic paste, without undoing any
successful publication. Doctor checks availability without changing selections.

Optional X11 automatic paste snapshots its method and shortcut after successful
publication. A Qt timer waits for key and mouse-button release, then checks focus
and all selected destinations before sending XTEST events. Keyboard mode retains
its existing shortcut behavior. Middle-click requires PRIMARY and validates that
the pointer is inside the focused client's bounds, accounting for window-manager
frames. The pointer is never moved. A pointer/destination change cancels the click.
Neither method claims to verify insertion in the destination application. Failure
is nonfatal and never retranscribes or retries paste automatically. A new operation
cancels pending paste. No keyboard or clipboard CLI is used.

The global X11 toggle uses passive key grabs on a separate persistent display
connection, monitored by QSocketNotifier and a short timer for buffered events.
Physical key state suppresses autorepeat. Conflicting reconfiguration rolls back
new grabs and retains the old binding. Caps/Num/Scroll Lock variants are included.
Bindings are released on shutdown. Wayland input integration is not implemented.

Live transcription, alternative providers, and transcript history are outside scope.
