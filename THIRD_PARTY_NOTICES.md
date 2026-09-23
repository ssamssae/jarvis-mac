# Third-party components

The MIT license in this repository covers the original connector, controller,
installer, native menu app, adapter, tests and documentation. It does not license
a model, vendor service, voice, operating system or hardware.

| Component | Role | Distribution / license boundary |
|---|---|---|
| [PyChromecast](https://github.com/home-assistant-libs/pychromecast) | Google Cast discovery and media control | Installed separately from PyPI; MIT. Its dependencies retain their own licenses. |
| [whisper.cpp](https://github.com/ggml-org/whisper.cpp) | Local transcription executable | User installs separately; MIT project. No executable, model weights or source from it is bundled here. Verify the license of your chosen model separately. |
| [Cursor CLI](https://cursor.com/docs/cli/overview) | Answer generation | External proprietary service/software; user's own login and service terms apply. This project does not supply access or include it in MIT. |
| macOS AppKit, AVFoundation, Swift toolchain, `say`, `afconvert` | Menu, microphone, build and speech synthesis | Provided by Apple, subject to their respective terms; no Apple binaries or voices redistributed. |
| Google Nest / Google Cast | Speaker playback | User-owned hardware and vendor software, not distributed or licensed here. No Google affiliation or endorsement. |
| [NASA Space Place](https://spaceplace.nasa.gov/blue-sky/en/) | Narrow live source for sky/sunset answers | Retrieved at runtime, not bundled. No NASA branding or images included; no NASA endorsement. |

Installers may download PyChromecast's transitive dependencies into a private
virtual environment. This repository does not vendor those packages.
