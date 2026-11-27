import json
import sys
from collections import Counter
from pathlib import Path

reports = Path(sys.argv[1])


def read_scan(name):
    report = json.loads((reports / (name + ".json")).read_text())
    findings = {}
    os_packages = set()
    for target in report["Results"]:
        if target["Class"] == "os-pkgs":
            os_packages.update(p["Name"] for p in target["Packages"])
        for v in target.get("Vulnerabilities") or []:
            key = (target["Type"], v["PkgName"], v["VulnerabilityID"])
            findings[key] = v
    if not os_packages:
        raise ValueError(f"No OS package inventory in {name}")
    return findings, os_packages


summary = {}
for variant in ("baseline", "candidate"):
    base, packages = read_scan(variant + "-base")
    release, _ = read_scan(variant + "-release")
    added = release.keys() - base.keys()
    summary[variant] = {
        "base_findings": len(base),
        "release_findings": len(release),
        "common_package_advisories": len(base.keys() & release.keys()),
        "removed_since_base": len(base.keys() - release.keys()),
        "release_only": sorted(added),
        "flagged_os_packages_absent_from_base": sorted({
            pkg for kind, pkg, _ in added if kind == "debian" and pkg not in packages
        }),
        "release_severities": Counter(v["Severity"] for v in release.values()),
        "release_findings_with_fixes": sum(bool(v.get("FixedVersion")) for v in release.values()),
    }
output = json.dumps(summary, indent=2)
(reports / "comparison.json").write_text(output + "\n")
print(output)
