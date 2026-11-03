"""Audit the runtime snapshot taken BEFORE installing audit tooling."""
from pathlib import Path
import subprocess
import sys
import tempfile
import re


def normalized_requirements(snapshot):
    rows = []
    for line in snapshot.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([^\s;]+)", line)
        if not match:
            raise ValueError("The runtime snapshot must contain exact package pins.")
        name, version = match.groups()
        if name.lower() == "torch":
            version = version.split("+", 1)[0]
        rows.append(f"{name}=={version}")
    if not rows:
        raise ValueError("The runtime dependency snapshot is empty.")
    return "\n".join(sorted(rows))+"\n"


def main():
    snapshot = Path(__file__).resolve().parents[1]/"deploy/installed-requirements.txt"
    with tempfile.TemporaryDirectory() as folder:
        requirements = Path(folder)/"installed.txt"
        requirements.write_text(normalized_requirements(snapshot.read_text(encoding="utf-8")), encoding="utf-8")
        return subprocess.call([sys.executable, "-m", "pip_audit", "--disable-pip", "--no-deps", "--strict",
                                "--progress-spinner", "off", "-r", str(requirements)])


if __name__ == "__main__":
    raise SystemExit(main())
