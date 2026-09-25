# Named session dictation

Say `자비스 헤르메스 코덱스` (or 아테나 / 볼칸 / 볼탄 and 그록 / 커서).
Wait for `말씀하세요`, then dictate. Pauses do not submit. End with the spoken
word `엔터` to send the accumulated text once. `승인 엔터` sends exactly `승인`.
No model interprets or rewrites the dictated content. Approval words are ordinary
user text, not a permission decision made by this router.

The observed STT spelling `헬멧스` is an explicit alias for `헤르메스` only in a
complete node + engine invocation. `헬멧스 코덱스가 뭐야` remains conversation;
dictated content is never corrected using this alias.

`Hermes Codex` is also accepted, case-insensitively, including mixed Korean and
English forms such as `헤르메스 Codex` and `Hermes 코덱스`. Only the invocation
is case-normalized; the dictated body keeps its original text and case.
Questions such as `What is Hermes Codex` remain conversation.

T-260925-036 reproduces the 2026-09-25 live `Hermes Codex` transcript. The
English/mixed-language parser and mocked listener checks pass, including
`말씀하세요` and no conversation backend call. Run
`python3 -m unittest discover -s scripts/tests -p 'test_dictation*.py' -q`
to repeat those 13 checks without sending messages or controlling devices.

## Target alias verification (T-260925-035)

- Prerequisites: configured microphone/STT and named-session transport.
- Entry: say `자비스`, wait for the cue, then say `헤르메스 코덱스`.
- Expected: if STT returns `헬멧스 코덱스`, the listener says `말씀하세요`
  and enters dictation for macbook14:codex instead of answering a general question.
- Local check: `python3 -m unittest discover -s scripts/tests -p test_dictation.py -v`.
  Covers target selection, exact-match boundaries, and unchanged dictated text.
- Verified 2026-09-25 KST on macbook14: the alias regression suite passed (9 tests),
  the full suite passed (175 tests), and native Swift compilation and plist lint passed.
- Sending occurs only after dictated content ends with `엔터`; the local tests
  do not send messages or operate appliances.
- Live speech, playback, and delivery with this alias remain unverified until
  the reviewed change is installed with restart approval and a user voice trial.

This mode uses the configured local STT worker, including the installed 입타
helper. The native microphone keeps recording during transcription. Audio events
carry a dictation ID so late chunks from an ended session cannot trigger commands.

Configure `dictation.argv` in the private listener config as a trusted command
that reads a JSON request on stdin and returns `{id, status}`. The optional
`jarvis_session_input.py --config PRIVATE.json` adapter routes using a `nodes`
mapping of node keys to trusted argv arrays. Install this adapter on each target
and invoke it with Python 3.11+ and `--local`. SSH commands and paths belong in
private configuration, never in recognized speech.

The adapter reuses existing bridge transports and the Grok local FIFO. It does
not start new sessions, clear composers, change models, or answer approval menus.
Occupied or unrecognized composers are preserved. A submitted receipt requires
the dictated text in a new user event in the target transcript. Ambiguous sends
are never retried automatically. Private drafts and failed requests remain under
the listener state directory; receiver UUID receipts suppress duplicate sends.

The optional adapter requires a compatible local `claude-automations/scripts`
installation. Other deployments can supply their own trusted stdin adapter.

On compatible Codex bridges, microphone submissions also write a private local
display receipt before delivery. It contains a request ID, target, session path,
transcript byte offset, timestamp and SHA-256 of the original text, never its body.
The Telegram viewer can match that receipt and label the input as Jarvis voice.
No marker is inserted into the model prompt. The label identifies the transport,
not the speaker, and cannot authorize an action. Unmatched inputs retain the
unknown-source label. Other engines and older bridge installations are unchanged.
