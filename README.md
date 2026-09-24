# Jarvis Mac v1.1.0 · experimental

**맥에 “자비스”라고 말하면, 로컬 음성 인식 → Cursor 답변 → Google Nest 재생으로 이어지는 작은 음성 비서입니다.**

Swift 메뉴 막대 앱이 마이크를 관리하고, Python이 로컬 호출 판별·명령 분류·대화 생성·재생을 연결합니다. 공개 코드는 MIT입니다. Cursor·macOS·음성 모델 등 외부 구성요소는 별도입니다.

```text
Mac microphone → local whisper.cpp → “자비스” wake gate
                                      ↓ qualified question only
                               local commands / Cursor CLI conversation
                                      ↓
                            macOS say → Google Nest
```

메뉴 앱에서는 **인사와 일반 질문을 Cursor로 보내 한국어 음성으로 답합니다.** 연결된 근거 자료가 없어도 대화할 수 있습니다. 별도 로컬 설정으로 연결한 스마트기기, 실시간 날씨, 상태등, 작업 시작·확인 후 종료 루틴은 기존 규칙으로 처리하며 모델을 호출하지 않습니다. Cursor는 대화에만 사용하고 기기를 직접 조작하지 않습니다. 일반 대화에서 웹 검색을 수행하지 않으므로 최신 정보 조회를 보장하지 않습니다. 개인 기기 정보와 제어 명령은 배포본에 포함하지 않습니다. 별도 텍스트/WAV CLI는 기존 근거 기반 동작을 유지합니다.

## 릴리스

[최신 릴리스](https://github.com/ssamssae/jarvis-mac/releases/latest)에서 설치 스크립트를 포함한 소스 패키지와 SHA-256 체크섬을 제공합니다. 완성된 단독 실행 앱이나 모델 번들이 아닙니다. 아래 준비물을 설치한 뒤 패키지 안의 설치기를 실행하세요. 변경 사항은 [CHANGELOG.md](CHANGELOG.md)에 기록합니다.

## 먼저 준비할 것

- macOS와 Xcode Command Line Tools (`xcode-select --install`), Python 3.11 이상.
- [whisper.cpp](https://github.com/ggml-org/whisper.cpp)의 `whisper-cli`와 한국어를 지원하는 모델 파일. 직접 설치·다운로드하고 해당 라이선스를 확인하세요. 모델이나 실행 파일은 이 저장소에 포함하지 않습니다.
- [Cursor CLI](https://cursor.com/docs/cli/overview)에 본인 계정으로 로그인하고 모델을 선택해 둡니다. 기본 실행 파일은 `~/.local/bin/agent`입니다. 사용자 계정의 사용량·플랜·서비스 약관이 적용되며 무료 사용을 보장하지 않습니다.
- 같은 신뢰할 수 있는 로컬 네트워크에 있는 Google Nest/Cast 스피커의 **정확한 기기 이름**. VPN·게스트 네트워크·방화벽은 검색/재생을 막을 수 있습니다.
- macOS 한국어 음성(기본값 `Yuna`). `say -v '?'`에서 확인하고 없으면 시스템 설정에서 추가합니다.

## 설치 및 사용

Mac의 로그인된 GUI 세션에서 터미널을 열어 실행합니다. 아래 모델 경로와 스피커 이름은 본인 값으로 바꾸세요.

```sh
git clone https://github.com/ssamssae/jarvis-mac.git
cd jarvis-mac
python3 scripts/install-jarvis-mac-listener.py \
  --cast-name 'My Nest Mini' \
  --model "$HOME/models/ggml-small.bin" \
  --whisper-cli /absolute/path/to/whisper-cli \
  --start
```

`--cast-name`과 `--model`은 필수입니다. `--whisper-cli`를 생략하면 현재 PATH에서 찾습니다. 다른 Cursor 실행 파일은 `--cursor-binary /absolute/path/to/agent`로 지정합니다.

답변 음성은 `--voice '설치된 음성 이름'`으로 바꿉니다. `say -v '?'`에 나온 이름 전체를 공백·괄호까지 정확히 지정하세요. 설치된 음성인지 먼저 확인하며, 옵션을 생략하면 업데이트 시 기존 선택을 유지하고 최초 설치는 `Yuna`를 사용합니다. 음성의 자연스러움은 설치된 음성에 따라 달라집니다.

설치기는 소스를 복사하고 전용 가상환경에 `pychromecast==14.0.9`를 설치한 뒤 네이티브 앱을 컴파일·로컬 서명합니다. 인터넷과 개발 도구가 필요합니다. `--start`를 주었을 때만 즉시 시작합니다. 생략하면 다음 GUI 로그인부터 실행됩니다. 다른 앱이나 서비스를 재시작하지 않습니다.

처음 뜨는 **JarvisMacOSS 마이크 접근** 요청을 허용한 뒤 메뉴 막대가 “호출 대기”가 되면 말하세요. 앱 시작 때 Nest 연결을 준비하고 질문 사이에 재사용하므로 최초 준비에는 시간이 걸릴 수 있습니다. 연결이 끊기거나 질문 처리가 실패하면 다음 질문에서 다시 연결하며, 실패한 답변을 자동으로 재생하지 않습니다.

> 자비스, 하늘이 파란 이유를 한 문장으로 알려줘.

“자비스, 안녕”처럼 인사하거나 일반 질문을 할 수 있습니다. 자비스 실행 중 최근 최대 6번의 일반 문답을 메모리에서 보관해 다음 질문에 함께 전달합니다(기록 JSON 10,000자 한도). 예를 들어 “자비스, 내가 고른 색은 파랑이야” 다음에 “자비스, 내가 고른 색이 뭐였지?”라고 물을 수 있습니다. 각 질문에는 기존처럼 호출어가 필요하며, 앱을 종료·재시작하면 대화 맥락이 초기화됩니다. 기기·날씨의 규칙 응답은 이 일반 대화 기록에 넣지 않습니다. 기존 Cursor TUI 대화에는 입력하지 않습니다.

호출어와 질문을 성공적으로 인식하면 **Nest에서 짧은 두 음의 접수음**이 먼저 납니다. 접수음은 질문이 인식되었다는 뜻이며, 답변 생성 성공을 뜻하지는 않습니다. 접수음을 재생하는 동안 Cursor가 답변을 생성하고, 같은 재생 큐가 접수음 뒤에 답변을 이어 재생합니다. 답변 재생 전까지 추가 질문은 받지 않습니다.

“자비스”, 영어 “Jarvis”만 말한 뒤 질문할 수도 있습니다. 영어 발음이 “자르비스”로 받아써진 경우도 같은 호출로 처리합니다. 호출 인식 후 8초 동안 다음 발화를 받습니다. 인식·생성·재생 중에는 새 질문을 받지 않아 자기 답변을 다시 호출로 처리하는 것을 줄입니다. 메뉴에서 마이크 일시 정지/다시 듣기 또는 종료할 수 있습니다. 종료하면 같은 세션에서 자동으로 다시 켜지지 않습니다.

기본 Whisper 인식기에서는 호출 대기 중 한국어 인식 결과가 “헤이” 또는 “hey”로 시작하지만 호출어가 맞지 않을 때, 같은 음성을 영어로 한 번 더 인식합니다. 영어 결과가 “Hey Jarvis” 단독 호출과 정확히 일치해야 호출을 허용합니다. 이미 인식된 호출에는 재시도하지 않으며, 뒤에 하는 질문은 계속 한국어로 인식합니다. 영어 재시도 결과에 질문이나 명령이 붙으면 사용하지 않습니다. 별도 `stt_worker`에는 이 재시도를 적용하지 않습니다.

설치 위치는 별도로 분리되어 있습니다.

- 앱: `~/Applications/JarvisMacOSS.app`
- 설정/상태: `~/Library/Application Support/JarvisMacOSS/`
- 로그인 항목: `~/Library/LaunchAgents/com.ssamssae.jarvis-mac-oss.plist`

업데이트 전에는 메뉴에서 앱을 종료한 후 설치기를 다시 실행합니다. 앱·설정·로그인 항목의 기존 파일은 전용 상태 폴더의 `backups/`에 보관합니다. 설치 인자가 새 설정의 기준이므로 모델·기기 이름을 다시 지정하세요.

로그인 실행만 해제하려면 메뉴에서 종료한 뒤 다음을 실행합니다. 현재 사용자에게 설치한 이 앱의 항목만 대상으로 합니다.

```sh
launchctl bootout "gui/$(id -u)/com.ssamssae.jarvis-mac-oss"
rm "$HOME/Library/LaunchAgents/com.ssamssae.jarvis-mac-oss.plist"
```

완전히 제거할 때는 종료 후 위 앱과 상태 폴더도 직접 삭제합니다. 상태 폴더에는 최신 질문/답변 기록과 설치 백업이 포함됩니다.

## 텍스트 / WAV로 먼저 시험하기

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python scripts/jarvis_mac_voice.py \
  --text '하늘이 파란 이유를 한 문장으로 알려줘' \
  --cast-name 'My Nest Mini' --play
```

텍스트/WAV 실행은 하늘·노을의 NASA 근거 조회 또는 명시적 `--evidence`를 사용하는 별도 근거 기반 데모입니다. 근거가 없는 질문은 답변 생성을 건너뜁니다. 메뉴 앱의 일반 대화와 구분됩니다. `--voice '설치된 음성 이름'`을 지원하며 기본값은 `Yuna`입니다.

`--play`가 없으면 음성 파일 합성까지만 실행하고 임시 파일을 정리합니다. `--wav question.wav --model /path/model.bin --whisper-cli /path/whisper-cli`로 로컬 인식을 포함할 수 있습니다. WAV는 16 kHz, 모노, 16-bit PCM이어야 합니다.

`--evidence evidence.json`은 아래 형식이며 `question`은 질문 전체와 일치해야 합니다. 근거의 신뢰성과 이용 권한은 제공자가 확인해야 합니다. 도구 실행을 요청하는 근거 텍스트를 사용하지 마세요.

```json
{"question":"예시 질문", "sources":[{"url":"https://example.org/source", "text":"질문에 답하는 신뢰할 수 있는 근거"}]}
```

Cursor는 기존 선택 모델을 그대로 사용하고 별도 임시 설정·작업공간에서 `ask` 모드로 실행합니다. 메뉴 앱의 일반 대화는 [Cursor ACP](https://cursor.com/docs/cli/acp) 프로세스를 유지해 요청마다 실행·인증을 반복하지 않습니다. 모델에 보내는 문맥 한도를 유지하기 위해 각 요청은 별도 ACP 세션에서 처리합니다. 임시 세션 자료는 앱 종료·연결 오류 때 지우고, 최대 32회 요청 후에도 연결을 교체해 정리합니다. 도구 권한 요청은 거절하고 파일·터미널 클라이언트 기능을 제공하지 않습니다. 기존 대화나 설정 파일을 바꾸지 않으며 별도 API 키를 입력받지 않고 환경의 Cursor API 키도 전달하지 않습니다. CLI나 서비스 버전이 바뀌면 권한/출력 형식을 재검증해야 합니다. 운영체제 보안 샌드박스를 제공하는 프로젝트는 아닙니다.

## 개인정보와 동작 범위

- 마이크에서 잡힌 발화는 **로컬에서 먼저 인식**합니다. 호출에 해당하지 않는 인식 텍스트는 답변 서비스에 보내거나 기록하지 않습니다. 일반 대화에서는 호출을 통과한 질문과 메모리에 보관한 최근 일반 문답을 Cursor로 보냅니다. 로컬 규칙으로 처리한 기기·날씨 요청은 Cursor로 보내지 않습니다. 별도 CLI의 NASA 근거 조회는 해당 사이트로 나갑니다.
- 발화 WAV는 임시로 디스크에 저장했다가 인식 후 삭제합니다. 비정상 강제 종료는 임시 파일을 남길 수 있습니다. 최신 호출 질문·답변·측정치는 `last-turn.json`에 덮어쓰며, 상태 파일은 로컬 전용 권한으로 저장합니다. 이 폴더를 공개하거나 동기화하지 마세요.
- 마이크 일시 정지는 캡처를 멈춥니다. 이미 처리 중인 질문은 끝날 수 있습니다. 앱 종료는 소유한 처리·합성·재생을 정리합니다.
- Nest에 들려줄 답변 WAV는 전용 임시 폴더의 **암호화되지 않은 LAN HTTP** 서버로 전달합니다. 연결과 서버는 앱이 유지하고 음성 파일은 각 답변 처리 후 삭제합니다. 신뢰할 수 있는 네트워크에서 사용하세요. 오디오를 인터넷에 공개하는 기능은 없습니다.
- 이미 다른 미디어가 재생/일시 정지/버퍼링 중인 스피커는 덮어쓰지 않습니다. 지정한 이름의 기기만 사용하며 다른 스피커로 자동 대체하지 않습니다.
- 호출어는 사용자 인증이 아닙니다. 주변 사람·TV 음성·오인식도 호출할 수 있습니다. 단일 사용자 실험용이며 화자 인증, 끼어들기, 동시 질문, 범용 가전 제어는 제공하지 않습니다.

## 성능과 검증 범위

원형 구현은 실제 마이크 질문 → Cursor → Nest의 **1회 실제 청취**를 확인했습니다. 말 끝부터 재생까지 **39.07초**, 이 중 답변 생성 **21.88초**, Nest 연결 **13.47초**였습니다. 이는 속도 개선을 보여 주는 수치가 아닙니다.

연결 재사용을 적용한 **공개판의 기본 `whisper-cli` 어댑터로 실제 마이크 → Cursor → Nest 재생 및 정상 종료를 확인했고, 사용자가 답변을 한 번 들었다고 확인했습니다.** 같은 질문의 말 끝부터 재생까지 **26.15초**로, 앞선 원형 표본보다 **12.92초(약 33%)** 짧았습니다. 질문 중 Nest 연결은 재사용으로 **0초**, Cursor 생성은 **20.40초**, 음성 인식은 **0.69초**였습니다.

이후 접수음을 추가한 공개판도 실제 마이크로 확인했습니다. **말 끝→접수음 3.81초, 말 끝→실제 답변 24.51초**였고, 사용자가 접수음 뒤 답변을 한 번 들었다고 확인했습니다. 접수음·답변 모두 정상 종료했으며 이 역시 단일 관측입니다. 접수음은 Cursor 생성과 병행하고 실제 답변 지연에 포함시키지 않습니다.

앱 시작 때 연결 준비는 별도입니다. 첫 시작에서 **12.83초**, 정상 실측 직전 재시작에서는 **0.60초**가 걸렸습니다. 최초 시험에서는 답변은 한 번 들렸지만 종료 확인에 시간 초과가 발생하여, 순간적인 Nest 종료 알림을 소유한 재생 세션과 대조해 보관하도록 수정한 뒤 정상 종료를 재검증했습니다.

**각 수치는 성공한 공개판 1회와 이전 원형 1회의 관측 비교이며 반복 통제 실험이 아닙니다.** 원형은 별도의 Ipta warm STT helper를 사용했고 공개판은 매 발화마다 `whisper-cli`를 시작합니다. Cursor 선택 모델은 같지만 서비스·네트워크 변동이 있으므로 같은 지연을 보장하지 않습니다. JSON 프로토콜·오류·종료 회귀검사와 네이티브 컴파일도 별도로 통과했습니다. [측정 범위와 집계](docs/benchmark.json)를 참고하세요.

위 수치는 전체 답변을 기다리던 이전 실행 방식의 기록입니다. 현재 메뉴 앱의 일반 대화는 ACP의 답변 텍스트만 받아 **완성된 문장부터 합성·재생**합니다. 추론·도구 진행·다른 세션의 알림은 읽지 않고, 마지막 미완성 문장은 정상 `end_turn` 결과 뒤에 읽습니다. 중간 오류가 나면 남은 재생을 중단하고 성공으로 기록하지 않습니다. 오류 전에 이미 읽은 문장은 되돌릴 수 없습니다. 대화 기록에는 성공한 답변만 추가합니다. 별도 텍스트/WAV CLI는 기존 최종 결과 검증 방식을 유지합니다.

모델, 하드웨어, 선택한 Cursor 모델/서비스 상태, 네트워크에 따라 지연이 크게 달라집니다. 첫 일반 질문에는 ACP 시작 비용이 들고 이후에는 프로세스를 재사용합니다. 모델 사전 질문이나 자동 재전송은 하지 않습니다. `last-turn.json`에는 `generation_timings.init_s`, `first_delta_s`, `result_s`, `process_reused`와 `first_sentence_s`를 기록합니다. `first_sentence_s`는 합성 요청 시점이며 실제 재생 시작과 다릅니다. 접수음 재생은 `speech_end_to_ack_s`, 실제 답변 재생은 `speech_end_to_playing_s`로 구분합니다. 접수음을 답변 지연 단축으로 계산하지 않습니다.

## 외부 로컬 STT worker 연결

`--stt-worker /absolute/path/to/worker --model /path/to/model`을 설치기에 넘기면 기본 whisper.cpp 어댑터 대신 호환 실행 파일을 사용합니다. 실행 시 모델 경로를 하나의 인자로 받으며 다음 JSON-lines 계약을 따릅니다.

```text
stdout: {"ready":true}
stdin:  {"wav":"/absolute/path/question.wav"}
stdout: {"text":"자비스, 하늘이 파란 이유"}
```

진단은 stderr, 프로토콜만 stdout에 출력해야 합니다. 오류는 `{"error":"short_code"}`로 반환합니다. 로컬 인식과 개인정보 보호는 사용자가 연결한 worker 구현에도 달려 있습니다. 외부 worker·모델은 이 저장소에 포함하지 않습니다.

## 문제 해결

- `cursor_keychain_locked`: SSH와 로그인된 GUI 터미널의 키체인 접근이 다를 수 있습니다. Mac의 키체인 접근 앱에서 `login` 키체인을 본인이 잠금 해제하고 GUI 터미널에서 Cursor를 확인하세요. 암호를 명령행·로그·이슈에 붙여 넣지 말고 키체인 우회 설정을 사용하지 마세요.
- `cursor_login_required` / `cursor_selected_model_missing`: 같은 Mac 사용자 계정의 Cursor CLI에서 로그인 및 모델 선택을 먼저 완료하세요.
- 마이크 권한 필요: 시스템 설정 → 개인정보 보호 및 보안 → 마이크에서 JarvisMacOSS를 확인하세요. 원격 터미널에서 앱을 직접 실행하지 말고 로그인된 Mac 세션에서 설치/실행하세요.
- `exact_cast_target_missing`: 기기 이름과 같은 LAN인지 확인하세요. 중복 이름은 피하세요.
- `existing_media_preserved`: Nest의 기존 미디어를 본인이 종료한 후 다시 호출하세요.
- 로컬 인식 실패: whisper-cli 경로, 모델 호환성, 한국어 지원, WAV 형식을 확인하세요. 기본 어댑터는 실패 시 원시 진단 내용을 답변으로 읽지 않습니다.

## 개발 / 라이선스

```sh
python3 -m unittest discover -s scripts/tests -q
xcrun swiftc -swift-version 5 scripts/native/jarvis_mac_listener.swift -o /tmp/JarvisMacOSS
```

테스트는 계정, 모델 다운로드, 마이크, Nest 없이 실행됩니다. CI는 Python 테스트와 Swift 컴파일을 확인하며 실제 하드웨어 시험을 대신하지 않습니다.

[MIT LICENSE](LICENSE) · [외부 구성요소 안내](THIRD_PARTY_NOTICES.md)

## English quick start

Experimental macOS menu-bar voice assistant: local whisper.cpp transcription and
wake-prefix gating, grounded Cursor CLI answers, macOS speech synthesis, and
Google Nest playback. Korean sky/sunset questions are the built-in demonstration;
unsupported questions abstain. This is not a general web-search assistant.

Install Python 3.11+, Xcode Command Line Tools, your own whisper.cpp executable and
multilingual model, and Cursor CLI with your own login and selected model. Run the
installer above with your exact `--cast-name`, `--model`, and `--whisper-cli` paths;
add `--start` to start immediately. Grant microphone access to **JarvisMacOSS**.
Say “자비스, 하늘이 파란 이유를 한 문장으로 알려줘.” Pause or quit from the menu bar.

The OSS app has its own bundle, state directory and login item. Ambient speech is
transcribed locally; wake-qualified question text and evidence reach Cursor.
Latest question/answer metrics stay in local `last-turn.json`. Reply audio is
served temporarily over unencrypted LAN HTTP. Wake words are not authentication.

The public adapter completed a real microphone → Cursor → Nest trial in **26.15 seconds**
from speech end to playback, with receiver completion and one user-confirmed playback.
The earlier prototype took 39.07 seconds with a different warm STT worker. These are
single observations, not a controlled repeated benchmark. Nest startup preparation
is reported separately; the selected Cursor model was unchanged. See the benchmark JSON.
Cursor usage is subject to your own account and terms; it is not bundled or made
free by this project. MIT covers only this repository's original code.

## 선택 사항: 기존 로컬 스마트홈 루틴

기본 설치는 기기를 제어하지 않습니다. 이미 사용 중인 로컬 실행기가 있다면
`config.json`의 `smart_home`에 명시적으로 연결할 수 있습니다. 설정과 기기 정보는
이 저장소에 커밋하지 마세요. 실행기 Python 모듈은 `plan_for_transcript`, `tuya_step`,
`execute_plan`, `RunResult` 인터페이스를 제공해야 합니다.

- `module`, `python`, `control_script`, `scheduler_script`: 기존 로컬 파일의 절대 경로.
- `phrases`: 정확한 발화 → 기존 루틴의 표준 발화 매핑.
- `devices`: 기기 별칭 → `routine` 표준 이름 또는 `on`/`off` 명령 인자 배열.
- `scheduled_device`: 기존 실행기가 처리할 예약 기기 이름(선택).

호출어를 통과한 직접 요청만 고정 규칙으로 분류합니다. 루틴 실행에는 Cursor 호출이
없으며, 일반 질문의 Cursor 설정은 유지합니다. 모델이 생성한 텍스트는 기기 명령이
될 수 없습니다. 기존 실행기의 단계·순서를 유지하고, 기존 TTS는 끈 뒤 현재 Nest
음성 큐로 안내합니다. 부분 실패는 성공으로 안내하지 않습니다. IR 신호 전송은
실물 전원 확인을 의미하지 않습니다. 물은 정확히 등록한 직접 발화에만 허용되며
테스트에서는 `dry_run=True`를 사용하세요. 호출어는 사용자 인증 수단이 아닙니다.

Nest가 해당 오디오에 명시적인 `ERROR`를 반환하면 새 URL로 **오디오만 한 번**
재전송합니다. 기기 명령은 반복하지 않으며, 다른 미디어가 재생되거나 중단 신호가
오면 재시도하지 않습니다. 선택한 목소리는 유지하고, 접수음과 답변 WAV를 macOS `afconvert`로 AAC/M4A로 변환해 전송합니다.

“자비스”만 부르면 로컬 인식 후 Nest 접수음으로 듣기 준비를 알리고, 효과음 종료부터
8초 동안 다음 명령을 기다립니다. 호출과 명령을 한 문장으로 말하면 기존처럼 명령
접수음이 나옵니다. 호출어만으로는 Cursor나 스마트기기를 실행하지 않습니다.

### Live weather

Optional private `config.json` entry `weather` sets `name`, `latitude`, `longitude`,
`timezone` (default `Asia/Seoul`), and optional locality `aliases`. No default
location is inferred from the speaker name or IP address. The installer preserves
this configuration. “오늘 날씨”, “지금 날씨”, “내일 날씨”, and “오늘 비 와?”
use a live [Open-Meteo](https://open-meteo.com/) forecast lookup and local Korean
templates without a language-model call. Current temperature is model-derived;
it is not a local thermometer reading. Daily precipitation probability is the
maximum for the requested day, not a claim that it is raining now.

Coordinates are sent to Open-Meteo for this request; utterance text is not sent.
The receipt records the provider, forecast date and fetch time. Timeouts, missing
values, wrong units and stale current data produce an explicit unavailable reply.
Unsupported locations or dates do not silently use the configured location.
Weather data attribution: [Open-Meteo, CC BY 4.0](https://open-meteo.com/en/terms).

### Optional status light

Private `status_light` configuration supplies `python` (an existing Tuya environment),
`map` (local device map), and `device` (the bulb alias). This opt-in adapter supports
DPS 1–5 with 12-digit HSV color. No local keys enter the public configuration example.
A recognized wake displays blue, the follow-up listening window green,
and processing yellow, at 1% color brightness. Ambient speech does not light it.
An independent worker turns the light OFF (without returning to white mode) on completion,
8-second follow-up expiry or graceful shutdown. A local recovery journal supports
restart after interruption. The default standby is OFF. Network failure is recorded without blocking recognition.
The indicator releases before appliance commands so it cannot undo a requested light
change; externally changed fields are preserved. Unsupported bulbs remain unconfigured.

### Optional work-start routine

“일하자”, “일 시작하자”, or “작업 시작하자” can execute a private `work_mode.steps`
list (up to four trusted local command argument arrays). Configure each step with
`name`, `kind` (`brightness` or `wake`), and `argv`. Brightness helpers must return
verified JSON for level 6 of 16. Wake steps require a nonempty `success_marker`
matching the sender's output; sending a packet is never reported as completed boot.
Unconfigured or failed steps are named in a partial-result reply. These fixed routines
bypass the language model and never interpolate utterance text into commands.
The optional `jarvis_display_brightness.py --level 6` helper targets only built-in
Mac displays and checks the resulting value (0.375); unsupported system APIs fail
explicitly. The installer preserves `work_mode`; credentials and device identities
remain in local configuration. User display auto-brightness policy is unchanged.

### Confirmed work-end routine

“일 끝” asks whether to lower both Mac displays to level 0 and normally shut down
both configured Windows PCs. No action runs until a fresh “예” or “네” is recognized
within 12 seconds after the spoken question finishes. Any other answer cancels;
timeout, failed question playback, process restart, early audio, and repeated yes
cannot authorize execution. Private `work_end.steps` holds four trusted `argv`
arrays with `brightness` or `shutdown` kind. Shutdown commands must use normal,
non-forced shutdown and emit `JARVIS_SHUTDOWN_ACCEPTED` only after acceptance.
The reply distinguishes shutdown requests from confirmed power-off. The installer
preserves this configuration. The confirmation path never calls the language model.

### Voice capture and current replies

The native detector uses a -48 dB floor, an 8 dB noise margin, 180 ms minimum
voiced time, and 350 ms pre-roll. These are experimental defaults, not a guarantee
for every room or microphone. Clips shorter than two seconds receive 350 ms leading
and one second trailing silence before local transcription; the speech bytes are
preserved. Language and model are unchanged, and no wake-word prompt is injected.

“끝” alone, after the wake gate, also asks the work-end confirmation because it was
observed as a shortened transcription of “일 끝”. It never shuts down immediately.
Cancellation and observed cancellation-only transcription variants are handled
locally, including outside the confirmation window. They cannot grant execution.

Current replies are “작전 개시. 시스템 기동을 요청했습니다.”, “오늘의 작전을 종료할까요?”,
“작전 종료 절차를 시작합니다.”, and “작전 종료 취소. 대기하겠습니다.”.
Work-end speech omits Mac brightness status while preserving internal results and
Windows shutdown-request warnings. A requested shutdown is not proof of power-off.
