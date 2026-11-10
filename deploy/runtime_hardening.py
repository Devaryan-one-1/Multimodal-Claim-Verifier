"""Build/native guards for the customer runtime; no app imports or network calls."""
from importlib import metadata
from importlib.util import find_spec
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys

SNAPSHOT = Path(__file__).with_name("installed-requirements.txt")
SYSTEM_ROOTS = (Path("/usr"),)


def installed_requirements():
    packages = {}
    for package in metadata.distributions():
        name, version = package.metadata.get("Name", ""), package.version
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", name) or not re.fullmatch(r"[^\s;]+", version):
            raise RuntimeError("A runtime distribution has invalid name/version metadata.")
        name = re.sub(r"[-_.]+", "-", name).lower()
        if name in packages and packages[name] != version:
            raise RuntimeError("Conflicting versions of a runtime distribution were found.")
        packages[name] = version
    if not packages:
        raise RuntimeError("The runtime distribution inventory is empty.")
    return packages


def requirements_text(packages):
    return "".join(f"{name}=={version}\n" for name, version in sorted(packages.items()))


def write_snapshot(path=SNAPSHOT):
    # Uses metadata after installer removal, without importing or restoring pip.
    Path(path).write_text(requirements_text(installed_requirements()), encoding="utf-8")


def privileged_files(roots=SYSTEM_ROOTS):
    found = []
    def walk_error(error):
        raise error
    for root in roots:
        root = Path(root)
        if not root.is_dir():
            raise RuntimeError("A system tree required for hardening checks is unavailable.")
        for folder, _, files in os.walk(root, followlinks=False, onerror=walk_error):
            for name in files:
                path = Path(folder)/name
                mode = path.lstat().st_mode
                if stat.S_ISREG(mode) and mode & (stat.S_ISUID | stat.S_ISGID):
                    found.append(str(path))
    return found


def check_runtime(path=SNAPSHOT, roots=SYSTEM_ROOTS):
    packages = installed_requirements()
    if "pip" in packages or find_spec("pip") is not None:
        raise RuntimeError("The customer runtime still contains the pip installer.")
    launchers = ("pip", "pip3", f"pip{sys.version_info.major}.{sys.version_info.minor}")
    if any(shutil.which(name) for name in launchers):
        raise RuntimeError("A pip executable remains on the customer runtime PATH.")
    if Path(path).read_text(encoding="utf-8") != requirements_text(packages):
        raise RuntimeError("Installed runtime packages do not match the auditable snapshot.")
    found = privileged_files(roots)
    if found:
        raise RuntimeError("Setuid/setgid files remain in the customer system tree: " + ", ".join(found[:10]))
    return {"pip_absent": True, "privilege_bits_cleared": True,
            "checked_roots": [str(root) for root in roots], "packages": len(packages)}


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) == 2 else ""
    if command == "snapshot":
        write_snapshot()
    elif command == "check":
        print(json.dumps(check_runtime()))
    else:
        raise SystemExit("Choose snapshot or check.")
