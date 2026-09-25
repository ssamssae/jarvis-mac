# Google Home work routine adapter

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
private directory. Never commit or print `pairing.json` or Matter storage. There
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
