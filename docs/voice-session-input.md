# Named session dictation

Say `자비스 헤르메스 코덱스` (or 아테나 / 볼칸 / 볼탄 and 그록 / 커서).
Wait for `말씀하세요`, then dictate. Pauses do not submit. End with the spoken
word `엔터` to send the accumulated text once. `승인 엔터` sends exactly `승인`.
No model interprets or rewrites the dictated content. Approval words are ordinary
user text, not a permission decision made by this router.

## Guided voice input (T-260925-037)

With microphone permission, the listener running, and `dictation.argv` configured:

1. Say `자비스`, wait for its cue, then say `음성 입력` (or `자비스 음성 입력`).
2. Jarvis asks `어디로 연결할까요?`. Say `코덱스`, `커서`, or `그록`.
3. Jarvis asks `어떤 노드인가요?`. Say `헤르메스` or `노트북` for macbook14,
   `아테나` for mac, or `볼칸` for macmini.
4. Wait for `말씀하세요.`, dictate the body, and say `엔터` to send it once.

`노트북` is also supported in the existing direct invocation `자비스 노트북 커서`.
English engine names Codex/Cursor/Grok are accepted. Unknown selection replies
repeat the current options instead of entering conversation or controlling devices.
Say `취소` during selection to return to wake listening. Each selection reply has
30 seconds after its prompt; an expired reply cancels selection without dispatch.
`음성 입력` during selection starts again from the engine question.

A Cast playback timeout after a selection/dictation prompt has demonstrably
started preserves that state; it does not resend audio or input. A timeout before
the prompt starts, or another playback error, cancels selection. The existing
Cast timeout can still delay readiness; this change does not claim to fix the
speaker's missing completion signal.

Validation entry: `python3 -m unittest discover -s scripts/tests -p 'test_dictation*.py' -q`.
The tests cover all three nodes and engines, the notebook alias, literal content,
unknown replies, cancellation/expiry, and the listener's prompt sequence and
single mocked delivery. They also cover preserving state only after confirmed
playback start. No real message or appliance command is sent by these tests.
Actual user speech and remote receipt for the guided flow require a live trial;
test success alone does not establish acoustic recognition or delivery.
Verified on macbook14, 2026-09-25 KST: 185 full-suite tests passed, native Swift
compilation succeeded, and plist lint passed in this change worktree.

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

## Selection STT vocabulary (T-260925-040)

The bundled whisper-cli worker supplies a one-request vocabulary prompt only
while waiting for an engine or node. It accepts the internal `engine` / `node`
context enum; callers cannot pass arbitrary prompt text. Wake recognition and
literal body dictation receive no vocabulary prompt. Custom workers retain their
existing protocol. Dictated body chunks do not run the wake-only English retry.

These are soft vocabulary hints, not grammar-constrained destination selection.
Unknown output still reprompts, cancellation stays available, and no extra model,
automatic message, or device operation is introduced.

Verification: worker protocol isolation/invalid-context tests, listener context
sequence (none → engine → node → none for body), and existing guided-flow tests.
On 2026-09-25, the installed model decoded clean synthetic Codex/Cursor/Grok,
notebook and cancel correctly with hints. Synthetic silence and an unrelated
appliance phrase did not decode into valid engines. These are controlled fixtures,
not a measured human recognition rate. Repeat the live speaker-to-microphone flow
after installation to assess acoustic performance; retain the actual outcome.


## Nest start (T-260925-041)

With the paired Matter voice outlet and the separate Google Home starter enabled,
say `OK Google、ボイススタート` (보이스 스타토). Only this initial invocation uses Nest.
After `어디로 연결할까요?`, engine/node selection and body use Athena's existing
microphone and STT. See [Matter adapter setup and live acceptance](../scripts/google-home/README.md#nest-starts-voice-input-athena-captures-everything-afterward-t-260925-041).
The setup being present is not proof that the Google Home routine has been
registered or heard; retain the actual live result separately.


## Input source display (T-260925-042)

Set private `dictation.capture_node` to the microphone host's canonical node key
(`mac` on Athena). The listener retains a Matter start through engine/node
selection, then submits `start_source` and `capture_node` separately from the
literal body. Cancel, timeout, a new direct invocation, and submission clear the
start source. A voice request never identifies or authenticates the speaker.

Install the matching session adapter and `voice_input_provenance.py` on each
configured receiving node, plus the matching Codex viewer hook. Missing modules
retain the unknown-source fallback; a repository merge alone is not deployment.
For the approved hybrid route the expected card is “네스트로 시작 · 아테나 음성 입력 ·
자비스 전달”. Local microphone starts must omit the Nest prefix. Reuse
`test_dictation_listener.py`, `test_dictation.py`, and the bridge repository's
`test_voice_input_provenance.py`. Live acceptance requires a new received voice
card with the original text intact; unit tests do not prove Telegram receipt.
