#!/usr/bin/env python3
"""Install and switch a Mindlogic model profile for local Codex."""

from __future__ import annotations

import copy
import getpass
import json
import os
from pathlib import Path
import re
import select
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime


GATEWAY = "https://factchat-cloud.mindlogic.ai/v1/gateway"
MODEL_NAMES = {
    "gpt-6-sol": "GPT-6 Sol",
    "gpt-6-luna": "GPT-6 Luna",
    "gpt-6-astra": "GPT-6 Astra",
    "gpt-5.6-sol": "GPT-5.6 Sol",
    "gpt-5.6-terra": "GPT-5.6 Terra",
    "gpt-5.6-luna": "GPT-5.6 Luna",
    "gpt-5.5": "GPT-5.5",
}
CODEX_HOME = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser()
CONFIG = CODEX_HOME / "config.toml"
CATALOG = CODEX_HOME / "mindlogic-models.json"
PROFILE = CODEX_HOME / "mindlogic-profile.json"
ENV_FILE = CODEX_HOME / ".env"


def fail(message: str) -> None:
    raise SystemExit(message)


def codex_executable() -> str:
    candidates = ["/Applications/ChatGPT.app/Contents/Resources/codex", shutil.which("codex")]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    fail("Codex CLI를 찾지 못했습니다. Codex 앱 또는 CLI를 먼저 설치하세요.")


def read_env_key() -> str | None:
    if os.environ.get("FACTCHAT_API_KEY"):
        return os.environ["FACTCHAT_API_KEY"]
    if not ENV_FILE.exists():
        return None
    for line in ENV_FILE.read_text().splitlines():
        match = re.match(r"^\s*(?:export\s+)?FACTCHAT_API_KEY\s*=\s*(.*?)\s*$", line)
        if match:
            value = match.group(1)
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]
            return value or None
    return None


def account_models(key: str) -> set[str]:
    if any(c in key for c in '\r\n"\\'):
        fail("API 키에 지원되지 않는 문자가 있습니다.")
    config = f'url = "{GATEWAY}/models/?type=llm"\nheader = "Authorization: Bearer {key}"\n'
    result = subprocess.run(
        ["curl", "--silent", "--show-error", "--fail", "--max-time", "20", "--config", "-"],
        input=config,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        fail("Mindlogic 모델 조회에 실패했습니다. 키, 네트워크, 계정 권한을 확인하세요. 키는 저장하지 않았습니다.")
    try:
        payload = json.loads(result.stdout)
        data = payload["data"] if isinstance(payload, dict) else payload
        return {item["id"] for item in data if item.get("type") == "llm"}
    except (KeyError, TypeError, ValueError) as error:
        fail(f"Mindlogic 모델 목록 형식을 해석하지 못했습니다: {type(error).__name__}")


def native_catalog() -> dict:
    with tempfile.TemporaryDirectory(prefix="mindlogic-codex-") as clean_home:
        env = dict(os.environ, CODEX_HOME=clean_home)
        result = subprocess.run(
            [codex_executable(), "debug", "models", "--bundled"],
            env=env,
            text=True,
            capture_output=True,
            check=False,
            timeout=20,
        )
    if result.returncode:
        fail("Codex 모델 메타데이터를 읽지 못했습니다. 이 Codex 버전은 'codex debug models'가 필요합니다.")
    try:
        return json.loads(result.stdout)
    except ValueError:
        fail("Codex 모델 메타데이터가 JSON 형식이 아닙니다.")


def build_catalog(available: set[str]) -> dict:
    native = {model["slug"]: model for model in native_catalog()["models"]}
    models = []
    for slug, name in MODEL_NAMES.items():
        if slug not in available:
            continue
        template = native.get(slug) or native.get("gpt-6-astra")
        if template is None:
            fail("이 Codex 버전에는 GPT-6 Astra 메타데이터가 없습니다. 설치를 중단합니다.")
        model = copy.deepcopy(template)
        model.update(
            slug=slug,
            display_name=f"Mindlogic {name}",
            description="Available through Mindlogic Gateway",
            visibility="list",
            supported_in_api=True,
            priority=len(models) + 1,
            default_reasoning_level="medium",
            supports_search_tool=False,
            additional_speed_tiers=[],
            service_tiers=[],
        )
        allowed_efforts = {"low", "medium", "high", "xhigh"}
        if slug != "gpt-5.5":
            allowed_efforts.add("max")
        model["supported_reasoning_levels"] = [
            level for level in model.get("supported_reasoning_levels", [])
            if level.get("effort") in allowed_efforts
        ]
        models.append(model)
    if not models:
        fail("이 키에서 Codex Responses용 OpenAI 모델 7개 중 사용 가능한 모델을 찾지 못했습니다.")
    return {"models": models}


def backup(path: Path) -> None:
    if path.exists():
        stamp = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%z")
        shutil.copy2(path, path.with_name(f"{path.name}.backup-{stamp}"))


def write_private(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as stream:
            stream.write(data)
        os.chmod(temporary, path.stat().st_mode & 0o777 if path.exists() else 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def top_level_value(config: str, key: str) -> str | None:
    prefix = "".join(line for line in config.splitlines(keepends=True)[:next(
        (i for i, line in enumerate(config.splitlines(keepends=True)) if line.lstrip().startswith("[")),
        len(config.splitlines(keepends=True)),
    )])
    match = re.search(rf'^\s*{re.escape(key)}\s*=\s*"([^"]*)"', prefix, re.MULTILINE)
    return match.group(1) if match else None


def update_config(config: str, *, provider: str, model: str, effort: str, catalog: bool) -> str:
    lines = config.splitlines(keepends=True)
    first_table = next((i for i, line in enumerate(lines) if line.lstrip().startswith("[")), len(lines))
    keys = {"model", "model_provider", "model_reasoning_effort", "model_catalog_json"}
    prefix = [line for line in lines[:first_table] if not any(re.match(rf'^\s*{key}\s*=', line) for key in keys)]
    settings = [
        f'model = {json.dumps(model)}\n',
        f'model_provider = {json.dumps(provider)}\n',
        f'model_reasoning_effort = {json.dumps(effort)}\n',
    ]
    if catalog:
        settings.append(f'model_catalog_json = {json.dumps(str(CATALOG))}\n')
    suffix = lines[first_table:]
    result = "".join(settings + prefix + suffix)
    if provider == "factchat":
        heading = "[model_providers.factchat]"
        provider_lines = [
            f'base_url = {json.dumps(GATEWAY)}\n',
            'env_key = "FACTCHAT_API_KEY"\n',
            'wire_api = "responses"\n',
        ]
        sections = result.splitlines(keepends=True)
        start = next((i for i, line in enumerate(sections) if line.strip() == heading), None)
        if start is None:
            result = result.rstrip() + "\n\n" + heading + "\n" + "".join(provider_lines)
        else:
            end = next((i for i in range(start + 1, len(sections)) if sections[i].lstrip().startswith("[")), len(sections))
            existing = [line for line in sections[start + 1:end] if not re.match(r'^\s*(base_url|env_key|wire_api)\s*=', line)]
            result = "".join(sections[:start + 1] + provider_lines + existing + sections[end:])
    return result


def save_key(key: str) -> None:
    existing = ENV_FILE.read_text() if ENV_FILE.exists() else ""
    updated = re.sub(
        r'^\s*(?:export\s+)?FACTCHAT_API_KEY\s*=.*$',
        f'FACTCHAT_API_KEY={key}',
        existing,
        count=1,
        flags=re.MULTILINE,
    )
    if updated == existing and not re.search(r'^\s*(?:export\s+)?FACTCHAT_API_KEY\s*=', existing, re.MULTILINE):
        updated = existing.rstrip("\n") + f'\nFACTCHAT_API_KEY={key}\n'
    if updated == existing:
        os.chmod(ENV_FILE, 0o600)
        return
    backup(ENV_FILE)
    write_private(ENV_FILE, updated)
    os.chmod(ENV_FILE, 0o600)


def install() -> None:
    CODEX_HOME.mkdir(parents=True, exist_ok=True)
    key = read_env_key()
    entered = key is None
    if entered:
        key = getpass.getpass("Mindlogic API key (입력 내용은 표시되지 않습니다): ").strip()
    if not key:
        fail("API 키가 없어 설치를 중단합니다.")
    available = account_models(key)
    catalog = build_catalog(available)
    current = CONFIG.read_text() if CONFIG.exists() else ""
    previous = {
        "openai_model": (top_level_value(current, "model") or "gpt-6-astra") if top_level_value(current, "model_provider") in (None, "openai") else "gpt-6-astra",
        "openai_effort": top_level_value(current, "model_reasoning_effort") or "medium",
    }
    if PROFILE.exists():
        previous = json.loads(PROFILE.read_text())
    chosen = catalog["models"][0]["slug"]
    updated = update_config(current, provider="factchat", model=chosen, effort="medium", catalog=True)
    save_key(key)
    backup(CONFIG)
    backup(CATALOG)
    write_private(CATALOG, json.dumps(catalog, ensure_ascii=False, indent=2) + "\n")
    write_private(PROFILE, json.dumps(previous, indent=2) + "\n")
    write_private(CONFIG, updated)
    destination = CODEX_HOME / "bin" / "codex-profile"
    destination.parent.mkdir(parents=True, exist_ok=True)
    if Path(__file__).resolve() != destination.resolve():
        shutil.copy2(Path(__file__), destination)
    os.chmod(destination, 0o755)
    print(f"Installed {len(catalog['models'])} Mindlogic models. Active model: {chosen}")
    print(f"Switch profiles with: {destination} openai | mindlogic | status")
    print("Codex 앱을 재시작한 뒤 새 채팅에서 모델 메뉴를 확인하세요.")


def switch(target: str) -> None:
    if not CONFIG.exists() or not PROFILE.exists() or not CATALOG.exists():
        fail("먼저 'python3 setup.py install'을 실행하세요.")
    current = CONFIG.read_text()
    profile = json.loads(PROFILE.read_text())
    if target == "openai":
        provider, model = "openai", profile["openai_model"]
        effort = profile["openai_effort"]
        use_catalog = False
    else:
        provider = "factchat"
        model = json.loads(CATALOG.read_text())["models"][0]["slug"]
        effort = "medium"
        use_catalog = True
    backup(CONFIG)
    write_private(CONFIG, update_config(current, provider=provider, model=model, effort=effort, catalog=use_catalog))
    status()
    print("기본 설정만 변경했습니다. 이미 열린 대화의 실행 제공자는 유지될 수 있습니다.")
    if target == "mindlogic":
        print("기존 대화를 같은 ID로 전환하려면 README의 '기존 대화 전환' 절차를 따르세요.")


def status() -> None:
    current = CONFIG.read_text() if CONFIG.exists() else ""
    print("Provider:", top_level_value(current, "model_provider") or "openai (default)")
    print("Model:", top_level_value(current, "model") or "Codex default")


class AppServer:
    """Short-lived app-server connection for thread metadata and resume only."""

    def __enter__(self):
        self.process = subprocess.Popen(
            [codex_executable(), "app-server", "--stdio"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        self.request_id = 0
        try:
            self.call("initialize", {"clientInfo": {
                "name": "mindlogic_codex_setup",
                "title": "Mindlogic Codex setup",
                "version": "1.0",
            }})
            self.call("initialized", {}, response=False)
        except BaseException:
            self.__exit__()
            raise
        return self

    def __exit__(self, *_exc):
        if self.process.poll() is not None:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)

    def call(self, method: str, params: dict, *, response: bool = True) -> dict | None:
        self.request_id += 1
        request = {"method": method, "params": params}
        if response:
            request["id"] = self.request_id
        self.process.stdin.write(json.dumps(request) + "\n")
        self.process.stdin.flush()
        if not response:
            return None
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            ready, _, _ = select.select(
                [self.process.stdout], [], [], max(0, deadline - time.monotonic())
            )
            if not ready:
                break
            line = self.process.stdout.readline()
            if not line:
                break
            reply = json.loads(line)
            if reply.get("id") != self.request_id:
                continue
            if "error" in reply:
                error = reply["error"]
                fail(f"Codex {method} 실패 ({error.get('code')}): {error.get('message')}")
            return reply["result"]
        fail(f"Codex {method} 응답을 30초 안에 받지 못했습니다.")


def switch_existing_thread(thread_id: str) -> None:
    """Persist Mindlogic on an already-unloaded thread, without a model turn."""
    try:
        if str(uuid.UUID(thread_id)) != thread_id:
            raise ValueError("non-canonical UUID")
    except ValueError:
        fail("정확한 Codex threadId(UUID)를 입력하세요.")
    if not CONFIG.exists() or top_level_value(CONFIG.read_text(), "model_provider") != "factchat":
        fail("먼저 'codex-profile mindlogic'으로 기본 제공자를 설정하세요.")
    if not read_env_key():
        fail("FACTCHAT_API_KEY가 없어 전환을 중단합니다.")
    if not CATALOG.exists():
        fail("Mindlogic 모델 목록이 없습니다. 먼저 설치를 완료하세요.")
    supported = {item["slug"] for item in json.loads(CATALOG.read_text())["models"]}

    with AppServer() as server:
        thread = server.call("thread/read", {"threadId": thread_id})["thread"]
        if thread["id"] != thread_id:
            fail("요청한 대화와 조회된 대화 ID가 다릅니다.")
        if thread["status"]["type"] != "notLoaded":
            fail("대화가 아직 로드되어 있습니다. 앱에서 해당 대화를 보관한 뒤 다시 보관 해제하고, 다른 대화에서 이 명령을 실행하세요.")
        model = thread.get("model") or top_level_value(CONFIG.read_text(), "model")
        if thread.get("modelProvider") == "mindlogic_menu_router" and isinstance(model, str) and model.startswith("mindlogic/"):
            model = model.removeprefix("mindlogic/")
        if model not in supported:
            fail(f"대화 모델 {model!r}은 현재 Mindlogic 모델 목록에 없습니다. 모델을 바꾼 뒤 다시 시도하세요.")
        cwd = thread["cwd"]
        project_id = thread.get("projectId")
        source = thread.get("source")
        resumed = server.call("thread/resume", {
            "threadId": thread_id,
            "modelProvider": "factchat",
            "model": model,
        })
        if (resumed["thread"]["id"] != thread_id or
                resumed["thread"]["modelProvider"] != "factchat" or
                resumed["thread"].get("projectId") != project_id or
                resumed["thread"].get("source") != source or
                resumed["modelProvider"] != "factchat" or
                resumed["model"] != model or Path(resumed["cwd"]).resolve() != Path(cwd).resolve()):
            actual = {"threadId": resumed["thread"]["id"], "modelProvider": resumed["modelProvider"],
                      "model": resumed["model"], "cwd": resumed["cwd"]}
            fail(f"재개 결과가 예상과 다릅니다: {actual}. 요청을 보내지 마세요.")

    # A second process checks persisted state after the first writer exits.
    with AppServer() as server:
        thread = server.call("thread/read", {"threadId": thread_id})["thread"]
        if (thread["id"] != thread_id or thread["modelProvider"] != "factchat" or
                thread.get("model") != model or Path(thread["cwd"]).resolve() != Path(cwd).resolve() or
                thread.get("projectId") != project_id or thread.get("source") != source):
            fail("Mindlogic 실행 제공자가 저장되지 않았습니다. 요청을 보내지 마세요.")
    print(f"동일 대화 {thread_id}의 저장된 실행 제공자: Mindlogic ({model})")
    print("원래 Codex 앱에서 이 대화를 다시 열고, 후속 요청의 실제 목적지를 확인하세요.")


def main() -> None:
    action = sys.argv[1] if len(sys.argv) >= 2 else None
    if action in ("menu-install", "menu-refresh", "menu-status", "menu-remove"):
        if len(sys.argv) != 2:
            fail(f"Usage: python3 setup.py {action}")
        import menu_install
        menu_install.main(action)
    elif action == "menu-thread" and len(sys.argv) == 3:
        import menu_install
        menu_install.main(action, sys.argv[2])
    elif action == "install":
        if len(sys.argv) != 2:
            fail("Usage: python3 setup.py install")
        install()
    elif action in ("openai", "mindlogic"):
        if len(sys.argv) != 2:
            fail(f"Usage: python3 setup.py {action}")
        switch(action)
    elif action == "status":
        if len(sys.argv) != 2:
            fail("Usage: python3 setup.py status")
        status()
    elif action == "mindlogic-thread" and len(sys.argv) == 3:
        switch_existing_thread(sys.argv[2])
    else:
        fail("Usage: python3 setup.py {install|openai|mindlogic|status|mindlogic-thread THREAD_ID}")


if __name__ == "__main__":
    main()
