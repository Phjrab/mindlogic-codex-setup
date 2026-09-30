"""Scheduled-task entry point: run the service in this process with UTF-8 logs."""

import argparse
import json
import os
from pathlib import Path
import runpy
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", type=Path, required=True)
    spec = json.loads(parser.parse_args().spec.read_text(encoding="utf-8"))
    os.environ["CODEX_HOME"] = spec["home"]
    os.environ["PYTHONUTF8"] = "1"
    program = Path(spec["program"])
    sys.path.insert(0, str(program.parent))
    sys.argv = [str(program), *spec["arguments"]]
    original_streams = sys.stdout, sys.stderr
    with open(spec["stdout"], "a", encoding="utf-8", buffering=1) as output, \
            open(spec["stderr"], "a", encoding="utf-8", buffering=1) as errors:
        sys.stdout, sys.stderr = output, errors
        try:
            runpy.run_path(str(program), run_name="__main__")
        except BaseException:
            import traceback
            traceback.print_exc()
            raise
        finally:
            sys.stdout, sys.stderr = original_streams


if __name__ == "__main__":
    main()
