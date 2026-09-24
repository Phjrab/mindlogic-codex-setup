#!/usr/bin/env python3
"""Install and switch a Mindlogic model profile for local Codex."""

from __future__ import annotations

import copy
import getpass
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
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
    candidates = [shutil.which("codex"), "/Applications/ChatGPT.app/Contents/Resources/codex"]
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
            [codex_executable(), "debug", "models"],
            env=env,
            text=True,
            capture_output=True,
            check=False,
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
        model["supported_reasoning_levels"] = [
            level for level in model.get("supported_reasoning_levels", [])
            if level.get("effort") in {"low", "medium", "high"}
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
        "openai_model": top_level_value(current, "model") if top_level_value(current, "model_provider") in (None, "openai") else "gpt-6-astra",
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
    print("새 Codex 채팅을 열어 적용하세요. 모델 메뉴가 갱신되지 않으면 앱을 재시작하세요.")


def status() -> None:
    current = CONFIG.read_text() if CONFIG.exists() else ""
    print("Provider:", top_level_value(current, "model_provider") or "openai (default)")
    print("Model:", top_level_value(current, "model") or "Codex default")


def main() -> None:
    action = sys.argv[1] if len(sys.argv) == 2 else None
    if action == "install":
        install()
    elif action in ("openai", "mindlogic"):
        switch(action)
    elif action == "status":
        status()
    else:
        fail("Usage: python3 setup.py {install|openai|mindlogic|status}")


if __name__ == "__main__":
    main()
