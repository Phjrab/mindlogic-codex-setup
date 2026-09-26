#!/usr/bin/env python3
"""Retry safe, model-free Mindlogic reconnection for unloaded user threads."""

from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import plistlib
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import tomllib


HOME = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser()
CONFIG = HOME / "config.toml"
CATALOG = HOME / "mindlogic-models.json"
MENU_MANIFEST = HOME / "mindlogic-menu-routes.json"
DB = HOME / "state_5.sqlite"
SCRIPT = HOME / "bin" / "mindlogic-thread-sync.py"
SWITCHER = HOME / "bin" / "mindlogic-thread-switch.py"
LOCK = HOME / "mindlogic-thread-sync.lock"
LABEL = "ai.mindlogic.codex-thread-sync"
AGENT = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


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
    config = tomllib.loads(CONFIG.read_text())
    provider_mode = config.get("model_provider")
    if provider_mode == "factchat" and CATALOG.is_file():
        available = {model["slug"] for model in json.loads(CATALOG.read_text())["models"]}
        legacy_providers = ("openai", "mindlogic_menu_router")
    elif provider_mode == "mindlogic_menu_router" and MENU_MANIFEST.is_file():
        routes = json.loads(MENU_MANIFEST.read_text())["routes"]
        legacy_providers = ("openai", "factchat")
    else:
        return []
    with sqlite3.connect(f"file:{DB}?mode=ro", uri=True) as database:
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


def run_once() -> tuple[int, int, int]:
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    with LOCK.open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return (0, 0, 0)
        switched = locked = failed = 0
        switcher = SWITCHER if SWITCHER.is_file() else Path(__file__).with_name("setup.py")
        if not switcher.is_file():
            return (0, 0, 1)
        router_mode = tomllib.loads(CONFIG.read_text()).get("model_provider") == "mindlogic_menu_router"
        for thread_id, _model, archived in candidates():
            try:
                command = [sys.executable, str(switcher),
                           "menu-thread" if router_mode else "mindlogic-thread", thread_id]
                if archived and not router_mode:
                    command.append("--preserve-archive")
                result = subprocess.run(
                    command, text=True, capture_output=True, timeout=120, check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                failed += 1
                continue
            if result.returncode == 0:
                switched += 1
            elif ("active writer" in result.stderr or "아직 로드" in result.stderr or
                  "still loaded" in result.stderr):
                locked += 1
            else:
                failed += 1
        return (switched, locked, failed)


def launch(action: str) -> None:
    domain = f"gui/{os.getuid()}"
    command = (["launchctl", "bootstrap", domain, str(AGENT)] if action == "bootstrap"
               else ["launchctl", "bootout", f"{domain}/{LABEL}"])
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError(f"launchctl {action}: {result.stderr.strip()[:200]}")


def install() -> None:
    if any(path.exists() for path in (SCRIPT, SWITCHER, AGENT)):
        raise RuntimeError("Thread sync is already installed or its target files exist")
    origin = Path(__file__).resolve()
    switcher_source = origin.with_name("setup.py")
    if not switcher_source.is_file():
        raise RuntimeError("setup.py must be next to thread_sync.py")
    plist = plistlib.dumps({
        "Label": LABEL,
        "ProgramArguments": [sys.executable, str(SCRIPT)],
        "RunAtLoad": True,
        "StartInterval": 60,
        "StandardOutPath": str(HOME / "mindlogic-thread-sync.log"),
        "StandardErrorPath": str(HOME / "mindlogic-thread-sync.err"),
    })
    made = []
    try:
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
    print("Removed thread sync; conversation providers were not changed.")


def main() -> None:
    action = sys.argv[1] if len(sys.argv) > 1 else "run"
    try:
        if action == "install":
            install()
        elif action == "remove":
            remove()
        elif action == "run":
            switched, locked, failed = run_once()
            print(f"Thread sync: switched={switched} waiting_for_writer={locked} errors={failed}")
        else:
            raise RuntimeError("Usage: thread_sync.py [install|run|remove]")
    except (OSError, ValueError, RuntimeError, tomllib.TOMLDecodeError) as error:
        raise SystemExit(str(error)) from error


if __name__ == "__main__":
    main()
