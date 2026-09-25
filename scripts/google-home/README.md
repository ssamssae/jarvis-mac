# Google Home work routine adapter

한국어 첫 설정: [Matter 등록·수동 코드·루틴·문제 해결](../../docs/nest-matter-setup.md).

This optional Matter plug is a momentary **work-start** button. An authenticated
Matter ON command invokes the existing installed `jarvis_work_mode.execute()`;
it does not duplicate brightness/Wake-on-LAN commands. An explicit Matter OFF
command requests a work-end confirmation from the running local Jarvis listener.
It never directly executes shutdown. Attribute resets to OFF have no action.

The plug resets to OFF after the invocation. Concurrent and rapid repeated ONs
are ignored. Restart sets OFF before attaching the handler and never runs work.
The Python adapter also takes a process lock. Execution receipts retain the
existing routine's step outcomes; a sent wake packet is not proof of boot.

## Install

Use Node 22+ and the existing local Python 3 at `/opt/homebrew/bin/python3`.
Install this directory separately from the native Jarvis bundle:

```sh
npm ci --ignore-scripts --no-audit --no-fund
node server.mjs
```

State defaults to `~/Library/Application Support/JarvisGoogleHome`; override with
`JARVIS_MATTER_STATE`. Pairing credentials and Matter fabric keys stay in this
private directory. Never commit or print `pairing.json`, `voice-pairing.json`, or Matter storage. There
is no HTTP command endpoint, shell input, or public tunnel. Only a paired Matter
controller can send a network command.

Google Home requires a test Matter integration for VID `0xFFF1`, PID `0x8000`
under the same account that owns the home. Register the device using the mobile
Google Home app and the locally displayed pairing code. The developer console
may require terms acceptance. Registration is not complete until Google Home
shows and controls the plug.

After registration, create a routine with starters `しごとスタート`,
`仕事スタート`, `しごとはじめ`, and action ON for this plug. Use the exact room and
device identifier offered by Google's editor; do not guess an unregistered ID.
For work-end, create a separate routine with `しごとおわり`, `仕事終わり`, and
`しごと終わり`, and action OFF for the same plug. Existing pairing is preserved.
Install `jarvis_control_inbox.py` along with the updated listener and work-end
module. The adapter uses an owner-only Unix socket, accepts no shell/command
arguments, drops stale requests, and rate-limits repeated requests.

After the spoken question finishes successfully, Jarvis accepts the exact Japanese
`はい` once within 12 seconds through its existing microphone. Japanese STT applies
only to this pending confirmation. Other replies cancel; timeout, restart, failed
playback, pre-prompt audio, a bare `はい`, and repeated remote OFF cannot authorize
shutdown. Existing Korean Jarvis confirmation remains unchanged. Google Assistant
does not need another wake phrase for the local confirmation.

## Validation

```sh
npm test
python3 -m unittest discover -s ../tests -p test_google_work_start.py
```

Tests inject a mock executor and verify endpoint ON/OFF, reset, restart,
deduplication, error recovery, local IPC, expiring confirmation, and selection of the installed work-start module.
Real acceptance requires the user voice command and a fresh `last-start.json`
receipt plus observed device results. Never report local tests as voice success.

References: [Google Matter pairing](https://developers.home.google.com/matter/integration/pair),
[matter.js](https://github.com/matter-js/matter.js).


## Nest starts voice input; Athena captures everything afterward (T-260925-041)

A separate Matter node on UDP 5541 exposes a momentary outlet named
`Jarvis Voice Input`. Pair it separately in the mobile Google Home app using
private `voice-pairing.json`. The original work node stays on UDP 5540 with its
existing endpoint topology and pairing storage unchanged. ON on the voice outlet sends only `dictation_start` to the owner-only
local socket; OFF/reset/restart do nothing. The original work outlet retains its
ON/OFF behavior. Duplicate starts are throttled and an active dictation or
confirmation cannot be replaced by a new Matter start.

After Google Home pairs the voice outlet, create a separate automation:
Japanese starter `ボイススタート` (spoken `OK Google、ボイススタート`), action ON on
**Jarvis Voice Input**, never the original work-start outlet. Verify the selected
device in the editor before enabling. The automation invokes no work routine.
If the voice node has not been paired, do not substitute the existing work
outlet or erase its pairing; onboarding remains pending. `voice_online` and
`voice_commissioned` in status.json track the new node separately. A commissioned
flag alone does not prove that Google Home currently sees a device as online.

Expected route: Nest recognizes the fixed starter → paired Matter ON → local
listener says `어디로 연결할까요?` → local Mac microphone / installed STT accepts
engine, node, body, and `엔터` using the existing guided input flow. Nest does not
stream or transcribe the subsequent body into this adapter. `queued` means only
IPC acceptance, not that the listener began or a session received text.

Verification: `npm test` covers the new endpoint, original endpoint number
preservation, OFF/reset/restart and deduplication. Python inbox/listener tests
cover start-only IPC, duplicate/busy protection and local-microphone continuation.
These tests use mocked execution; real acceptance requires a fresh
`last-turn.json` with `input_kind=google-home-matter`, `question=보이스 스타토`, a
successful spoken prompt and live `selection_stage=engine`, plus a Google Home
execution observation. Remote session delivery is a separate check.
