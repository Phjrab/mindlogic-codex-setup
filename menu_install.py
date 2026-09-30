"""Install, refresh, inspect and remove the native Codex model-menu router."""

from __future__ import annotations

import copy
import hashlib
from http.client import HTTPConnection
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import tomllib
import uuid

import setup
import platform_support
from menu_router import read_mindlogic_key


HOME = setup.CODEX_HOME
CONFIG = setup.CONFIG
CATALOG = HOME / "mindlogic-menu-models.json"
MANIFEST = HOME / "mindlogic-menu-routes.json"
STATE = HOME / "mindlogic-menu-state.json"
ROUTER = HOME / "bin" / "mindlogic-menu-router.py"
LABEL = "ai.mindlogic.codex-menu-router"
AGENT = platform_support.service_path(HOME, LABEL)
PORT = 18762
PROVIDER = "mindlogic_menu_router"
MANAGED_KEYS = ("model", "model_provider", "model_catalog_json")
_previous_router_instance = None


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
    cached = json.loads(cache_path.read_text(encoding="utf-8-sig"))["models"]
    bundled = setup.native_catalog()["models"]
    templates = {model["slug"]: model for model in bundled}
    templates.update({model["slug"]: model for model in cached})
    openai = copy.deepcopy(cached)
    # The custom picker catalog can outlive Codex's account cache after an app update.
    # Expose the native 6.1 Sol entry while keeping every cached account model intact.
    if "gpt-6.1-sol" not in {item["slug"] for item in openai}:
        native_61 = next((item for item in bundled if item["slug"] == "gpt-6.1-sol"), None)
        if native_61 is not None:
            item = copy.deepcopy(native_61)
            item["priority"] = 0
            openai.insert(0, item)
    routes = {item["slug"]: {"provider": "openai", "model": item["slug"]} for item in openai}
    mindlogic = []
    unsupported = []
    preferred_slugs = ("gpt-6.1-sol", "gpt-6-astra", "gpt-6-sol", "gpt-6-luna")
    ordered_slugs = preferred_slugs + tuple(
        slug for slug in setup.MODEL_NAMES if slug not in preferred_slugs
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
    return platform_support.service_definition(HOME, LABEL, ROUTER,
        ["--manifest", str(MANIFEST), "--env-file", str(setup.ENV_FILE), "--port", str(PORT)],
        log_prefix="mindlogic-menu-router")


def launch(action: str) -> None:
    global _previous_router_instance
    _previous_router_instance = None
    if action == "kickstart":
        log = HOME / "mindlogic-menu-router.log"
        if log.is_file():
            for line in reversed(log.read_text(encoding="utf-8-sig").splitlines()):
                try:
                    item = json.loads(line)
                except ValueError:
                    continue
                if item.get("event") == "listening":
                    _previous_router_instance = item.get("instance_id")
                    break
    platform_support.launch_service(action, AGENT, LABEL)


def await_router_health(seconds: float | None = None) -> None:
    if seconds is None:
        seconds = 22 if platform_support.is_windows() else 6
    deadline = time.monotonic() + seconds
    expected = sha256(json.loads(MANIFEST.read_text(encoding="utf-8-sig"))["local_token"])[:16]
    while time.monotonic() < deadline:
        connection = HTTPConnection("127.0.0.1", PORT, timeout=0.5)
        try:
            connection.request("GET", "/health")
            response = connection.getresponse()
            body = response.read()
            payload = json.loads(body)
            if (response.status == 200 and payload.get("router_id") == expected
                    and payload.get("instance_id") and payload["instance_id"] != _previous_router_instance):
                return
        except (OSError, ValueError):
            pass
        finally:
            connection.close()
        time.sleep(0.2)
    raise RuntimeError("Router service did not become healthy after launch")


def install(*, preserve_default: bool = False) -> None:
    if STATE.exists():
        raise ValueError("Menu router is already installed; use menu-refresh or menu-remove")
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", PORT))
        except OSError as error:
            raise ValueError(f"Router port {PORT} is in use; refusing to replace another service") from error
    source = CONFIG.read_text(encoding="utf-8-sig") if CONFIG.exists() else ""
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
    profile_path = HOME / "mindlogic-menu.config.toml"
    if preserve_default and profile_path.exists():
        raise ValueError("Mindlogic menu profile already exists; refusing to overwrite it")
    profile_text = edit_config("", values, provider_section=section)
    state = {"original_keys": top_level_lines(source), "original_values": {key: parsed.get(key) for key in MANAGED_KEYS},
             "installed_values": values, "provider_section": section, "config_before_sha256": sha256(source),
             "config_after_sha256": sha256(updated), "initial_config_existed": CONFIG.exists()}
    state["preserve_default"] = preserve_default
    if preserve_default:
        state["config_after_sha256"] = sha256(source)
        state["profile_path"] = str(profile_path)
    if AGENT.exists() or ROUTER.exists() or CATALOG.exists() or MANIFEST.exists():
        raise ValueError("A menu-router file already exists; refusing to overwrite it")
    if (CONFIG.read_text(encoding="utf-8-sig") if CONFIG.exists() else "") != source:
        raise ValueError("Codex configuration changed during installation; retry without overwriting it")
    config_backup = backup(CONFIG) if not preserve_default else None
    made = []
    service_started = False
    try:
        files = [
            (CATALOG, json.dumps(catalog, ensure_ascii=False, indent=2).encode() + b"\n", 0o600),
            (MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=2).encode() + b"\n", 0o600),
            (ROUTER, (Path(__file__).with_name("menu_router.py")).read_bytes(), 0o700),
            (AGENT, agent_plist(), 0o600),
            (STATE, json.dumps(state, ensure_ascii=False, indent=2).encode() + b"\n", 0o600),
        ]
        files.append((profile_path if preserve_default else CONFIG,
                      (profile_text if preserve_default else updated).encode("utf-8"), 0o600))
        if platform_support.is_windows():
            for path, data in platform_support.runtime_files(HOME):
                if path.exists():
                    if path.read_bytes() != data:
                        raise ValueError(f"Existing runtime differs: {path}; update it explicitly")
                else:
                    files.append((path, data, 0o700))
        if preserve_default:
            cli = HOME / "bin" / "mindlogic-cli.py"
            command = HOME / "bin" / "mindlogic-menu.cmd"
            if cli.exists() or (platform_support.is_windows() and command.exists()):
                raise ValueError("Profile launcher already exists")
            files.append((cli, Path(__file__).with_name("mindlogic_cli.py").read_bytes(), 0o700))
            if platform_support.is_windows():
                # Select UTF-8 before cmd.exe reads the non-ASCII Python path.
                cmd = '@echo off\r\nchcp 65001 >nul\r\n' + subprocess.list2cmdline([sys.executable, "-X", "utf8"]) + ' "%~dp0mindlogic-cli.py" %*\r\n'
                files.append((command, cmd.encode("utf-8"), 0o700))
        for path, data, mode in files:
            atomic_bytes(path, data, mode)
            made.append(path)
        launch("bootstrap")
        service_started = True
        await_router_health()
    except BaseException:
        if service_started:
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
    if preserve_default:
        print(f"OpenAI default preserved. Separate CLI profile: codex --profile mindlogic-menu")
        print(f"Key-loading launcher: python {HOME / 'bin' / 'mindlogic-cli.py'}")
        print("Desktop default activation requires the explicit menu-activate command.")


def refresh() -> None:
    if not STATE.exists():
        raise ValueError("Menu router is not installed")
    catalog, manifest = catalog_and_routes()
    manifest["local_token"] = json.loads(MANIFEST.read_text(encoding="utf-8-sig"))["local_token"]
    current = tomllib.loads(CONFIG.read_text(encoding="utf-8-sig"))
    if current.get("model_provider") == PROVIDER and current.get("model") not in manifest["routes"]:
        raise ValueError("Current picker model is no longer available; choose a supported model before refreshing")
    updates = {ROUTER: Path(__file__).with_name("menu_router.py").read_bytes()}
    if platform_support.is_windows():
        updates.update(platform_support.runtime_files(HOME))
    cli = HOME / "bin" / "mindlogic-cli.py"
    if cli.exists():
        updates[cli] = Path(__file__).with_name("mindlogic_cli.py").read_bytes()
    originals = {path: path.read_bytes() if path.exists() else None for path in (CATALOG, MANIFEST, *updates)}
    for path in originals:
        backup(path)
    try:
        atomic_bytes(CATALOG, json.dumps(catalog, ensure_ascii=False, indent=2).encode() + b"\n")
        atomic_bytes(MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=2).encode() + b"\n")
        for path, data in updates.items():
            atomic_bytes(path, data, 0o700)
        launch("kickstart")
        await_router_health()
    except BaseException:
        for path, data in originals.items():
            if data is None:
                path.unlink(missing_ok=True)
            else:
                atomic_bytes(path, data, 0o700 if path in updates else 0o600)
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
    state = json.loads(STATE.read_text(encoding="utf-8-sig"))
    old_section = state["provider_section"]
    targets = [CONFIG]
    if state.get("profile_path"):
        targets.append(Path(state["profile_path"]))
    sources = {path: path.read_text(encoding="utf-8-sig") for path in targets if path.exists()}
    sources = {path: source for path, source in sources.items() if PROVIDER in tomllib.loads(source).get("model_providers", {})}
    if not sources or any(source.count(old_section) != 1 for source in sources.values()):
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
    updates = {path: source.replace(old_section, new_section, 1) for path, source in sources.items()}
    for updated in updates.values():
        tomllib.loads(updated)
    state["provider_section"] = new_section
    if CONFIG in updates:
        state["config_after_sha256"] = sha256(updates[CONFIG])
    originals = {path: path.read_bytes() for path in (*updates, STATE, ROUTER)}
    for path in originals:
        backup(path)
    try:
        atomic_bytes(ROUTER, Path(__file__).with_name("menu_router.py").read_bytes(), 0o700)
        atomic_bytes(STATE, json.dumps(state, ensure_ascii=False, indent=2).encode() + b"\n")
        for path, updated in updates.items():
            atomic_bytes(path, updated.encode("utf-8"), path.stat().st_mode & 0o777)
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
    source_bytes = CONFIG.read_bytes()
    source = CONFIG.read_text(encoding="utf-8-sig")
    parsed = tomllib.loads(source)
    state = json.loads(STATE.read_text(encoding="utf-8-sig"))
    section = state.get("provider_section", "")
    add_section = bool(state.get("preserve_default") and PROVIDER not in parsed.get("model_providers", {}))
    if not section or (not add_section and source.count(section) != 1):
        raise ValueError("Installed router provider settings changed; refusing to overwrite them")
    routes = json.loads(MANIFEST.read_text(encoding="utf-8-sig")).get("routes", {})
    catalog_models = {item.get("slug") for item in json.loads(CATALOG.read_text(encoding="utf-8-sig")).get("models", [])}
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
    if (state.get("preserve_default") and parsed.get("model_provider", "openai") == "openai"
            and current_model in catalog_models and routes.get(current_model, {}).get("provider") == "openai"):
        selected = current_model
    if selected is None:
        raise ValueError("Current model has no installed Mindlogic menu entry; choose a supported model before activating")
    values = {"model": selected, "model_provider": PROVIDER, "model_catalog_json": str(CATALOG)}
    updated = edit_config(source, values, provider_section=section if add_section else None)
    if updated == source:
        print("Mindlogic menu is already active")
        return
    backup(CONFIG)
    atomic_bytes(CONFIG, updated.encode(), CONFIG.stat().st_mode & 0o777)
    try:
        launch("kickstart")
        await_router_health()
    except BaseException:
        atomic_bytes(CONFIG, source_bytes, CONFIG.stat().st_mode & 0o777)
        launch("kickstart")
        raise
    print("Mindlogic model menu activated; existing thread providers were not changed")


def status() -> None:
    installed = STATE.exists()
    parsed = tomllib.loads(CONFIG.read_text(encoding="utf-8-sig")) if CONFIG.exists() else {}
    print("Installed:", installed)
    print("Configured provider:", parsed.get("model_provider", "openai"))
    print("Configured model:", parsed.get("model", "Codex default"))
    print("Catalog:", parsed.get("model_catalog_json", "Codex default"))
    print("Router service:", "configured" if AGENT.exists() else "absent")
    if installed:
        state = json.loads(STATE.read_text(encoding="utf-8-sig"))
        if state.get("profile_path"):
            print("Separate CLI profile:", state["profile_path"])
    if installed and parsed.get("model_provider") != PROVIDER:
        print("Default configuration uses the original provider; the separate profile can use the router.")
    print("Observed app thread provider: unverified")
    print("Observed upstream request: unverified (see secret-free router log after a user request)")


def remove() -> None:
    if not STATE.exists():
        raise ValueError("Menu router is not installed")
    state = json.loads(STATE.read_text(encoding="utf-8-sig"))
    source = CONFIG.read_text(encoding="utf-8-sig")
    parsed = tomllib.loads(source)
    routes = json.loads(MANIFEST.read_text(encoding="utf-8-sig"))["routes"]
    profile_path = Path(state["profile_path"]) if state.get("profile_path") else None
    if profile_path:
        profile_source = profile_path.read_text(encoding="utf-8-sig")
        profile = tomllib.loads(profile_source)
        if (profile_source.count(state["provider_section"]) != 1 or profile.get("model") not in routes
                or profile.get("model_provider") != PROVIDER or profile.get("model_catalog_json") != str(CATALOG)):
            raise ValueError("Separate profile changed; refusing automatic removal")
    if state.get("preserve_default") and PROVIDER not in parsed.get("model_providers", {}):
        launch("bootout")
        for path in (AGENT, ROUTER, MANIFEST, CATALOG, STATE, profile_path,
                     HOME / "bin" / "mindlogic-cli.py", HOME / "bin" / "mindlogic-menu.cmd"):
            if path:
                path.unlink(missing_ok=True)
        print("Removed separate menu profile and router; OpenAI default configuration was preserved.")
        platform_support.cleanup_runtime(HOME)
        return
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
    if profile_path:
        for path in (profile_path, HOME / "bin" / "mindlogic-cli.py", HOME / "bin" / "mindlogic-menu.cmd"):
            path.unlink(missing_ok=True)
    platform_support.cleanup_runtime(HOME)
    print("Removed menu router. Reopen Codex to restore its original picker.")


def switch_thread(thread_id: str) -> None:
    """One-time migration of an unloaded user chat to the fixed router provider."""
    setup.switch_thread_to_menu(thread_id)


def switch_thread_to_mindlogic(thread_id: str) -> None:
    """Select the Mindlogic route for an existing, unloaded thread without a model call."""
    if str(uuid.UUID(thread_id)) != thread_id:
        raise ValueError("Use the exact canonical threadId")
    if not STATE.exists() or tomllib.loads(CONFIG.read_text(encoding="utf-8-sig")).get("model_provider") != PROVIDER:
        raise ValueError("Activate the menu router first")
    routes = json.loads(MANIFEST.read_text(encoding="utf-8-sig"))["routes"]
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
