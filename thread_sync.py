#!/usr/bin/env python3
"""Retry safe, model-free Mindlogic reconnection for unloaded user threads."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sqlite3
from contextlib import closing
import subprocess
import sys
import tempfile
import tomllib
import platform_support


HOME = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser()
CONFIG = HOME / "config.toml"
CATALOG = HOME / "mindlogic-models.json"
MENU_MANIFEST = HOME / "mindlogic-menu-routes.json"
DB = HOME / "state_5.sqlite"
SCRIPT = HOME / "bin" / "mindlogic-thread-sync.py"
SWITCHER = HOME / "bin" / "mindlogic-thread-switch.py"
LOCK = HOME / "mindlogic-thread-sync.lock"
LABEL = "ai.mindlogic.codex-thread-sync"
AGENT = platform_support.service_path(HOME, LABEL)


def atomic_copy(source: Path, target: Path, mode: int) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as output, source.open("rb") as data:
            shutil.copyfileobj(data, output)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)


def candidates() -> list[tuple[str, str, bool]]:
    if not CONFIG.is_file() or not DB.is_file():
        return []
    config = tomllib.loads(CONFIG.read_text(encoding="utf-8-sig"))
    provider_mode = config.get("model_provider")
    if provider_mode == "factchat" and CATALOG.is_file():
        available = {model["slug"] for model in json.loads(CATALOG.read_text(encoding="utf-8-sig"))["models"]}
        legacy_providers = ("openai", "mindlogic_menu_router")
    elif provider_mode == "mindlogic_menu_router" and MENU_MANIFEST.is_file():
        routes = json.loads(MENU_MANIFEST.read_text(encoding="utf-8-sig"))["routes"]
        legacy_providers = ("openai", "factchat")
    else:
        return []
    with closing(sqlite3.connect(DB.resolve().as_uri() + "?mode=ro", uri=True)) as database:
        rows = database.execute("""SELECT id, model_provider, model, archived FROM threads
            WHERE thread_source = 'user'
              AND model_provider IN (?, ?)
            ORDER BY updated_at DESC""", legacy_providers).fetchall()
    selected = []
    for thread_id, provider, model, archived in rows:
        if not isinstance(model, str):
            continue
        if provider_mode == "factchat":
            direct_model = model
            if provider == "mindlogic_menu_router":
                for prefix in ("mindlogic/", "mindlogic--"):
                    if model.startswith(prefix):
                        direct_model = model.removeprefix(prefix)
                        break
            if direct_model in available:
                selected.append((thread_id, direct_model, bool(archived)))
        else:
            alias = f"mindlogic--{model}" if provider == "factchat" else model
            expected = "mindlogic" if provider == "factchat" else "openai"
            if routes.get(alias, {}).get("provider") == expected:
                selected.append((thread_id, alias, bool(archived)))
    return selected


def replacement_candidates() -> list[tuple[str, str, str, bool]]:
    """Find user chats whose old model is absent from the installed router menu."""
    if not CONFIG.is_file() or not DB.is_file() or not MENU_MANIFEST.is_file():
        return []
    config = tomllib.loads(CONFIG.read_text(encoding="utf-8-sig"))
    if config.get("model_provider") != "mindlogic_menu_router":
        return []
    routes = json.loads(MENU_MANIFEST.read_text(encoding="utf-8-sig"))["routes"]
    configured = config.get("model")

    def fallback(provider: str) -> str | None:
        route_provider = "openai" if provider == "openai" else "mindlogic"
        preferred = ([configured, "gpt-6-luna"] if provider == "openai" else
                     [f"mindlogic--{configured}" if isinstance(configured, str) else None,
                      "mindlogic--gpt-6-luna"])
        selectable = []
        for alias, route in routes.items():
            if route.get("provider") != route_provider:
                continue
            if provider == "openai" and alias not in ("gpt-reserve", "codex-auto-review"):
                selectable.append(alias)
            elif provider == "factchat" and alias.startswith("mindlogic--"):
                selectable.append(alias)
        for alias in [*preferred, *selectable]:
            if isinstance(alias, str) and alias in selectable:
                return alias
        return None

    with closing(sqlite3.connect(DB.resolve().as_uri() + "?mode=ro", uri=True)) as database:
        rows = database.execute("""SELECT id, model_provider, model, archived FROM threads
            WHERE thread_source = 'user' AND model_provider IN ('openai', 'factchat')
            ORDER BY updated_at DESC""").fetchall()
    selected = []
    for thread_id, provider, model, archived in rows:
        if not isinstance(model, str):
            continue
        alias = f"mindlogic--{model}" if provider == "factchat" else model
        expected = "mindlogic" if provider == "factchat" else "openai"
        if routes.get(alias, {}).get("provider") == expected:
            continue
        replacement = fallback(provider)
        if replacement is None:
            raise RuntimeError(f"No {expected} menu model is available for chat {thread_id}")
        selected.append((thread_id, model, replacement, bool(archived)))
    return selected


def run_once(include_unsupported: bool = False) -> tuple[int, int, int]:
    if not CONFIG.is_file():
        return (0, 0, 0)
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    with LOCK.open("a+") as lock:
        if not platform_support.try_lock(lock):
            if include_unsupported:
                raise RuntimeError("Another chat migration is running; retry in a moment")
            return (0, 0, 0)
        switched = locked = failed = 0
        checkout_switcher = Path(__file__).with_name("setup.py")
        switcher = checkout_switcher if include_unsupported or not SWITCHER.is_file() else SWITCHER
        if not switcher.is_file():
            return (0, 0, 1)
        router_mode = tomllib.loads(CONFIG.read_text(encoding="utf-8-sig")).get("model_provider") == "mindlogic_menu_router"
        normal = [(thread_id, model, archived, None) for thread_id, model, archived in candidates()]
        replacements = replacement_candidates() if include_unsupported else []
        work = normal + [(thread_id, old_model, archived, new_model)
                         for thread_id, old_model, new_model, archived in replacements]
        for thread_id, old_model, archived, new_model in work:
            try:
                command = [sys.executable, str(switcher),
                           "menu-thread" if router_mode else "mindlogic-thread", thread_id]
                if new_model is not None:
                    command.extend(["--model", new_model])
                if archived and not router_mode:
                    command.append("--preserve-archive")
                result = subprocess.run(
                    command, text=True, encoding="utf-8", capture_output=True, timeout=120, check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                failed += 1
                if include_unsupported:
                    print(f"Chat {thread_id}: migration command failed or timed out")
                continue
            if result.returncode == 0:
                switched += 1
                if new_model is not None:
                    print(f"Chat {thread_id}: unsupported {old_model} -> {new_model}")
            elif ("active writer" in result.stderr or "아직 로드" in result.stderr or
                  "still loaded" in result.stderr):
                locked += 1
                if include_unsupported:
                    print(f"Chat {thread_id}: still in use; close Codex and retry")
            else:
                failed += 1
                if include_unsupported:
                    print(f"Chat {thread_id}: migration failed; try menu-thread for details")
        return (switched, locked, failed)


def launch(action: str) -> None:
    platform_support.launch_service(action, AGENT, LABEL)


def install() -> None:
    if any(path.exists() for path in (SCRIPT, SWITCHER, AGENT)):
        raise RuntimeError("Thread sync is already installed or its target files exist")
    origin = Path(__file__).resolve()
    switcher_source = origin.with_name("setup.py")
    if not switcher_source.is_file():
        raise RuntimeError("setup.py must be next to thread_sync.py")
    plist = platform_support.service_definition(HOME, LABEL, SCRIPT, [],
        interval=60, log_prefix="mindlogic-thread-sync")
    made = []
    try:
        if platform_support.is_windows():
            for target, data in platform_support.runtime_files(HOME):
                if target.exists():
                    if target.read_bytes() != data:
                        raise RuntimeError(f"Existing runtime differs: {target}")
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(data)
                    made.append(target)
        atomic_copy(origin, SCRIPT, 0o700)
        made.append(SCRIPT)
        atomic_copy(switcher_source, SWITCHER, 0o700)
        made.append(SWITCHER)
        AGENT.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=AGENT.parent, prefix=f".{AGENT.name}.", delete=False) as temp:
            temp.write(plist)
            temp.flush()
            os.fsync(temp.fileno())
            temporary = Path(temp.name)
        os.chmod(temporary, 0o600)
        os.replace(temporary, AGENT)
        made.append(AGENT)
        launch("bootstrap")
    except BaseException:
        for path in made:
            path.unlink(missing_ok=True)
        raise
    print("Installed model-free user-thread sync; retries unloaded threads every 60 seconds.")


def remove() -> None:
    if not AGENT.exists():
        raise RuntimeError("Thread sync is not installed")
    launch("bootout")
    for path in (AGENT, SCRIPT, SWITCHER):
        path.unlink(missing_ok=True)
    platform_support.cleanup_runtime(HOME)
    print("Removed thread sync; conversation providers were not changed.")


def main() -> None:
    action = sys.argv[1] if len(sys.argv) > 1 else "run"
    try:
        if action == "install":
            install()
        elif action == "remove":
            remove()
        elif action in ("run", "migrate-all"):
            switched, locked, failed = run_once(include_unsupported=action == "migrate-all")
            print(f"Thread sync: switched={switched} waiting_for_writer={locked} errors={failed}")
        else:
            raise RuntimeError("Usage: thread_sync.py [install|run|migrate-all|remove]")
    except (OSError, ValueError, RuntimeError, tomllib.TOMLDecodeError) as error:
        raise SystemExit(str(error)) from error


if __name__ == "__main__":
    main()
