#!/usr/bin/env python3
"""Cross-compile one portable server release and package its exact binary."""
import argparse
import datetime
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
TARGETS = {
    "linux-amd64": ("linux", "amd64", ""),
    "linux-arm64": ("linux", "arm64", ""),
    "linux-armv7": ("linux", "arm", "7"),
    "darwin-amd64": ("darwin", "amd64", ""),
    "darwin-arm64": ("darwin", "arm64", ""),
    "windows-amd64": ("windows", "amd64", ""),
    "windows-arm64": ("windows", "arm64", ""),
}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", choices=TARGETS)
    args = parser.parse_args()
    version = (ROOT / "VERSION").read_text().strip()
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[a-z0-9.-]+)?", version):
        raise SystemExit("Invalid VERSION")
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    timestamp = subprocess.check_output(["git", "show", "-s", "--format=%ct", "HEAD"], cwd=ROOT, text=True).strip()
    date = datetime.datetime.fromtimestamp(int(timestamp), datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    goos, goarch, goarm = TARGETS[args.target]
    suffix = ".exe" if goos == "windows" else ""
    basename = "ottplay-control-server-" + args.target
    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    binary = dist / (basename + suffix)
    env = dict(os.environ, CGO_ENABLED="0", GOOS=goos, GOARCH=goarch, GOARM=goarm, GOAMD64="v1")
    subprocess.run(["go", "build", "-trimpath", "-buildvcs=false", "-ldflags", f"-s -w -X main.version={version} -X main.commit={commit} -X main.date={date}", "-o", str(binary), "./cmd/ottplay-control-server"], cwd=ROOT, env=env, check=True)
    subprocess.run([os.sys.executable, "scripts/write_binary_build_metadata.py", str(binary.relative_to(ROOT)), "dist/" + basename + ".version.json"], cwd=ROOT, check=True)
    with tempfile.TemporaryDirectory() as temp:
        package = Path(temp) / basename
        package.mkdir()
        shutil.copy2(binary, package / ("ottplay-control-server" + suffix))
        for name in ("LICENSE", "NOTICE", "README.md", "config.example.json"):
            shutil.copy2(ROOT / name, package / name)
        shutil.copytree(ROOT / "LICENSES", package / "LICENSES")
        if goos == "windows":
            with zipfile.ZipFile(dist / (basename + ".zip"), "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for path in sorted(package.rglob("*")):
                    if path.is_file():
                        archive.write(path, path.relative_to(package.parent))
        else:
            with tarfile.open(dist / (basename + ".tar.gz"), "w:gz") as archive:
                archive.add(package, arcname=basename)
    print("Built " + basename + " " + version)

if __name__ == "__main__":
    main()
