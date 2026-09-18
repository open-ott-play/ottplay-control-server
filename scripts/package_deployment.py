#!/usr/bin/env python3
"""Package deployment defaults using the full frozen tag, including RC identity."""

import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile

from version_plan import projected_value, validate_plan

ROOT = Path(__file__).resolve().parents[1]


def package(root: Path, plan: dict, output: Path) -> None:
    policy = json.loads((root / ".release-policy.json").read_text())
    validate_plan(plan, policy)
    tag = plan["tag"]
    package_version = projected_value(plan, {"value": "package"})
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="control-deployment-") as temporary:
        stage = Path(temporary)
        chart = stage / "ottplay-control-server"
        shutil.copytree(root / "charts/ottplay-control-server", chart)
        values = chart / "values.yaml"
        original = values.read_text()
        marker = '  tag: ""'
        if original.splitlines().count(marker) != 1:
            raise ValueError("Expected exactly one empty source image tag")
        values.write_text(original.replace(marker, "  tag: " + json.dumps(tag)))
        subprocess.run(["helm", "package", str(chart), "--version", tag[1:], "--app-version", package_version, "--destination", str(output)], check=True)
        shutil.copytree(root / "deploy", stage / "deploy")
        rendered = subprocess.check_output(["helm", "template", "ottplay-control-server", str(chart)])
        (stage / "deploy/kubernetes.yaml").write_bytes(rendered)
        names = ["config.example.json", "README.md", "LICENSE", "NOTICE", "LICENSES", "docs"]
        for name in names:
            source = root / name
            if source.is_dir():
                shutil.copytree(source, stage / name)
            else:
                shutil.copy2(source, stage / name)
        with tarfile.open(output / "ottplay-control-server-deployment.tar.gz", "w:gz") as archive:
            for name in ["deploy", *names]:
                archive.add(stage / name, arcname=name)


def main() -> None:
    policy = json.loads((ROOT / ".release-policy.json").read_text())
    plan = json.loads((ROOT / ".release-plan.json").read_text())
    source = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    validate_plan(plan, policy, source)
    package(ROOT, plan, ROOT / "dist")


if __name__ == "__main__":
    main()
