# Named session dictation

Say `자비스 헤르메스 코덱스` (or 아테나 / 볼칸 / 볼탄 and 그록 / 커서).
Wait for `말씀하세요`, then dictate. Pauses do not submit. End with the spoken
word `엔터` to send the accumulated text once. `승인 엔터` sends exactly `승인`.
No model interprets or rewrites the dictated content. Approval words are ordinary
user text, not a permission decision made by this router.

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
