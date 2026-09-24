# Codex 기본 모델 메뉴에서 OpenAI·Mindlogic 선택

macOS ChatGPT/Codex 앱의 **기존 모델 선택 메뉴**에 OpenAI 모델과 `Mindlogic · ...` 모델을 함께 표시하는 로컬 구성입니다. 다만 **기본 앱에서 한도 초과 상태의 메뉴 전환은 아직 동작하지 않습니다.** OpenAI 사용량이 소진된 계정에서 Mindlogic 항목을 선택해도 앱이 입력을 막는 사례가 확인됐습니다. 메뉴 표시만으로 Mindlogic 요청이 전송됐다고 판단하지 마세요. OpenAI API 키 과금 방식으로 바꾸지 않습니다.

## 검증 환경과 구현 이유

2026-09-24의 macOS ChatGPT/Codex 앱 **26.917.51856**(빌드 10492), 내장 CLI **0.155.0-alpha.16**을 기준으로 합니다. 이 버전에서는 모델 선택이 모델을 변경하지만 이미 로드된 대화의 실행 제공자를 바꾸지는 않았습니다. `thread/resume(modelProvider=...)`도 로드된 대화에는 적용되지 않았습니다. 따라서 `model_provider`를 사용자 수준에서 한 번 `mindlogic_menu_router`로 고정하고, 모델 ID별로 로컬 Responses 라우터가 분기합니다.

이 경로는 Codex의 사용자 지정 제공자 설정 `requires_openai_auth = true`, `http_headers`, `model_catalog_json`과 app-server `model/list`를 사용합니다. [인증 설명](https://learn.chatgpt.com/docs/auth#alternative-model-providers)에는 사용자 지정 제공자가 기존 ChatGPT 로그인을 사용할 수 있다고 명시되어 있습니다. 그러나 이 설정으로 OpenAI 한도 검사와 무관하게 Mindlogic을 사용할 수 있다는 뜻은 아닙니다. `requires_openai_auth = false`로 단순 변경하면 라우터가 기존 ChatGPT bearer 인증을 받지 못해 OpenAI 메뉴 경로를 유지할 수 없습니다. [설정 레퍼런스](https://learn.chatgpt.com/docs/config-file/config-reference)와 [App Server 문서](https://learn.chatgpt.com/docs/app-server)를 함께 참고하세요. 라우터는 Codex 바이너리나 앱 서명을 수정하지 않습니다.

OpenAI upstream의 `chatgpt.com/backend-api/codex` 경로는 현재 설치된 클라이언트 요청에서 관찰·검증한 값이며 공개 안정성 계약으로 문서화된 경로는 아닙니다. 앱 버전이 바뀐 뒤에는 실제 왕복 요청을 다시 확인해야 합니다.

## 설치와 일상 사용

기존 `FACTCHAT_API_KEY`는 `~/.codex/.env`에서 사용합니다. 키 값은 로그·설정·저장소에 기록하지 않습니다. 설치 명령은 모델 목록 조회, 기존 설정 백업, 사용자 수준 LaunchAgent 등록을 한 번 수행합니다.

```bash
python3 setup.py menu-install
python3 setup.py menu-status
```

설치 당시 앱이 모델 목록을 캐시했다면 **처음 한 번만** 앱을 다시 여세요. 기본 모델 메뉴에서 기존 `GPT-6 Sol` 같은 항목은 OpenAI, `Mindlogic · GPT-6 Sol` 같은 항목은 Mindlogic을 뜻하도록 구성했습니다. 같은 upstream 모델 ID라도 메뉴 ID는 `gpt-6-sol`과 `mindlogic/gpt-6-sol`로 분리됩니다. **선택 후 앱이 요청을 허용하고 라우터 로그에 목적지가 기록됐는지 확인해야 합니다.** OpenAI 한도 초과 시에는 메뉴에서 Mindlogic을 선택해도 입력 단계에서 막힌 사례가 있습니다.

설치 시 현재 OpenAI 계정의 `~/.codex/models_cache.json` 목록을 복제하여 기존 모델 항목을 보존하고, Mindlogic 계정에서 조회되는 모델 중 같은 upstream 모델의 Codex 메타데이터가 있는 것만 추가합니다. 새 모델이 **계정에 추가되었을 때만** `python3 setup.py menu-refresh`를 실행합니다. 카탈로그 자체를 갱신한 뒤에는 앱을 다시 열어야 목록이 갱신될 수 있습니다. 이미 등록된 모델 사이의 평소 전환에는 이 명령을 쓰지 않습니다.

기존 대화는 시작 당시 제공자를 유지하므로, 같은 대화에서 두 제공자를 고르려면 **한 번** 라우터 제공자로 재연결해야 합니다. 대화가 유휴 상태인지 확인하고, 앱에서 해당 대화만 보관했다가 보관 해제하여 writer가 내려간 뒤 다음을 실행합니다. 잠금이 남아 있으면 명령은 중단하며 다른 실행 주체를 종료하지 않습니다.

```bash
python3 setup.py menu-thread <기존-threadId>
```

이 명령은 모델 요청을 보내지 않으며 동일 threadId·프로젝트 경로·이력과 시작 모델을 검증합니다. 이후 그 대화를 원래 앱에서 다시 열어 메뉴를 사용합니다. 새 대화는 설치된 고정 제공자를 바로 사용합니다. 다른 기존 대화도 필요할 때 각각 한 번만 재연결합니다.

## 요청·인증 처리

라우터는 `127.0.0.1:18762`에만 바인딩합니다. Codex가 보내는 별도 로컬 헤더 토큰을 검사하고, 알려진 모델 별칭 이외에는 HTTP 400으로 거절합니다. 사용자 설정과 라우터 매니페스트는 소유자 전용 권한으로 저장합니다.

| 메뉴 ID | 실제 모델 | 목적지 | 인증 |
| --- | --- | --- | --- |
| `gpt-6-sol` | `gpt-6-sol` | `chatgpt.com/backend-api/codex/responses` | 기존 ChatGPT bearer |
| `mindlogic/gpt-6-sol` | `gpt-6-sol` | `factchat-cloud.mindlogic.ai/v1/gateway/responses` | 기존 `FACTCHAT_API_KEY` |

OpenAI 인증 헤더는 Mindlogic에 전달하지 않습니다. 라우터는 SSE 데이터를 순서대로 전달하고 클라이언트가 취소하면 upstream 연결을 닫습니다. `store=false`를 유지하고 `previous_response_id`·`conversation` 같은 서버 종속 참조가 있으면 명시적으로 거절합니다. 제공자를 바꾸거나 라우터가 재시작된 첫 요청에서는 다른 제공자의 암호화된 reasoning 항목을 제외합니다. 보이는 대화 이력과 도구 호출·결과 항목은 유지합니다. 연결 실패 시 다른 제공자로 자동 우회하지 않습니다. 로그에는 별칭, 실제 모델, 목적지, 인증 **종류**, 요청 ID, HTTP 상태만 남기며 토큰과 본문을 기록하지 않습니다.

## 확인 결과 (2026-09-24)

| 항목 | 결과 | 근거 |
| --- | --- | --- |
| 내장 `model/list` | PASS | OpenAI 7개와 Mindlogic 7개가 함께 반환됨 |
| 설치된 CLI의 제공자 왕복 | PASS | `OpenAI → Mindlogic → OpenAI` 각 요청 HTTP 200, 라우터 목적지·인증 종류 기록 |
| Mindlogic 모델 간 ID | PASS (CLI) | `gpt-6-sol`과 `gpt-6-luna`의 별칭·upstream 모델 ID 확인 |
| Mindlogic 스트리밍 | PASS (CLI) | Responses SSE로 정상 응답 |
| Mindlogic 도구 왕복 | PASS (CLI) | Gateway HTTP 200 두 번, `command_execution` 시작·완료와 `turn.completed` 확인 |
| 기본 앱 메뉴 표시 | PASS (사용자 관찰) | 앱 재시작 후 OpenAI와 Mindlogic 항목이 함께 표시됨 |
| 기본 앱 메뉴 클릭·왕복 | BLOCKED | OpenAI 한도 초과 상태에서 Mindlogic 항목 선택 후 앱이 입력을 막음. 해당 시도에 대응하는 라우터 요청 기록 없음 |
| 같은 대화의 제공자 전환 | PASS (임시 app-server) | 같은 threadId의 두 turn에서 Mindlogic → OpenAI 목적지·인증 종류 변경, 모두 HTTP 200 |
| 지정된 기존 대화 재연결 | PASS (모델 요청 전) | 동일 threadId·이력·프로젝트 경로로 라우터 제공자를 저장; 앱 메뉴 클릭 검증은 별도 |
| 무인증 로컬 요청 | PASS | HTTP 401 |
| 복구 | PASS (모의) | 설치·반복 설치 거부·메뉴 선택 후 설정 복원 시험 통과 |

`python3 -m unittest -v test_menu.py`는 TOML의 주석·따옴표 키·작은따옴표 문자열·여러 줄 문자열, 백업 이름, 설치·복구, 모의 제공자 분기·키 분리·SSE·도구 항목·오류를 검사합니다. CLI 결과는 기본 앱 메뉴 조작 성공을 대신하지 않습니다. 현재 주목표인 **기본 앱에서 재시작·터미널 명령 없이 OpenAI → Mindlogic → OpenAI 전환**은 달성되지 않았습니다. 사용자가 현재 `factchat` 직접 연결로 돌린 설정은 그대로 두었습니다. 한도 차단의 정확한 위치와 지원되는 해결 인터페이스가 확인되기 전까지 라우터를 다시 활성화하거나 테스트용 OpenAI 요청을 보내지 마세요.

## 제거와 복구

```bash
python3 setup.py menu-remove
```

제거는 서비스와 이 작업이 만든 카탈로그·매니페스트를 지우고 설치 전 `config.toml`의 관련 키를 복원합니다. 설치 후 사용자가 다른 설정을 바꿨다면 그 내용은 유지합니다. 라우터 관련 설정을 다른 값으로 바꾼 경우 자동 덮어쓰기를 거부합니다. 제거 뒤 앱을 다시 열면 원래 메뉴가 반영됩니다. 원본 백업은 고유한 `.backup-<UUID>` 이름으로 남습니다. 이전 `codex-profile` 사용자가 수정한 파일은 설치·제거 과정에서 건드리지 않습니다.

이전 `setup.py install/openai/mindlogic/mindlogic-thread` 명령은 기존 프로필 방식으로 남아 있습니다. 이 경로는 전역 설정을 바꾸므로 이번 **메뉴 선택만으로 전환**하는 사용법과 혼용하지 마세요.
