# Mindlogic 모델을 Codex에서 사용하기

Mindlogic Gateway의 OpenAI 모델을 로컬 Codex 앱과 CLI의 모델 메뉴에 추가하고, Mindlogic과 기본 OpenAI 제공자를 전환하는 설치 스크립트입니다. 각자 계정에서 실제로 조회되는 모델만 추가합니다.

## 확인한 환경

2026-09-24에 macOS의 **ChatGPT/Codex 데스크톱 앱 26.917.51856**(빌드 10492), 앱에 포함된 **codex-cli 0.155.0-alpha.16**에서 설정했습니다. 이 버전은 테스트 환경을 기록한 것이며 최소 지원 버전을 뜻하지 않습니다. 자신의 버전은 앱의 **About** 화면과 `codex --version`으로 확인하세요. 앱에 포함된 CLI는 macOS에서 `/Applications/ChatGPT.app/Contents/Resources/codex --version`으로도 확인할 수 있습니다.

## 설치

필요한 것: Python 3, `curl`, Codex 앱 또는 CLI, 본인에게 발급된 Mindlogic API 키.

```bash
git clone https://github.com/Phjrab/mindlogic-codex-setup.git
cd mindlogic-codex-setup
python3 setup.py install
```

스크립트는 기존 `FACTCHAT_API_KEY` 환경변수 또는 `~/.codex/.env`를 사용합니다. 둘 다 없으면 키를 화면에 표시하지 않고 입력받아 `~/.codex/.env`에 저장합니다. **키를 GitHub, 명령 인수, 채팅에 붙여 넣지 마세요.** 설치 후 Codex 앱을 재시작하고 새 채팅에서 `Mindlogic`으로 시작하는 모델을 고르세요.

설치 과정은 다음과 같습니다.

1. 인증된 `/models/?type=llm` 목록에서 본인 계정의 모델 ID를 확인합니다.
2. 로컬 Codex CLI의 기본 모델 메타데이터로 `~/.codex/mindlogic-models.json`을 생성합니다. 모델 내부 지침이나 개인 설정은 이 저장소에 포함하지 않습니다.
3. `~/.codex/config.toml`에 `factchat` 제공자와 모델 목록 경로를 추가합니다. 기존 설정 파일과 모델 목록은 변경 전 백업합니다.
4. `~/.codex/bin/codex-profile` 명령을 설치합니다.

## 제공자 전환

```bash
~/.codex/bin/codex-profile status
~/.codex/bin/codex-profile openai
~/.codex/bin/codex-profile mindlogic
```

`openai`는 설치 당시의 OpenAI 모델과 추론 강도를 복원합니다. 설치 당시 이미 다른 제공자를 사용 중이면 OpenAI 쪽 모델은 `gpt-6-astra`로 기록합니다. 전환 후 새 채팅을 여세요. 모델 메뉴가 그대로라면 앱을 재시작하세요. 원하는 Mindlogic 모델은 앱 모델 메뉴에서 고를 수 있습니다.

## 모델 범위

스크립트가 인식하는 모델 ID는 `gpt-6-sol`, `gpt-6-luna`, `gpt-6-astra`, `gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-5.6-luna`, `gpt-5.5`입니다. 이 중 본인 키의 목록에 있는 모델만 추가됩니다. 모델 목록에 있더라도 모든 Codex 도구 호출과의 호환성이 보장되지는 않습니다. 작성자의 계정에서는 `gpt-6-sol`의 Codex 도구 호출과 `gpt-6-luna`, `gpt-5.6-sol`의 간단한 Responses 요청을 확인했습니다. 다른 계정이나 버전은 직접 확인해야 합니다.

## 추론 강도

2026-09-24에 Mindlogic Gateway의 짧은 Responses 요청으로 확인한 결과, 위 GPT-6·GPT-5.6 모델은 `xhigh`와 `max`를 받아들였고 GPT-5.5는 `xhigh`를 받아들였습니다. GPT-5.5의 `max`와 GPT-6 Sol의 `ultra`는 HTTP 400으로 거절됐습니다. 따라서 설치 스크립트는 GPT-6·GPT-5.6에 `low`, `medium`, `high`, `xhigh`, `max`를, GPT-5.5에 `low`, `medium`, `high`, `xhigh`를 표시합니다. 기본값은 `medium`입니다.

[OpenAI 추론 문서](https://developers.openai.com/api/docs/guides/reasoning)는 모델별로 높은 단계를 안내하지만, [Mindlogic Responses 문서](https://docs.mindlogic.ai/docs/ynu-ac/api-gateway/reference/responses-api)는 `high`까지만 예시로 적고 있습니다. 위 범위는 작성자 계정의 실제 API 응답에 근거합니다. 다른 계정이나 Gateway 변경 후의 동작은 달라질 수 있습니다.

**Claude 모델은 이 Codex 제공자에 추가하지 않습니다.** [OpenAI Codex 설정 문서](https://learn.chatgpt.com/docs/config-file/config-reference)의 사용자 지정 모델 제공자는 Responses 프로토콜을 사용하며, [Mindlogic 모델 문서](https://docs.mindlogic.ai/docs/general/api-gateway/getting-started/models)는 Claude에 Chat Completions 또는 Anthropic Messages 경로를 안내합니다. Claude를 쓰려면 해당 프로토콜을 지원하는 별도 클라이언트가 필요합니다.

## 원상 복구

설치 또는 전환 전에 생성된 `~/.codex/config.toml.backup-날짜` 파일 중 원하는 것을 `~/.codex/config.toml`로 복사하고 앱을 재시작하세요. `mindlogic-models.json`과 `mindlogic-profile.json`은 더 이상 필요 없으면 삭제할 수 있습니다. 스크립트가 새로 저장한 키는 `~/.codex/.env`에서 `FACTCHAT_API_KEY` 줄을 직접 제거할 수 있습니다.

참고: [Mindlogic 모델 목록](https://docs.mindlogic.ai/docs/general/api-gateway/getting-started/models) · [OpenAI Codex 설정](https://learn.chatgpt.com/docs/config-file/config-reference)
