# Controller recovery

The menu-bar app restarts an unexpectedly exited controller after 1, 2 and 4 seconds. A fourth failure stops capture and displays `컨트롤러 복구 실패 · 앱 재시작 필요`. Five minutes of continuous controller uptime after its first ready message resets that budget. A controller that does not become ready within 60 seconds is stopped and goes through the same recovery policy.

Each restart closes and drains the old input pipe, discards pending microphone clips, clears the dictation capture ID, and starts a fresh controller. Old output callbacks cannot change the new controller's state. Confirmation windows and conversation state are not restored; pending dictation receipts are retained for inspection but never replayed. A manual microphone pause stays paused. Quitting the app cancels pending restarts. Recovery only covers the controller while the native app is alive, not termination of the native app itself.

The controller and inherited workers run in their own process group. After controller termination, the supervisor reaps that acknowledged group before starting a replacement. Separately isolated provider processes may finish work already submitted; recovery does not resubmit requests or claim to undo external effects.

Malformed, missing, out-of-directory or stale captures are rejected individually. Confirmation/dictation state is cancelled and capture returns to listening. Logs contain only exception class, a fixed error code, stage, PID, exit status and time, never raw exceptions or transcripts:

- `~/Library/Application Support/JarvisMacOSS/last-controller-error.json`
- `~/Library/Application Support/JarvisMacOSS/last-controller-exit.json`
- `native-status.json`: `controller_restart_count`, `controller_recovery_attempts`, `controller_recovering`, `controller_pid`

These are bounded last-event files with mode 0600. The native exit file includes EOF, process exit, input delivery failure, startup timeout and spawn failure triggers. A supervisor-initiated termination records the final exit status; it does not prove what originally broke the pipe.

## Microphone recovery

When a device or configuration change stops the audio engine, the app rebuilds it up to three times, one second apart (`마이크 다시 연결 중`). If all three fail, for example while a call still holds the input device, it shows `마이크 재연결 대기` and makes another three attempts after each 30-second cooldown until the engine runs. A running engine resets the budget. A manual pause, quitting and fatal controller errors still stop recovery. Until 2026-10-19 (T-261019-007) the third failure was final (`마이크 재연결 실패`) and only an app restart brought the microphone back; Berry calls on 2026-10-17 and 2026-10-18 left Jarvis deaf for hours that way. While the engine is stopped, `native-status.json` reports `engine_running: false`, so dictation start requests are answered `microphone_unavailable`.

Check path: during the cooldown `native-status.json` shows `status: 마이크 재연결 대기` with `engine_running: false`. Within about 30 seconds after the device is free again it shows `호출 대기`, `engine_running: true`, a higher `microphone_recovery_count` and an updating `meter_updated_wall`. The retry policy is covered by `scripts/tests/test_native_voice_gate.py`; a live forced device failure has not been exercised.

## Verification path

1. Run `python3 -m unittest discover -s scripts/tests -q`. Provider/device effects are mocked. Native recovery tests use real local fake child processes and pipes without microphone access, Cast playback, API calls or appliance commands.
2. Compile with `xcrun swiftc -swift-version 5 scripts/native/jarvis_mac_listener.swift -o /tmp/JarvisMacOSS-check` and lint the app plist.
3. On an approved installation, first confirm an idle `listening` controller and fresh microphone meter. Record the controller PID from `status.json`, verify its command and parent, then send SIGTERM to that controller only. Expect a new controller PID, one restart, `controller_ready=true`, `engine_running=true`, and a newly updating `meter_updated_wall`.
4. Do not replay old speech or issue a physical-device command to test recovery. Acoustic wake recognition is a separate user-boundary check; an updating meter does not prove it.

T-261001-004 evidence: local regression, build, integration and installation receipts are under `/Users/user/reports/T-261001-004/` on Athena. Original 2026-09-30 23:11 termination trigger remains unknown; this change prevents recurrence from an isolated bad capture and records future failures.
