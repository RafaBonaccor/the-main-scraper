import os
import subprocess
import sys
from pathlib import Path


VENV_ENV_VAR = "BOTASAURUS_PROJECT_VENV_ACTIVE"


def ensure_project_venv(script_file: str) -> None:
    if os.environ.get(VENV_ENV_VAR) == "1":
        return

    script_path = Path(script_file).resolve()
    venv_candidates = (
        script_path.parent / ".venv" / "Scripts" / "python.exe",
        script_path.parent.parent / ".venv" / "Scripts" / "python.exe",
    )

    for candidate in venv_candidates:
        if not candidate.exists():
            continue

        if Path(sys.executable).resolve() == candidate.resolve():
            return

        env = os.environ.copy()
        env[VENV_ENV_VAR] = "1"
        completed = subprocess.run(
            [str(candidate), str(script_path), *sys.argv[1:]],
            cwd=Path.cwd(),
            env=env,
        )
        raise SystemExit(completed.returncode)
