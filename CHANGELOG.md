# Changelog

## v1.1.0 — 2026-09-24

First packaged GitHub release of the experimental Mac voice assistant. Source and
installer only: whisper.cpp/model, Cursor login, Python dependencies, installed
macOS voices, and private device configuration remain external prerequisites.

- Korean 자비스 and English Jarvis wake handling; native quieter/shorter capture
  defaults and silence padding for short local transcription clips.
- Local smart-home, live weather, optional low-brightness status light, and fixed
  work-start routines. No model call for these configured routes.
- Work-end routine requires a fresh yes after its spoken confirmation, with expiry,
  cancellation, no replay, and normal non-forced Windows shutdown requests.
- Observed shortened 끝 and cancellation transcription variants route locally;
  mission-style replies omit Mac brightness status on work-end.
- Cast cue/answer queue and AAC playback recovery preserve device-command execution
  count; retries replay audio only in the supported playback-error case.

Validation: repository tests and native compilation; live microphone/controller,
Nest playback, user-confirmed English wake (one observed successful call, not a
reliability rate), and device-specific field tests. Synthetic recognition tests,
mock command receipts, and physical device outcomes are distinct evidence.

Device-specific Wake-on-LAN recovery is operational configuration outside this
package. Sending a WOL packet does not prove boot, and a shutdown receipt does not
prove power-off. End-to-end voice work-start verification remains a separate field
check; no general recognition-rate or all-hardware compatibility claim is made.
