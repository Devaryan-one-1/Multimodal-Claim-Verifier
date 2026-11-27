"""Inspect package ownership and simulate Debian removals inside the release image."""
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys

APP = Path("/app")
sys.path[:0] = [str(APP), str(APP / "app")]
from deploy import container_smoke
import pilot_worker
import pilot_task


def package_owners(paths):
    # Debian's lists can use /lib instead of /usr/lib on merged-/usr.
    owners = {}
    basenames = {Path(p).name for p in paths}
    for manifest in Path("/var/lib/dpkg/info").glob("*.list"):
        for item in manifest.read_text(errors="replace").splitlines():
            path = os.path.realpath(item) if Path(item).name in basenames else ""
            if path in paths:
                owners[path] = manifest.name.split(".list")[0].split(":")[0]
    return owners


if __name__ == "__main__":
    sys.argv[1:] = ["cloud"]
    container_smoke.main()
    with sqlite3.connect(":memory:") as conn:
        assert conn.execute("SELECT 1").fetchone() == (1,)
    # Include dependencies of delayed native imports exercised by the smoke.
    paths = set()
    for line in Path("/proc/self/maps").read_text().splitlines():
        fields = line.split(None, 5)
        if len(fields) == 6 and fields[5].startswith("/") and "x" in fields[1]:
            paths.add(os.path.realpath(fields[5]))
    owners = package_owners(paths)
    status = subprocess.check_output(["dpkg-query", "-W", "-f",
        r"${Package}\t${Essential}\t${Protected}\n"], text=True)
    protected = {line.split("\t")[0] for line in status.splitlines() if "yes" in line.split("\t")[1:]}
    scan = json.loads(Path("/audit/baseline-release.json").read_text())
    affected = {v["PkgName"] for t in scan["Results"] if t["Class"] == "os-pkgs"
                for v in t.get("Vulnerabilities", [])}
    # Limit changes to administration commands outside the app's startup and
    # child-process paths. Other flagged packages still get a simulated removal.
    tools = {"mount", "login", "passwd", "openssl", "adduser"}
    plans = {}
    loaded = set(owners.values())
    for pkg in sorted(affected):
        sim = subprocess.run(["apt-get", "-s", "purge", pkg], text=True,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             env=dict(os.environ, LC_ALL="C"))
        removed = set(re.findall(r"^(?:Remv|Purg) ([^ :]+)", sim.stdout, re.M))
        changes = bool(re.search(r"^(?:Inst|Conf) ", sim.stdout, re.M))
        safe = (sim.returncode == 0 and pkg in removed and removed <= tools
                and removed.isdisjoint(protected | loaded) and not changes)
        plans[pkg] = {"removed": sorted(removed), "exit": sim.returncode,
                      "loaded": sorted(removed & loaded), "protected": sorted(removed & protected),
                      "other_changes": changes, "eligible": safe, "simulation": sim.stdout}
    selected = next((p for p in ("mount", "login", "passwd", "openssl") if plans.get(p, {}).get("eligible")), None)
    report = {"loaded_files": {p: owners.get(p) for p in sorted(paths)},
              "protected": sorted(protected), "plans": plans, "selected": selected}
    print("PACKAGE_REPORT=" + json.dumps(report))
