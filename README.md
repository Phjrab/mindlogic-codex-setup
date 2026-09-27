# Codex 앱에서 OpenAI와 Mindlogic 모델 선택하기

Codex 데스크톱 앱의 **기존 모델 메뉴**에 `Mindlogic · ...` 항목을 추가합니다. 메뉴에서 모델을 바꾸면 같은 채팅의 실제 API 목적지도 OpenAI 또는 Mindlogic으로 바뀝니다. 매번 터미널에서 공급자를 변경할 필요는 없습니다.

macOS Codex/ChatGPT 앱 **26.924.22138**, 내장 CLI **0.158.0-alpha.2.1**에서 확인했습니다. 같은 채팅에서 `Mindlogic · GPT-6 Luna → GPT-6 Luna → Mindlogic · GPT-6 Luna`를 선택했을 때 Mindlogic·OpenAI·Mindlogic 응답을 확인했습니다. 앱이 업데이트되면 두 목적지의 응답을 다시 확인하세요.

## 준비물

- macOS의 Codex 앱에 로그인한 계정
- Python **3.11 이상**, Git, `curl`
- 본인에게 발급된 **Mindlogic Gateway API 키**와 사용 가능한 잔액

설정은 사용자 계정의 `~/.codex/`에 저장됩니다. API 키를 GitHub나 채팅에 올리지 마세요. 이 저장소에는 키가 포함되지 않습니다.

## 처음 설치

1. 저장소를 받습니다.

   ```bash
   git clone https://github.com/Phjrab/mindlogic-codex-setup.git
   cd mindlogic-codex-setup
   ```

2. 터미널에서 `mkdir -p ~/.codex`를 실행하고 `nano ~/.codex/.env`를 엽니다. 아래 한 줄을 넣되 `발급받은_키`를 본인 키로 바꿉니다. 이미 파일이 있다면 다른 설정은 유지하고 `FACTCHAT_API_KEY` 줄의 값만 바꾸세요. 같은 이름의 줄은 하나만 남겨야 합니다.

   ```text
   FACTCHAT_API_KEY=발급받은_키
   ```

   `Ctrl+O` → Enter로 저장하고 `Ctrl+X`로 나옵니다. 이어서 `chmod 600 ~/.codex/.env`를 실행합니다.

3. 메뉴와 로컬 라우터를 설치합니다.

   ```bash
   python3 setup.py menu-install
   python3 setup.py menu-status
   ```

   설치는 본인 Mindlogic 계정에서 사용 가능한 모델을 조회하고 기존 Codex 설정을 백업합니다. `~/.codex/config.toml`에 메뉴 카탈로그와 로컬 공급자를 등록하고, `127.0.0.1:18762`에서만 듣는 사용자 LaunchAgent를 설치합니다. 이미 설치했다면 다시 `menu-install`을 실행하지 마세요.

4. **Codex 앱을 완전히 종료하고 다시 실행**합니다. 새 채팅의 모델 메뉴에서 `Mindlogic · GPT-6 Astra`가 `Mindlogic · GPT-6 Sol`보다 위에 표시됩니다. Astra가 본인 Mindlogic 계정에 없다면 해당 항목은 추가되지 않습니다.

새 채팅에서 `Mindlogic · GPT-6 Luna`로 짧게 질문한 뒤, 같은 채팅에서 `GPT-6 Luna`로 바꿔 다시 질문해 보세요. `Mindlogic · ...` 항목은 Mindlogic Gateway로, 접두사 없는 GPT 항목은 기존 OpenAI 경로로 전송됩니다. **채팅의 저장된 공급자 이름은 로컬 라우터로 유지**되며 실제 API 목적지는 선택한 모델에 따라 매 요청마다 바뀝니다.

## 기존 채팅 연결

설치 전에 OpenAI 또는 Mindlogic 직접 공급자로 시작한 채팅은 처음 한 번 라우터에 연결해야 같은 채팅에서 두 제공자를 고를 수 있습니다. **일반 사용자 채팅만** 대상으로 하며, 대화 ID·이력·작업 경로·보관 상태를 유지합니다. 앱이 현재 사용 중인 채팅과 내부 검토·하위 에이전트 채팅은 건드리지 않습니다. 연결 과정에서 모델 요청은 보내지 않습니다.

**Codex 앱을 완전히 종료한 뒤**, 설치한 저장소 폴더에서 아래 명령을 한 번 실행합니다. 앱을 종료해야 열어 두었던 채팅의 writer도 풀립니다.

```bash
python3 thread_sync.py migrate-all
```

현재 메뉴에서 지원하는 기존 모델은 그대로 유지합니다. 메뉴에서 없어진 구형 모델을 사용한 채팅은 **현재 설정의 같은 공급자 모델**로 바꾸고, 그것도 없다면 같은 공급자의 사용 가능한 모델로 바꿉니다. 바뀐 채팅 ID와 모델은 터미널에 표시됩니다. 완료 후 앱을 다시 실행하면 기존 채팅에서도 두 공급자의 모델을 선택할 수 있습니다. `switched`는 연결한 채팅 수이고 `waiting_for_writer`가 0이 아니면 앱이 아직 사용 중인 채팅이 있다는 뜻입니다. 이 경우 앱을 완전히 종료했는지 확인하고 같은 명령을 다시 실행하세요. 이미 연결된 채팅은 다시 바꾸지 않습니다.

앞으로 생길 미연결 채팅을 자동 재시도하도록 하려면 선택적으로 아래 명령을 한 번 실행합니다. 사용자 LaunchAgent가 60초마다 **기존 모델을 유지할 수 있는 채팅**을 확인합니다.

```bash
python3 thread_sync.py install
```

이 명령은 채팅이 진행 중일 때 공급자를 강제로 바꾸지 않습니다. 특정 채팅 하나만 연결하려면 유휴 상태에서 `python3 setup.py menu-thread <threadId>`를 사용합니다. 지원 모델만 원래대로 보존하며 전환하려면 `python3 thread_sync.py run`을 사용합니다.

## 평소 사용과 문제 해결

- **키 변경:** `nano ~/.codex/.env`에서 `FACTCHAT_API_KEY` 값만 바꿉니다. 라우터가 요청할 때마다 읽으므로 앱 재시작이 필요 없습니다.
- **Mindlogic HTTP 402:** 해당 키의 사용량 또는 잔액을 확인하세요. 다른 키로 바꾼 후 다시 요청할 수 있습니다. 오류가 나도 OpenAI로 자동 우회하지 않습니다.
- **모델이 메뉴에 없음:** `python3 setup.py menu-status`로 설치 상태를 확인합니다. Mindlogic 계정에 새 모델이 추가됐다면 `python3 setup.py menu-refresh` 후 앱을 다시 실행합니다.
- **기존 채팅만 예전 공급자로 감:** `python3 thread_sync.py run`을 다시 실행합니다. 사용 중이라 건너뛴 채팅은 닫힌 뒤 재시도할 수 있습니다.
- **설치 당시와 다른 앱 버전:** 모델 메뉴와 양쪽 실제 응답을 다시 확인하세요. 라우터의 OpenAI 목적지 경로는 현재 앱에서 관찰한 값이며 공개 안정성 계약은 아닙니다.

라우터 로그는 `~/.codex/mindlogic-menu-router.log`에 있습니다. 여기에는 선택한 별칭, 실제 목적지, HTTP 상태가 남고 API 키와 대화 본문은 기록하지 않습니다. OpenAI 로그인 토큰은 OpenAI 경로에만, Mindlogic 키는 Mindlogic 경로에만 전달합니다. 제공자마다 본인 계정의 사용량 정책이 적용됩니다.

## 제거

자동 채팅 연결을 설치했다면 `python3 thread_sync.py remove`로 중지할 수 있습니다. 메뉴 설치를 제거하려면 `python3 setup.py menu-remove`를 실행합니다. **이미 라우터 공급자로 저장된 채팅은 제거 전에 다른 공급자로 옮겨야 이어서 사용할 수 있습니다.** 설치 전 설정의 백업은 `~/.codex/`에 보존됩니다.

이전 실험과 검증 기록은 [docs/legacy-notes.md](docs/legacy-notes.md)에 있습니다.
