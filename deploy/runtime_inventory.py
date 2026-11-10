"""Read-only image evidence. No app imports, provider calls or secret values."""
from contextlib import closing
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import platform
import re
import shutil
import sqlite3
import stat
import subprocess


def file_info(path, *, checksum=False):
    path = Path(path)
    result = {"path": str(path), "present": path.exists()}
    if result["present"]:
        try:
            details = path.stat()
            result.update(mode=oct(stat.S_IMODE(details.st_mode)), uid=details.st_uid,
                          setuid=bool(details.st_mode & stat.S_ISUID),
                          setgid=bool(details.st_mode & stat.S_ISGID))
            if checksum and path.is_file():
                result["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as exc:
            result["error_type"] = type(exc).__name__
    return result


def process_controls():
    result = {"uid": os.geteuid() if hasattr(os, "geteuid") else None,
              "gid": os.getegid() if hasattr(os, "getegid") else None}
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            name, _, value = line.partition(":")
            if name in {"CapEff", "CapPrm", "CapBnd", "NoNewPrivs"}:
                result[name] = value.strip()
    except OSError:
        result["linux_status_available"] = False
    return result


def python_packages():
    result = {}
    for name in ("pip", "setuptools", "streamlit", "torch", "psycopg"):
        try:
            package = metadata.distribution(name)
            result[name] = {"version": package.version, "root": str(package.locate_file(""))}
            if name == "pip":
                vendor = Path(package.locate_file("pip/_vendor"))
                manifest = vendor / "vendor.txt"
                result[name]["vendor_manifest"] = file_info(manifest, checksum=True)
                if manifest.is_file():
                    result[name]["vendored_requirements"] = [
                        line.strip() for line in manifest.read_text().splitlines()
                        if line.strip() and not line.lstrip().startswith("#")]
                result[name]["vendored_components"] = [file_info(vendor / relative, checksum=True) for relative in (
                    "pkg_resources/__init__.py", "setuptools/package_index.py",
                    "setuptools/command/sdist.py", "setuptools/command/easy_install.py")]
        except metadata.PackageNotFoundError:
            result[name] = {"installed": False}
        except OSError as exc:
            result[name] = {"error_type": type(exc).__name__}
    return result


def os_packages():
    binary = shutil.which("dpkg-query")
    if not binary:
        return {"available": False}
    try:
        query = subprocess.run([binary, "-W", "-f=${binary:Package}\t${Version}\t${source:Package}\t${source:Version}\n"],
                               capture_output=True, text=True, timeout=10, check=False)
        if query.returncode:
            return {"available": False, "returncode": query.returncode}
        packages = []
        for line in query.stdout.splitlines():
            fields = line.split("\t")
            if len(fields) == 4:
                packages.append(dict(zip(("name", "version", "source", "source_version"), fields)))
        return {"available": True, "packages": packages}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"available": False, "error_type": type(exc).__name__}


def perl_archive_tar():
    perl = shutil.which("perl")
    if not perl:
        return {"interpreter_present": False}
    try:
        probe = subprocess.run([perl, "-MArchive::Tar", "-e", "print $Archive::Tar::VERSION"],
                               capture_output=True, text=True, timeout=5, check=False,
                               env={"PATH": os.defpath, "LC_ALL": "C.UTF-8"})
        result = {"interpreter_present": True, "module_load_succeeded": probe.returncode == 0,
                  "module_missing": "Can't locate Archive/Tar.pm" in probe.stderr,
                  "returncode": probe.returncode}
        if probe.returncode == 0 and re.fullmatch(r"[0-9._]+", probe.stdout.strip()):
            result["version"] = probe.stdout.strip()
        return result
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"interpreter_present": True, "error_type": type(exc).__name__}


def collect():
    with closing(sqlite3.connect(":memory:")) as connection:
        options = [row[0] for row in connection.execute("PRAGMA compile_options")]
    try:
        release = platform.freedesktop_os_release()
        release = {name: release[name] for name in ("ID", "VERSION_ID", "PRETTY_NAME") if name in release}
    except OSError:
        release = {}
    programs = {}
    for name in ("mount", "umount", "nsenter", "infocmp", "getfacl", "setfacl", "su", "perl"):
        path = shutil.which(name)
        programs[name] = file_info(path) if path else {"present": False}
    programs["systemd-homed"] = [file_info(path) for path in (
        "/usr/lib/systemd/systemd-homed", "/lib/systemd/systemd-homed")]
    try:
        entries = [line for line in Path("/etc/fstab").read_text().splitlines()
                   if line.strip() and not line.lstrip().startswith("#")]
        fstab = {"entry_count": len(entries), "x_mount_options_present": any("X-mount." in line for line in entries)}
    except OSError:
        fstab = {"available": False}
    return {
        "schema": 1, "scope": "isolated CI cloud image; not the eventual hosted runtime",
        "applicability_assessed": False,
        "os_release": release, "python": platform.python_version(),
        "process_controls": process_controls(), "python_packages": python_packages(),
        "os_packages": os_packages(), "programs": programs, "fstab": fstab,
        "perl_archive_tar": perl_archive_tar(),
        "sqlite": {"version": sqlite3.sqlite_version, "compile_options": options},
        "cloud_only_marker": Path(__file__).with_name("cloud-only").is_file(),
    }


if __name__ == "__main__":
    print(json.dumps(collect(), indent=2))
