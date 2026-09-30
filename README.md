# Codex 앱에서 OpenAI와 Mindlogic 모델 선택하기

Codex 데스크톱 앱의 **기존 모델 메뉴**에 `Mindlogic · ...` 항목을 추가합니다. 메뉴를 활성화하면 같은 채팅에서 모델을 선택해 실제 API 목적지를 OpenAI 또는 Mindlogic으로 바꿀 수 있습니다.

## 환경 선택

| 🍎 [macOS](#macos) | 🪟 [Windows](#windows) |
| :---: | :---: |
| 터미널 · `python3` · LaunchAgent | PowerShell · `python` · 작업 스케줄러 |

사용하는 환경의 링크를 누르고 **설치·사용 안내를 펼치세요**. GitHub README에서 지원하는 [접기/펼치기 형식](https://docs.github.com/en/get-started/writing-on-github/working-with-advanced-formatting/organizing-information-with-collapsed-sections)을 사용합니다.

### 설치 기본 동작의 차이

| 항목 | macOS | Windows |
| --- | --- | --- |
| `menu-install` | 앱 기본 제공자를 로컬 라우터로 변경 | 기존 기본 제공자를 유지하고 별도 CLI 프로필 설치 |
| 앱 모델 메뉴 활성화 | 기본 설치에 포함 | 설치 후 `menu-activate` 실행 |
| 기존 OpenAI 기본 설정 유지 | `menu-install --preserve-default` | 기본 적용 |
| 라우터 자동 실행 | 사용자 LaunchAgent | 사용자 로그온 작업 |

**앱 메뉴 활성화는 기본 제공자를 로컬 라우터로 변경합니다.** 일반 GPT 항목은 OpenAI로, `Mindlogic · ...` 항목은 Mindlogic Gateway로 전송합니다. 기존 채팅의 저장된 제공자는 설치·활성화만으로 바뀌지 않습니다.

### 검증 환경

- **macOS 원본:** Codex/ChatGPT 앱 **26.924.22138**, 내장 CLI **0.158.0-alpha.2.1**. 같은 채팅에서 Mindlogic → OpenAI → Mindlogic 전환과 실제 응답을 확인했습니다. Windows 수정 후 macOS 실기기 재검증은 수행하지 않았습니다.
- **Windows 수정본:** Python **3.12.10**, Codex CLI **0.159.0**. 작업 스케줄러 설치·갱신·제거, 별도 프로필의 OpenAI·Mindlogic 실제 응답, 38개 회귀 테스트를 확인했습니다. 앱 메뉴 활성화 후 app-server의 `model/list`에서 Mindlogic **8개**를 포함한 표시 대상 모델 **16개**도 확인했습니다. 재시작 후 데스크톱 GUI의 실제 표시와 사용자 채팅 일괄 전환은 아직 검증하지 않았습니다. 상세 기록은 [WINDOWS-VERIFICATION.txt](WINDOWS-VERIFICATION.txt)에 있습니다.

이 저장소는 macOS와 Windows를 지원합니다. 아래 명령은 `setup.py`가 있는 저장소 폴더에서 실행합니다. 기존 설치는 해당 환경의 `menu-refresh` 명령으로 모델 목록을 갱신합니다.

**GPT-6.1 Sol 지원:** Mindlogic 계정에서 모델을 사용할 수 있고 Codex 내장 목록 또는 로그인한 계정의 모델 캐시에 일치하는 메타데이터가 있어야 합니다. 메뉴 이름은 `Mindlogic · GPT-6.1 Sol`, CLI 모델 ID는 `mindlogic--gpt-6.1-sol`입니다. Mindlogic 계정에 없거나 일치하는 메타데이터가 없는 모델은 해당 항목을 추가하지 않습니다. Codex 업데이트 후 OpenAI 계정 캐시가 이전 버전이라도 내장 목록에 `gpt-6.1-sol`이 있으면 `GPT-6.1-Sol` 항목도 추가합니다. OpenAI 계정에 접근 권한이 없는 경우 해당 OpenAI 요청은 거부될 수 있습니다.

## macOS

<details>
<summary><strong>🍎 macOS 설치·사용 안내 펼치기</strong></summary>

### 1. 준비 및 저장소 받기

Codex 앱에 로그인하고 Python **3.11 이상**, Git, `curl`을 준비합니다. 본인 Mindlogic Gateway API 키와 사용 가능한 잔액이 필요합니다.

```bash
python3 --version
curl --version
codex --version

git clone https://github.com/Phjrab/mindlogic-codex-setup.git
cd mindlogic-codex-setup
```

수정본 ZIP을 받았다면 압축을 풀고 그 폴더로 이동합니다.

### 2. API 키 저장

```bash
mkdir -p ~/.codex
nano ~/.codex/.env
```

아래 한 줄의 값을 본인 키로 바꿉니다. 기존 내용은 유지하고 같은 이름의 줄은 하나만 남깁니다.

```text
FACTCHAT_API_KEY=발급받은_키
```

`Ctrl+O` → Enter로 저장하고 `Ctrl+X`로 나온 뒤 파일 권한을 설정합니다.

```bash
chmod 600 ~/.codex/.env
```

### 3. 앱 모델 메뉴 설치

**이 명령은 앱 기본 제공자를 로컬 라우터로 변경합니다.** 설치 전 설정을 백업하고 계정에서 사용 가능한 모델을 조회합니다.

```bash
python3 setup.py menu-install
python3 setup.py menu-status
```

기존 기본 제공자를 유지하고 별도 CLI 프로필만 설치하려면 첫 번째 명령 대신 아래 명령을 사용합니다.

```bash
python3 setup.py menu-install --preserve-default
codex --profile mindlogic-menu
```

별도 프로필 설치 후 앱 메뉴도 활성화하려면 다음을 실행합니다.

```bash
python3 setup.py menu-activate
```

이미 설치했다면 다시 설치하는 대신 `python3 setup.py menu-refresh`를 사용합니다. 라우터는 사용자 LaunchAgent로 실행하고 `127.0.0.1:18762`에서만 듣습니다.

### 4. 사용 확인

앱 메뉴를 활성화한 경우 **Codex 앱을 완전히 종료하고 다시 실행**합니다. 새 채팅의 모델 메뉴에서 `Mindlogic · GPT-6 Luna`로 짧게 질문하고, 같은 채팅에서 `GPT-6 Luna`로 바꿔 다시 질문합니다.

`Mindlogic · GPT-6.1 Sol`을 먼저 표시하고, 이어서 `Mindlogic · GPT-6 Astra`, `Mindlogic · GPT-6 Sol`, `Mindlogic · GPT-6 Luna` 순으로 표시합니다. 본인 Mindlogic 계정에서 제공하지 않는 모델은 추가되지 않습니다. 채팅의 저장된 제공자는 로컬 라우터로 유지되며, 실제 API 목적지는 선택한 모델에 따라 매 요청마다 바뀝니다.

### 5. 기존 채팅 연결 — 선택

앱 메뉴 활성화 후 [기존 채팅 연결](#기존-채팅-연결)의 설명을 확인하고, **앱을 완전히 종료한 뒤** 실행합니다.

```bash
python3 thread_sync.py migrate-all
```

특정 유휴 채팅 하나만 연결하려면 `대화_ID`를 실제 ID로 바꿉니다.

```bash
python3 setup.py menu-thread "대화_ID"
```

자동 재시도를 설치하려면 다음 명령을 사용합니다. 사용자 LaunchAgent가 60초마다 확인합니다.

```bash
python3 thread_sync.py install
```

### 6. 갱신 및 제거

```bash
# 상태 확인
python3 setup.py menu-status
# 모델 목록과 라우터 갱신
python3 setup.py menu-refresh
```

갱신 후 앱을 다시 실행해 모델 목록을 확인합니다. 제거할 때는 아래 명령을 사용합니다. **라우터로 연결한 채팅은 제거 전에 다른 제공자로 옮겨야 이어서 사용할 수 있습니다.**

```bash
# 자동 채팅 연결을 설치한 경우 제거
python3 thread_sync.py remove
# 메뉴와 라우터 제거
python3 setup.py menu-remove
```

</details>

## Windows

<details>
<summary><strong>🪟 Windows 설치·사용 안내 펼치기</strong></summary>

### 1. 준비 및 수정본 폴더로 이동

Codex 앱에 로그인하고 Python **3.11 이상**, `curl.exe`, Codex CLI를 준비합니다. PowerShell의 `ScheduledTasks` 모듈을 사용합니다. 본인 Mindlogic Gateway API 키와 사용 가능한 잔액이 필요합니다.

PowerShell에서 저장소를 받고 폴더로 이동한 뒤 실행 환경을 확인합니다. Git 대신 ZIP을 받았다면 압축을 풀고 `setup.py`가 있는 폴더로 이동합니다.

```powershell
git clone https://github.com/Phjrab/mindlogic-codex-setup.git
cd mindlogic-codex-setup
python --version
curl.exe --version
codex --version
Get-Module -ListAvailable ScheduledTasks
```

### 2. API 키 저장

```powershell
New-Item -ItemType Directory -Force "$env:USERPROFILE\.codex" | Out-Null
notepad "$env:USERPROFILE\.codex\.env"
```

아래 한 줄의 값을 본인 키로 바꾸고 **UTF-8**로 저장합니다. 기존 내용은 유지하고 같은 이름의 줄은 하나만 남깁니다.

```text
FACTCHAT_API_KEY=발급받은_키
```

### 3. 라우터와 별도 CLI 프로필 설치

```powershell
python setup.py menu-install
python setup.py menu-status
```

Windows에서는 `--preserve-default`가 기본 적용됩니다. 평소의 앱과 `codex`는 기존 기본 제공자를 유지합니다. 별도 프로필은 `$env:USERPROFILE\.codex\mindlogic-menu.config.toml`입니다.

라우터는 사용자 로그온 작업으로 등록하고 `127.0.0.1:18762`에서만 듣습니다. 사용자 로그인 중에 동작하며 관리자 실행 수준이나 비밀번호 저장이 필요하지 않습니다. 설치 실패 시 생성한 파일과 작업을 정리합니다. 이미 설치했다면 `python setup.py menu-refresh`를 사용합니다.

### 4. 별도 CLI 사용

```powershell
# 메뉴 프로필 실행
& "$env:USERPROFILE\.codex\bin\mindlogic-menu.cmd"
# Mindlogic 모델 사용
& "$env:USERPROFILE\.codex\bin\mindlogic-menu.cmd" --model mindlogic--gpt-6.1-sol
# OpenAI 모델 사용
& "$env:USERPROFILE\.codex\bin\mindlogic-menu.cmd" --model gpt-6-luna
```

다음 중 하나로도 실행할 수 있습니다.

```powershell
python "$env:USERPROFILE\.codex\bin\mindlogic-cli.py"
codex --profile mindlogic-menu
```

`--ignore-user-config`는 프로필도 무시하므로 함께 사용하지 마세요.

### 5. Codex 앱 모델 메뉴 활성화 — 선택

**이 명령은 앱 기본 제공자를 로컬 라우터로 변경합니다.** 선택된 OpenAI 모델의 실제 목적지는 OpenAI로 유지하지만 요청이 라우터를 거칩니다. 앱 메뉴에서 Mindlogic을 선택하려는 경우 실행합니다.

```powershell
python setup.py menu-activate
python setup.py menu-status
```

완료 후 **Codex 앱을 완전히 종료하고 다시 실행**합니다. 새 채팅의 모델 메뉴에서 `Mindlogic · ...` 항목을 확인합니다. 처음부터 앱 메뉴까지 설치하려면 `menu-install` 대신 `python setup.py menu-install --activate`를 사용합니다.

### 6. 기존 채팅 연결 — 선택

앱 메뉴 활성화 후 [기존 채팅 연결](#기존-채팅-연결)의 설명을 확인하고, **앱을 완전히 종료한 뒤** 실행합니다.

```powershell
python thread_sync.py migrate-all
```

특정 유휴 채팅 하나만 연결하려면 `대화_ID`를 실제 ID로 바꿉니다.

```powershell
python setup.py menu-thread "대화_ID"
```

자동 재시도를 설치하려면 다음 명령을 사용합니다. 사용자 작업 스케줄러가 60초마다 확인합니다.

```powershell
python thread_sync.py install
```

### 7. 갱신 및 제거

```powershell
# 상태 확인
python setup.py menu-status
# 모델 목록과 라우터 갱신
python setup.py menu-refresh
```

갱신 후 앱을 다시 실행해 모델 목록을 확인합니다. 제거할 때는 아래 명령을 사용합니다. **라우터로 연결한 채팅은 제거 전에 다른 제공자로 옮겨야 이어서 사용할 수 있습니다.**

```powershell
# 자동 채팅 연결을 설치한 경우 제거
python thread_sync.py remove
# 메뉴와 라우터 제거
python setup.py menu-remove
```

별도 프로필만 사용한 상태에서 제거하면 기본 설정을 그대로 유지합니다.

### Mindlogic 전용 CLI만 설치하는 경우

로컬 라우터 없이 Mindlogic 전용 CLI를 사용하려면 메뉴 설치 대신 아래 명령을 사용합니다. 기본 OpenAI 설정을 유지하는 `mindlogic.config.toml` 프로필을 만들며 기존 프로필 파일을 덮어쓰지 않습니다.

```powershell
python setup.py install
& "$env:USERPROFILE\.codex\bin\mindlogic.cmd"
```

`python setup.py install --activate`와 `codex-profile.cmd mindlogic`은 기본 제공자를 직접 변경하는 명령입니다. 이전 직접 전환 방식은 [docs/legacy-notes.md](docs/legacy-notes.md)를 참고하세요.

</details>

## 명령어 빠른 참조

`setup.py`가 있는 폴더에서 실행합니다. **채팅 연결 명령은 앱 메뉴 활성화 후 사용합니다.**

| 작업 | macOS 터미널 | Windows PowerShell |
| --- | --- | --- |
| 기본 제공자 유지하며 설치 | `python3 setup.py menu-install --preserve-default` | `python setup.py menu-install` |
| 앱 메뉴 활성화 | `python3 setup.py menu-activate` | `python setup.py menu-activate` |
| 상태 확인 | `python3 setup.py menu-status` | `python setup.py menu-status` |
| 모델·라우터 갱신 | `python3 setup.py menu-refresh` | `python setup.py menu-refresh` |
| API 키 수정 | `nano ~/.codex/.env` | `notepad "$env:USERPROFILE\.codex\.env"` |
| 기존 채팅 일괄 연결 | `python3 thread_sync.py migrate-all` | `python thread_sync.py migrate-all` |
| 지원 모델을 유지할 수 있는 채팅만 연결 | `python3 thread_sync.py run` | `python thread_sync.py run` |
| 자동 연결 설치 | `python3 thread_sync.py install` | `python thread_sync.py install` |
| 자동 연결 제거 | `python3 thread_sync.py remove` | `python thread_sync.py remove` |
| 메뉴·라우터 제거 | `python3 setup.py menu-remove` | `python setup.py menu-remove` |

## 기존 채팅 연결

설치 전에 OpenAI 또는 Mindlogic 직접 제공자로 시작한 채팅은 처음 한 번 라우터에 연결해야 같은 채팅에서 두 제공자를 고를 수 있습니다. 앱 메뉴를 활성화한 뒤 플랫폼별 `migrate-all` 명령을 사용합니다.

- **실행 전에 Codex 앱을 완전히 종료합니다.** 앱이 사용 중인 채팅과 내부 검토·하위 에이전트 채팅은 건너뜁니다.
- 일반 사용자 채팅의 **대화 ID·이력·작업 경로·보관 상태**를 유지하며 연결 과정에서 모델 요청은 보내지 않습니다.
- 현재 메뉴에서 지원하는 기존 모델은 유지합니다. 구형 모델은 현재 설정의 같은 제공자 모델로 바꾸고, 그것도 없으면 같은 제공자의 사용 가능한 모델로 바꿉니다. 변경된 ID와 모델을 출력합니다.
- `switched`는 연결된 채팅 수입니다. `waiting_for_writer`가 0이 아니면 앱을 완전히 종료했는지 확인한 뒤 재실행합니다. 이미 연결된 채팅은 다시 바꾸지 않습니다.
- `run`과 자동 연결은 기존 모델을 유지할 수 있는 채팅만 연결합니다. 진행 중인 채팅의 제공자를 강제로 바꾸지 않습니다.

완료 후 앱을 다시 실행합니다. 기존 사용자 채팅의 일괄 연결은 Windows 실사용 환경에서 아직 검증하지 않았습니다.

## 평소 사용과 문제 해결

| 증상·작업 | 확인 방법 |
| --- | --- |
| 키 변경 | 위 표의 환경별 편집 명령으로 `FACTCHAT_API_KEY` 값을 바꿉니다. 라우터가 요청마다 읽으므로 앱 재시작은 필요 없습니다. |
| Mindlogic HTTP 402 | 해당 키의 사용량·잔액을 확인합니다. 오류가 나도 OpenAI로 자동 우회하지 않습니다. |
| 모델이 메뉴에 없음 | `menu-status`로 상태를 확인합니다. Windows는 앱 메뉴 활성화 여부도 확인합니다. 계정에 새 모델이 추가됐다면 `menu-refresh` 후 앱을 다시 실행합니다. |
| 기존 채팅만 예전 제공자로 감 | 앱 메뉴를 활성화한 뒤 기존 채팅 연결 절차를 진행합니다. 사용 중이라 건너뛴 채팅은 닫힌 뒤 재시도합니다. |
| 설치 당시와 다른 앱 버전 | 모델 메뉴와 양쪽 실제 응답을 다시 확인합니다. OpenAI 목적지 경로는 현재 앱에서 관찰한 값이며 공개 안정성 계약은 아닙니다. |

기본 설정 위치는 macOS `~/.codex/`, Windows `$env:USERPROFILE\.codex\`이며 사용자 지정 `CODEX_HOME`도 지원합니다. 위 키 저장·실행 경로는 기본 위치 기준입니다. 설정 백업은 같은 폴더에 보존됩니다.

라우터 로그는 설정 폴더의 `mindlogic-menu-router.log`입니다. 선택한 별칭, 실제 목적지, HTTP 상태를 기록하며 API 키와 대화 본문은 기록하지 않습니다. OpenAI 로그인 토큰은 OpenAI 경로에만, Mindlogic 키는 Mindlogic 경로에만 전달합니다. 제공자마다 본인 계정의 사용량 정책이 적용됩니다. **API 키를 GitHub나 채팅에 올리지 마세요.**

이전 실험과 검증 기록은 [docs/legacy-notes.md](docs/legacy-notes.md)에 있습니다.
