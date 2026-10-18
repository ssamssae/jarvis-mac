# Changelog

## Unreleased — 2026-10-12

- 2026-10-19 (T-261019-003): `dictation_start` is answered `microphone_unavailable` while the
  native microphone engine is stopped (`native-status.json` `engine_running: false`), and
  `request_dictation.py` passes the listener's answer through (`queued` /
  `microphone_unavailable` / `rejected`; `unavailable` only when the socket is gone). A
  Berry call had left JarvisMacOSS at `마이크 재연결 실패`; a Vibe Key request was still
  queued, so Jarvis said `말씀하세요.` and held the hallway status light green with no
  capture to end it.
- Dictation corrects the observed homophone `자녀` → `잔여` at a word start: `잔여` is
  pronounced [자녀], and whisper-small writes the commoner word. A longer word that only
  contains the syllables stays as heard; add further observed pairs to `STT_CORRECTIONS`.
- Dictation drops whole STT chunks that are only whisper subtitle boilerplate, once or
  repeated (`-끝-`, `(끝)`, dash-prefixed `-감사합니다.`, `MBC 뉴스 …입니다.`); noise after
  `말씀하세요.` had put them in front of the dictated text. `엠토`, an observed STT spelling,
  submits like `엔터`.
- Claude is a dictation engine (`클로드` / `claude` / STT spelling `클라우드`),
  delivered through the existing Claude bridge tmux transport with the same
  prompt/draft guards as Codex. Nodes `제우스` (zeus) and `아폴론` (apollo) join
  아테나 / 볼칸; the selection prompts now name every choice.
- A local trigger may preset the target: `request_dictation.py --target "아테나 클로드"`
  starts dictation at `말씀하세요.` without the engine and node questions. The
  phrase is validated as a standalone `node engine` invocation before it is queued.
- Claude delivery verifies only the transcript bytes appended after the paste,
  so large session transcripts are not re-read on every poll.

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
