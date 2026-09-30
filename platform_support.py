"""Per-user services and file locks for macOS and Windows."""

from __future__ import annotations

import errno
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys


def is_windows() -> bool:
    return sys.platform == "win32"


def service_path(home: Path, label: str) -> Path:
    if is_windows():
        return home / "services" / f"{label}.json"
    return Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"


def task_name(label: str, definition: Path) -> str:
    # Isolated CODEX_HOME installations must not stop the real user's router.
    suffix = hashlib.sha256(str(definition.resolve()).casefold().encode()).hexdigest()[:12]
    return f"{label}-{suffix}"


def service_definition(home: Path, label: str, program: Path, arguments: list[str],
                       *, interval: int | None = None, log_prefix: str) -> bytes:
    if is_windows():
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        return (json.dumps({"home": str(home), "python": str(pythonw if pythonw.is_file() else Path(sys.executable)),
            "runner": str(home / "bin" / "mindlogic-service.py"), "program": str(program),
            "arguments": arguments, "interval": interval,
            "stdout": str(home / f"{log_prefix}.log"),
            "stderr": str(home / f"{log_prefix}.err")}, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if sys.platform != "darwin":
        raise RuntimeError("Background services currently support macOS and Windows only")
    data = {"Label": label, "ProgramArguments": [sys.executable, str(program), *arguments],
            "RunAtLoad": True, "EnvironmentVariables": {"CODEX_HOME": str(home), "PYTHONUTF8": "1"},
            "StandardOutPath": str(home / f"{log_prefix}.log"),
            "StandardErrorPath": str(home / f"{log_prefix}.err")}
    data["StartInterval" if interval else "KeepAlive"] = interval if interval else True
    return plistlib.dumps(data)


def runtime_files(home: Path) -> list[tuple[Path, bytes]]:
    root = Path(__file__).parent
    return [(home / "bin" / name, (root / source).read_bytes()) for name, source in (
        ("platform_support.py", "platform_support.py"),
        ("mindlogic-service.py", "service_runner.py"),
    )]


def cleanup_runtime(home: Path) -> None:
    if not is_windows():
        return
    if ((home / "mindlogic-menu-state.json").exists()
            or service_path(home, "ai.mindlogic.codex-thread-sync").exists()):
        return
    for name in ("platform_support.py", "mindlogic-service.py"):
        (home / "bin" / name).unlink(missing_ok=True)


def _powershell(script: str) -> None:
    executable = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
    if not executable:
        raise RuntimeError("PowerShell is required to manage Windows scheduled tasks")
    script = "[Console]::OutputEncoding = [Text.UTF8Encoding]::new(); $ErrorActionPreference = 'Stop';\n" + script
    result = subprocess.run([executable, "-NoProfile", "-NonInteractive", "-Command", script],
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode:
        raise RuntimeError("Windows scheduled task failed: " + result.stderr.strip()[:500])


def launch_service(action: str, definition: Path, label: str) -> None:
    if is_windows():
        name = task_name(label, definition).replace("'", "''")
        path = str(definition.resolve()).replace("'", "''")
        prelude = f"$name = '{name}'; $task = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue;\n"
        stop = """if ($task) {
    Stop-ScheduledTask -TaskName $name
    $deadline = (Get-Date).AddSeconds(8)
    while ((Get-ScheduledTask -TaskName $name).State -eq 'Running') {
        if ((Get-Date) -gt $deadline) { throw 'Task did not stop' }
        Start-Sleep -Milliseconds 100
    }
}
"""
        if definition.is_file():
            spec = json.loads(definition.read_text(encoding="utf-8"))
            arguments = spec.get("arguments", [])
            if "--port" in arguments:
                port = int(arguments[arguments.index("--port") + 1])
                stop += f"""
$deadline = (Get-Date).AddSeconds(15)
while (Get-NetTCPConnection -LocalAddress '127.0.0.1' -LocalPort {port} -State Listen -ErrorAction SilentlyContinue) {{
    if ((Get-Date) -gt $deadline) {{ throw 'The router listener did not stop' }}
    Start-Sleep -Milliseconds 100
}}
"""
        if action == "bootstrap":
            spec = json.loads(definition.read_text(encoding="utf-8"))
            arguments = subprocess.list2cmdline([spec["runner"], "--spec", str(definition.resolve())]).replace("'", "''")
            script = prelude + f"""
if ($task) {{ throw 'Task already exists; refusing to replace it' }}
$spec = Get-Content -LiteralPath '{path}' -Raw -Encoding UTF8 | ConvertFrom-Json
$user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$action = New-ScheduledTaskAction -Execute $spec.python -Argument '{arguments}'
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
if ($spec.interval) {{
    $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddSeconds(5) -RepetitionInterval (New-TimeSpan -Seconds $spec.interval)
}} else {{
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
}}
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Principal $principal -Settings $settings | Out-Null
try {{ Start-ScheduledTask -TaskName $name }} catch {{
    Unregister-ScheduledTask -TaskName $name -Confirm:$false
    throw
}}
"""
        elif action == "kickstart":
            script = prelude + "if (-not $task) { throw 'Task is not installed' };\n" + stop + "Start-ScheduledTask -TaskName $name"
        elif action == "bootout":
            script = prelude + stop + "if ($task) { Unregister-ScheduledTask -TaskName $name -Confirm:$false }"
        else:
            raise ValueError("Unknown service action")
        _powershell(script)
        return
    if sys.platform != "darwin":
        raise RuntimeError("Background services currently support macOS and Windows only")
    domain = f"gui/{os.getuid()}"
    command = (["launchctl", "bootstrap", domain, str(definition)] if action == "bootstrap" else
               ["launchctl", "kickstart", "-k", f"{domain}/{label}"] if action == "kickstart" else
               ["launchctl", "bootout", f"{domain}/{label}"])
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
    if result.returncode and not (action == "bootout" and "could not find" in result.stderr.lower()):
        raise RuntimeError(f"launchctl {action} failed: {result.stderr.strip()[:300]}")


def try_lock(stream) -> bool:
    if is_windows():
        import msvcrt
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write("0")
            stream.flush()
        stream.seek(0)
        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            if error.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                return False
            raise
    else:
        import fcntl
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
    return True
