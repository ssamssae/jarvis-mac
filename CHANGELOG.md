# Changelog

## v1.2.0 — 2026-09-26

- Optional paired Matter adapters connect Google Nest to work-start, confirmed
  work-end, and guided voice session input. Voice starter: `ボイススタート`.
- Unified local `자비스 보이스 스타토` and Nest `ボイススタート` start commands;
  the older local trigger remains compatible.
- Engine then node selection, configured aliases, accumulated body and explicit
  Enter delivery; continuation uses the local Mac microphone, not Nest audio.
- Japanese work-end confirmation `はい` is accepted once after the spoken question
  within 12 seconds. Cancel/expiry/restart retain the existing protections.
- Fresh session delivery and display-only source metadata for compatible bridges;
  no automatic resend on uncertain delivery.
- Field verification includes user-confirmed Nest start/work-end and a fresh
  Hermes Codex source card. WOL/shutdown requests do not prove physical power state.

Source/installer packages only. Matter service, Google Home pairing/routines,
voice model, engine login and private routing still require operator setup.

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
