"""Launch a named Codex profile with the user's Mindlogic key in the child only."""

import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


def main() -> int:
    profile = "mindlogic-menu"
    arguments = sys.argv[1:]
    if arguments[:1] == ["--profile"]:
        if len(arguments) < 2:
            raise SystemExit("Missing profile name")
        profile, arguments = arguments[1], arguments[2:]
    home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    values = []
    for line in (home / ".env").read_text(encoding="utf-8-sig").splitlines():
        match = re.match(r"^\s*(?:export\s+)?FACTCHAT_API_KEY\s*=\s*(.*?)\s*$", line)
        if match:
            value = match.group(1)
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            values.append(value)
    if len(values) != 1 or not values[0]:
        raise SystemExit("Expected one nonempty FACTCHAT_API_KEY entry in .codex/.env")
    executable = shutil.which("codex.exe") or shutil.which("codex")
    if not executable:
        raise SystemExit("Codex CLI is not on PATH")
    return subprocess.call([executable, "--profile", profile, *arguments],
                           env=dict(os.environ, FACTCHAT_API_KEY=values[0]))


if __name__ == "__main__":
    sys.exit(main())
