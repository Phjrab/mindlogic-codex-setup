"""Install, refresh, inspect and remove the native Codex model-menu router."""

from __future__ import annotations

import copy
import hashlib
from http.client import HTTPConnection
import json
import os
from pathlib import Path
import plistlib
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
import uuid

import setup
from menu_router import read_mindlogic_key


HOME = setup.CODEX_HOME
CONFIG = setup.CONFIG
CATALOG = HOME / "mindlogic-menu-models.json"
MANIFEST = HOME / "mindlogic-menu-routes.json"
STATE = HOME / "mindlogic-menu-state.json"
ROUTER = HOME / "bin" / "mindlogic-menu-router.py"
LABEL = "ai.mindlogic.codex-menu-router"
AGENT = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
PORT = 18762
PROVIDER = "mindlogic_menu_router"
MANAGED_KEYS = ("model", "model_provider", "model_catalog_json")


def mindlogic_alias(slug: str) -> str:
    # The desktop picker drops the provider segment of slash-separated slugs.
    return f"mindlogic--{slug}"


def mindlogic_alias_for_model(model: str, routes: dict) -> str:
    upstream_model = model
    for prefix in ("mindlogic/", "mindlogic--"):
        if upstream_model.startswith(prefix):
            upstream_model = upstream_model.removeprefix(prefix)
            break
    alias = mindlogic_alias(upstream_model)
    if alias not in routes or routes[alias].get("provider") != "mindlogic":
        raise ValueError("This model is not available through Mindlogic")
    return alias


def sha256(data: str) -> str:
    return hashlib.sha256(data.encode()).hexdigest()


def atomic_bytes(path: Path, data: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def backup(path: Path) -> Path | None:
    if not path.exists():
        return None
    target = path.with_name(path.name + ".backup-" + uuid.uuid4().hex)
    with path.open("rb") as source, target.open("xb") as destination:
        shutil.copyfileobj(source, destination)
    os.chmod(target, path.stat().st_mode & 0o777)
    return target


def heading_indexes(source: str) -> list[int]:
    """Find TOML table lines outside quoted and multiline strings."""
    headings = []
    triple = None
    for index, line in enumerate(source.splitlines(keepends=True)):
        if triple is None and line.lstrip().startswith("["):
            headings.append(index)
        i = 0
        quote = None
        while i < len(line):
            if triple:
                if line.startswith(triple, i):
                    triple = None
                    i += 3
                else:
                    i += 1
                continue
            if quote:
                if quote == '"' and line[i] == "\\":
                    i += 2
                elif line[i] == quote:
                    quote = None
                    i += 1
                else:
                    i += 1
                continue
            if line[i] == "#":
                break
            if line.startswith("'''", i) or line.startswith('"""', i):
                triple = line[i:i + 3]
                i += 3
            elif line[i] in "\"'":
                quote = line[i]
                i += 1
            else:
                i += 1
    return headings


def managed_assignment(line: str) -> str | None:
    match = re.match(r'^\s*(?:([A-Za-z_][A-Za-z0-9_-]*)|"([^"]+)"|\'([^\']+)\')\s*=', line)
    if not match:
        return None
    key = next(part for part in match.groups() if part is not None)
    return key if key in MANAGED_KEYS else None


def top_level_lines(source: str) -> dict[str, str]:
    lines = source.splitlines(keepends=True)
    limit = next(iter(heading_indexes(source)), len(lines))
    found = {}
    for line in lines[:limit]:
        key = managed_assignment(line)
        if key:
            found[key] = line
    return found


def edit_config(source: str, values: dict[str, str], *, provider_section: str | None = None) -> str:
    tomllib.loads(source)
    lines = source.splitlines(keepends=True)
    limit = next(iter(heading_indexes(source)), len(lines))
    prefix = [line for line in lines[:limit] if managed_assignment(line) is None]
    inserted = [f"{key} = {json.dumps(value)}\n" for key, value in values.items()]
    result = "".join(inserted + prefix + lines[limit:])
    if provider_section:
        if PROVIDER in tomllib.loads(source).get("model_providers", {}):
            raise ValueError("Provider ID already exists; refusing to replace user settings")
        result = result.rstrip() + "\n\n" + provider_section
    tomllib.loads(result)
    return result


def catalog_and_routes() -> tuple[dict, dict]:
    key = read_mindlogic_key(setup.ENV_FILE)
    available = setup.account_models(key)
    cache_path = HOME / "models_cache.json"
    if not cache_path.is_file():
        raise ValueError("OpenAI account model cache is absent; cannot preserve the current picker")
    cached = json.loads(cache_path.read_text())["models"]
    bundled = setup.native_catalog()["models"]
    templates = {model["slug"]: model for model in bundled}
    templates.update({model["slug"]: model for model in cached})
    openai = copy.deepcopy(cached)
    routes = {item["slug"]: {"provider": "openai", "model": item["slug"]} for item in openai}
    mindlogic = []
    unsupported = []
    ordered_slugs = ("gpt-6-astra", "gpt-6-sol", "gpt-6-luna") + tuple(
        slug for slug in setup.MODEL_NAMES if slug not in ("gpt-6-astra", "gpt-6-sol", "gpt-6-luna")
    )
    for slug in ordered_slugs:
        if slug not in available:
            continue
        name = setup.MODEL_NAMES[slug]
        template = templates.get(slug)
        if template is None:
            unsupported.append(slug)
            continue
        item = copy.deepcopy(template)
        alias = mindlogic_alias(slug)
        item.update(slug=alias, display_name=f"Mindlogic · {name}",
                    description="Mindlogic Gateway", visibility="list",
                    supported_in_api=True, supports_search_tool=False,
                    additional_speed_tiers=[], service_tiers=[],
                    priority=100 + len(mindlogic))
        item["supported_reasoning_levels"] = [level for level in item.get("supported_reasoning_levels", [])
            if level.get("effort") in ({"low", "medium", "high", "xhigh"} if slug == "gpt-5.5"
                                       else {"low", "medium", "high", "xhigh", "max"})]
        mindlogic.append(item)
        routes[alias] = {"provider": "mindlogic", "model": slug}
        routes[f"mindlogic/{slug}"] = {"provider": "mindlogic", "model": slug}
    if not mindlogic:
        raise ValueError("No Mindlogic model has verified matching Codex metadata")
    return {"models": openai + mindlogic}, {"routes": routes, "unsupported": unsupported}


def agent_plist() -> bytes:
    return plistlib.dumps({"Label": LABEL, "ProgramArguments": [sys.executable, str(ROUTER),
        "--manifest", str(MANIFEST), "--env-file", str(setup.ENV_FILE), "--port", str(PORT)],
        "RunAtLoad": True, "KeepAlive": True,
        "StandardOutPath": str(HOME / "mindlogic-menu-router.log"),
        "StandardErrorPath": str(HOME / "mindlogic-menu-router.err")})


def launch(action: str) -> None:
    domain = f"gui/{os.getuid()}"
    if action == "bootstrap":
        command = ["launchctl", "bootstrap", domain, str(AGENT)]
    elif action == "kickstart":
        command = ["launchctl", "kickstart", "-k", f"{domain}/{LABEL}"]
    else:
        command = ["launchctl", "bootout", f"{domain}/{LABEL}"]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode and not (action == "bootout" and "could not find" in result.stderr.lower()):
        raise RuntimeError(f"launchctl {action} failed: {result.stderr.strip()[:300]}")


def await_router_health(seconds: float = 6) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        connection = HTTPConnection("127.0.0.1", PORT, timeout=0.5)
        try:
            connection.request("GET", "/health")
            if connection.getresponse().status == 200:
                return
        except OSError:
            pass
        finally:
            connection.close()
        time.sleep(0.2)
    raise RuntimeError("Router service did not become healthy after launch")


def install() -> None:
    if STATE.exists():
        raise ValueError("Menu router is already installed; use menu-refresh or menu-remove")
    source = CONFIG.read_text() if CONFIG.exists() else ""
    parsed = tomllib.loads(source)
    if parsed.get("model_provider", "openai") not in ("openai", "factchat"):
        raise ValueError("Another custom provider is active; refusing to replace it")
    catalog, manifest = catalog_and_routes()
    manifest["local_token"] = secrets.token_urlsafe(32)
    current_model = parsed.get("model", "gpt-6-sol")
    if parsed.get("model_provider") == "factchat" and mindlogic_alias(current_model) in manifest["routes"]:
        selected = mindlogic_alias(current_model)
    else:
        selected = current_model if current_model in manifest["routes"] else next(
            x for x in manifest["routes"] if x.startswith("mindlogic--"))
    section = (f'[model_providers.{PROVIDER}]\nname = "Codex model menu router"\n'
               f'base_url = "http://127.0.0.1:{PORT}"\nwire_api = "responses"\n'
               f'requires_openai_auth = true\nhttp_headers = {{ X-Mindlogic-Router-Token = "{manifest["local_token"]}" }}\n')
    values = {"model": selected, "model_provider": PROVIDER, "model_catalog_json": str(CATALOG)}
    updated = edit_config(source, values, provider_section=section)
    state = {"original_keys": top_level_lines(source), "original_values": {key: parsed.get(key) for key in MANAGED_KEYS},
             "installed_values": values, "provider_section": section, "config_before_sha256": sha256(source),
             "config_after_sha256": sha256(updated), "initial_config_existed": CONFIG.exists()}
    if AGENT.exists() or ROUTER.exists() or CATALOG.exists() or MANIFEST.exists():
        raise ValueError("A menu-router file already exists; refusing to overwrite it")
    if (CONFIG.read_text() if CONFIG.exists() else "") != source:
        raise ValueError("Codex configuration changed during installation; retry without overwriting it")
    config_backup = backup(CONFIG)
    made = []
    try:
        for path, data, mode in [
            (CATALOG, json.dumps(catalog, ensure_ascii=False, indent=2).encode() + b"\n", 0o600),
            (MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=2).encode() + b"\n", 0o600),
            (ROUTER, (Path(__file__).with_name("menu_router.py")).read_bytes(), 0o700),
            (AGENT, agent_plist(), 0o600),
            (CONFIG, updated.encode(), 0o600),
            (STATE, json.dumps(state, ensure_ascii=False, indent=2).encode() + b"\n", 0o600),
        ]:
            atomic_bytes(path, data, mode)
            made.append(path)
        launch("bootstrap")
        await_router_health()
    except BaseException:
        if AGENT in made:
            try:
                launch("bootout")
            except RuntimeError:
                pass
        if config_backup:
            atomic_bytes(CONFIG, config_backup.read_bytes(), config_backup.stat().st_mode & 0o777)
        elif CONFIG in made:
            CONFIG.unlink(missing_ok=True)
        for path in made:
            if path != CONFIG:
                path.unlink(missing_ok=True)
        raise
    print(f"Installed: {len(catalog['models'])} picker models, {len(manifest['routes'])} routes")
    print("First application may require reopening the Codex app.")
    print("ChatGPT login authenticates OpenAI routes; Mindlogic uses FACTCHAT_API_KEY.")


def refresh() -> None:
    if not STATE.exists():
        raise ValueError("Menu router is not installed")
    catalog, manifest = catalog_and_routes()
    manifest["local_token"] = json.loads(MANIFEST.read_text())["local_token"]
    current = tomllib.loads(CONFIG.read_text())
    if current.get("model_provider") == PROVIDER and current.get("model") not in manifest["routes"]:
        raise ValueError("Current picker model is no longer available; choose a supported model before refreshing")
    originals = {path: path.read_bytes() for path in (CATALOG, MANIFEST, ROUTER)}
    for path in originals:
        backup(path)
    try:
        atomic_bytes(CATALOG, json.dumps(catalog, ensure_ascii=False, indent=2).encode() + b"\n")
        atomic_bytes(MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=2).encode() + b"\n")
        atomic_bytes(ROUTER, Path(__file__).with_name("menu_router.py").read_bytes(), 0o700)
        launch("kickstart")
        await_router_health()
    except BaseException:
        for path, data in originals.items():
            atomic_bytes(path, data, 0o700 if path == ROUTER else 0o600)
        launch("kickstart")
        raise
    print(f"Refreshed {len(catalog['models'])} picker models; restart may be needed to reload the new catalog")


def isolate_local_auth() -> None:
    set_chatgpt_auth(False)


def enable_chatgpt_auth() -> None:
    """Let the supported Codex auth layer supply and refresh ChatGPT credentials."""
    set_chatgpt_auth(True)


def set_chatgpt_auth(enabled: bool) -> None:
    """Update the installed local provider without changing the selected provider or model."""
    if not STATE.is_file() or not ROUTER.is_file():
        raise ValueError("Menu router is not installed")
    state = json.loads(STATE.read_text())
    source = CONFIG.read_text()
    old_section = state["provider_section"]
    if source.count(old_section) != 1:
        raise ValueError("Installed router provider settings changed; refusing to overwrite them")
    desired = str(enabled).lower()
    previous = str(not enabled).lower()
    new_section = old_section.replace(f"requires_openai_auth = {previous}\n",
                                      f"requires_openai_auth = {desired}\n")
    if new_section == old_section:
        if f"requires_openai_auth = {desired}\n" in old_section:
            print(f"Router ChatGPT authentication is already {desired}")
            return
        raise ValueError("Installed router authentication setting is unrecognized")
    updated = source.replace(old_section, new_section, 1)
    tomllib.loads(updated)
    state["provider_section"] = new_section
    state["config_after_sha256"] = sha256(updated)
    originals = {path: path.read_bytes() for path in (CONFIG, STATE, ROUTER)}
    for path in originals:
        backup(path)
    try:
        atomic_bytes(ROUTER, Path(__file__).with_name("menu_router.py").read_bytes(), 0o700)
        atomic_bytes(STATE, json.dumps(state, ensure_ascii=False, indent=2).encode() + b"\n")
        atomic_bytes(CONFIG, updated.encode(), CONFIG.stat().st_mode & 0o777)
        launch("kickstart")
        await_router_health()
    except BaseException:
        for path, data in originals.items():
            atomic_bytes(path, data, 0o700 if path == ROUTER else 0o600)
        launch("kickstart")
        raise
    print(f"Router ChatGPT authentication: {desired}; selected provider and model were preserved")


def activate() -> None:
    """Restore the installed menu router as the default without discarding user settings."""
    if not STATE.is_file() or not MANIFEST.is_file() or not CATALOG.is_file():
        raise ValueError("Menu router is not installed; run menu-install first")
    source = CONFIG.read_text()
    parsed = tomllib.loads(source)
    state = json.loads(STATE.read_text())
    section = state.get("provider_section", "")
    if not section or source.count(section) != 1:
        raise ValueError("Installed router provider settings changed; refusing to overwrite them")
    routes = json.loads(MANIFEST.read_text()).get("routes", {})
    catalog_models = {item.get("slug") for item in json.loads(CATALOG.read_text()).get("models", [])}
    current_model = parsed.get("model")
    candidates = []
    if isinstance(current_model, str):
        for prefix in ("mindlogic/", "mindlogic--"):
            if current_model.startswith(prefix):
                candidates.insert(0, mindlogic_alias(current_model.removeprefix(prefix)))
        if mindlogic_alias(current_model) in routes:
            candidates.insert(0, mindlogic_alias(current_model))
        candidates.append(current_model)
    selected = next((candidate for candidate in candidates
                     if candidate in catalog_models and candidate in routes), None)
    if selected is None:
        raise ValueError("Current model has no installed Mindlogic menu entry; choose a supported model before activating")
    values = {"model": selected, "model_provider": PROVIDER, "model_catalog_json": str(CATALOG)}
    updated = edit_config(source, values)
    if updated == source:
        print("Mindlogic menu is already active")
        return
    backup(CONFIG)
    atomic_bytes(CONFIG, updated.encode(), CONFIG.stat().st_mode & 0o777)
    try:
        launch("kickstart")
        await_router_health()
    except BaseException:
        atomic_bytes(CONFIG, source.encode(), CONFIG.stat().st_mode & 0o777)
        launch("kickstart")
        raise
    print("Mindlogic model menu activated; existing thread providers were not changed")


def status() -> None:
    installed = STATE.exists()
    parsed = tomllib.loads(CONFIG.read_text()) if CONFIG.exists() else {}
    print("Installed:", installed)
    print("Configured provider:", parsed.get("model_provider", "openai"))
    print("Configured model:", parsed.get("model", "Codex default"))
    print("Catalog:", parsed.get("model_catalog_json", "Codex default"))
    print("Router service:", "configured" if AGENT.exists() else "absent")
    if installed and parsed.get("model_provider") != PROVIDER:
        print("Menu router is inactive; the user's current provider setting was preserved.")
    print("Observed app thread provider: unverified")
    print("Observed upstream request: unverified (see secret-free router log after a user request)")


def remove() -> None:
    if not STATE.exists():
        raise ValueError("Menu router is not installed")
    state = json.loads(STATE.read_text())
    source = CONFIG.read_text()
    parsed = tomllib.loads(source)
    routes = json.loads(MANIFEST.read_text())["routes"]
    if parsed.get("model") not in routes:
        raise ValueError("Selected model is outside the installed catalog; refusing to overwrite it")
    for key, expected in state["installed_values"].items():
        if key == "model":
            continue
        if parsed.get(key) != expected:
            raise ValueError(f"{key} changed since installation; refusing to overwrite the user's setting")
    section = state["provider_section"]
    if source.count(section) != 1:
        raise ValueError("Router provider table changed since installation; refusing automatic removal")
    prefix = source.replace(section, "", 1)
    lines = prefix.splitlines(keepends=True)
    limit = next(iter(heading_indexes(prefix)), len(lines))
    kept = [line for line in lines[:limit] if managed_assignment(line) is None]
    restored = "".join(list(state["original_keys"].values()) + kept + lines[limit:]).rstrip() + "\n"
    tomllib.loads(restored)
    backup(CONFIG)
    launch("bootout")
    try:
        atomic_bytes(CONFIG, restored.encode(), CONFIG.stat().st_mode & 0o777)
    except BaseException:
        launch("bootstrap")
        raise
    for path in (AGENT, ROUTER, MANIFEST, CATALOG, STATE):
        path.unlink(missing_ok=True)
    print("Removed menu router. Reopen Codex to restore its original picker.")


def switch_thread(thread_id: str) -> None:
    """One-time migration of an unloaded user chat to the fixed router provider."""
    setup.switch_thread_to_menu(thread_id)


def switch_thread_to_mindlogic(thread_id: str) -> None:
    """Select the Mindlogic route for an existing, unloaded thread without a model call."""
    if str(uuid.UUID(thread_id)) != thread_id:
        raise ValueError("Use the exact canonical threadId")
    if not STATE.exists() or tomllib.loads(CONFIG.read_text()).get("model_provider") != PROVIDER:
        raise ValueError("Activate the menu router first")
    routes = json.loads(MANIFEST.read_text())["routes"]
    with setup.AppServer() as server:
        thread = server.call("thread/read", {"threadId": thread_id})["thread"]
        if thread["id"] != thread_id or thread["status"]["type"] != "notLoaded":
            raise ValueError("Thread is still loaded; wait until it is idle and unload only this thread")
        original_model = thread.get("model")
        if not isinstance(original_model, str):
            raise ValueError("Thread has no model ID")
        alias = mindlogic_alias_for_model(original_model, routes)
        upstream_model = routes[alias]["model"]
        cwd, project_id, source = thread["cwd"], thread.get("projectId"), thread.get("source")
        turns = len(thread.get("turns", []))
        resumed = server.call("thread/resume", {
            "threadId": thread_id, "modelProvider": PROVIDER, "model": alias,
        })
        if (resumed["thread"]["id"] != thread_id or resumed["modelProvider"] != PROVIDER
                or resumed["model"] != alias or Path(resumed["cwd"]).resolve() != Path(cwd).resolve()
                or resumed["thread"].get("projectId") != project_id
                or resumed["thread"].get("source") != source
                or len(resumed["thread"].get("turns", [])) != turns):
            raise ValueError("Resume did not preserve the original thread; do not send a prompt")
    with setup.AppServer() as server:
        after = server.call("thread/read", {"threadId": thread_id})["thread"]
        if (after["id"] != thread_id or after["modelProvider"] != PROVIDER
                or after.get("model") != alias or Path(after["cwd"]).resolve() != Path(cwd).resolve()
                or after.get("projectId") != project_id or after.get("source") != source):
            raise ValueError("Mindlogic model alias was not persisted; do not send a prompt")
    print(f"Existing thread now selects Mindlogic for {upstream_model}; no model request was sent")


def main(action: str, thread_id: str | None = None) -> None:
    try:
        if action in ("menu-thread", "menu-thread-mindlogic"):
            if not thread_id:
                raise ValueError(f"Usage: python3 setup.py {action} THREAD_ID")
            (switch_thread_to_mindlogic if action == "menu-thread-mindlogic" else switch_thread)(thread_id)
        else:
            {"menu-install": install, "menu-refresh": refresh, "menu-activate": activate,
             "menu-auth-isolate": isolate_local_auth, "menu-auth-chatgpt": enable_chatgpt_auth,
             "menu-status": status,
             "menu-remove": remove}[action]()
    except (ValueError, RuntimeError, OSError, tomllib.TOMLDecodeError) as error:
        setup.fail(str(error))
