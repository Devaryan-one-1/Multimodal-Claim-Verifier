"""Generate the experimental final stage and compare the two finished images."""
import json
from pathlib import Path
import subprocess
import sys
from collections import Counter

reports = Path("reports")


def inspect(tag):
    return json.loads(subprocess.check_output(["docker", "image", "inspect", tag]))[0]


def findings(tag):
    scan = json.loads((reports / f"{tag}-release.json").read_text())
    packages = {p["Name"] for t in scan["Results"] if t["Class"] == "os-pkgs" for p in t["Packages"]}
    rows = {(t["Type"], v["PkgName"], v["VulnerabilityID"]): v for t in scan["Results"]
            for v in t.get("Vulnerabilities", [])}
    return packages, rows


plan = json.loads((reports / "package-probe.json").read_text())
package = plan["selected"]
if not package:
    print("No eligible administrative package. See package-probe.json.")
    sys.exit(0)

baseline = inspect("compare-baseline:release")
cfg = baseline["Config"]
if sys.argv[1] == "prepare":
    assert not any(cfg.get(k) for k in ("Volumes", "Healthcheck", "OnBuild", "Shell",
                                       "StopSignal", "Labels", "ExposedPorts"))
    assert package in {"mount", "login", "passwd", "openssl"}
    dockerfile = [
        "FROM compare-baseline:release AS pruned", "USER root",
        f"RUN apt-get -y --no-auto-remove purge {package} && dpkg --audit && apt-get check",
        "FROM scratch", "COPY --from=pruned / /",
    ]
    # Copy the effective runtime config from the baseline. Copying the final
    # filesystem into a fresh layer actually drops purged bytes from the image.
    for entry in cfg["Env"]:
        key, value = entry.split("=", 1)
        dockerfile.append(f"ENV {key}={json.dumps(value)}")
    dockerfile += ["WORKDIR " + cfg["WorkingDir"], "USER " + cfg["User"],
                   "ENTRYPOINT " + json.dumps(cfg["Entrypoint"]), "CMD " + json.dumps(cfg["Cmd"])]
    (reports / "Dockerfile.pruned").write_text("\n".join(dockerfile) + "\n")
else:
    candidate = inspect("compare-candidate:release")
    for key in set(cfg) | set(candidate["Config"]):
        if key == "Env":
            assert sorted(cfg[key]) == sorted(candidate["Config"][key]), key
        else:
            assert candidate["Config"].get(key) == cfg.get(key), key
    before, old = findings("baseline")
    after, new = findings("candidate")
    removed = sorted(before - after)
    assert removed == plan["plans"][package]["removed"], (removed, plan["plans"][package]["removed"])
    assert not after - before
    result = {"selected": package, "removed_packages": removed,
              "baseline_bytes": baseline["Size"], "candidate_bytes": candidate["Size"],
              "baseline_image": baseline["Id"], "candidate_image": candidate["Id"],
              "baseline_findings": len(old), "candidate_findings": len(new),
              "before_severity": Counter(v["Severity"] for v in old.values()),
              "after_severity": Counter(v["Severity"] for v in new.values()),
              "removed_findings": sorted(old.keys() - new.keys()),
              "added_findings": sorted(new.keys() - old.keys())}
    output = json.dumps(result, indent=2)
    (reports / "pruning-result.json").write_text(output + "\n")
    print(output)
