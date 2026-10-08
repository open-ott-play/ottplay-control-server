#!/usr/bin/env python3
"""Build a static ARMv7 agent and versioned bootstrap bundle."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
version = (ROOT / 'VERSION').read_text().strip()
if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+(?:-[a-z0-9.-]+)?', version):
    raise SystemExit('Invalid version')
output = ROOT / 'dist'
output.mkdir(exist_ok=True)
with tempfile.TemporaryDirectory() as directory:
    stage = Path(directory)
    binary = stage / 'agent'
    subprocess.run(['go', 'build', '-trimpath', '-buildvcs=false', '-ldflags', '-s -w -X main.version=' + version,
                    '-o', str(binary), '.'], cwd=HERE,
                   env=dict(os.environ, CGO_ENABLED='0', GOOS='linux', GOARCH='arm', GOARM='7'), check=True)
    metadata = {'version': version, 'source': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                'sha256': hashlib.sha256(binary.read_bytes()).hexdigest(), 'target': 'linux-armv7',
                'device_acceptance': 'required-before-installation'}
    (stage / 'build.json').write_text(json.dumps(metadata, indent=2) + '\n')
    with tarfile.open(output / 'ottplay-control-server-android-agent.tar.gz', 'w:gz') as archive:
        for name in ['agent', 'build.json']:
            archive.add(stage / name, arcname=name)
        for name in ['install.py', 'README.md', 'config.example.json']:
            archive.add(HERE / name, arcname=name)
print('Built ARMv7 Android agent bundle ' + version)
