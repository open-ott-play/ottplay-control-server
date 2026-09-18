#!/usr/bin/env python3
"""Check deployable chart behavior and release coverage before publication."""
import json
from pathlib import Path
import subprocess

root = Path(__file__).resolve().parents[1]
chart = str(root / "charts/ottplay-control-server")
base = ["helm", "template", "control", chart]
# A release must name immutable version identity rather than a moving default.
for extra in ([], ["--set", "image.tag=latest"], ["--set", "image.tag=v0.1.0", "--set", "image.digest=invalid"]):
    result = subprocess.run(base + extra, capture_output=True, text=True)
    if result.returncode == 0:
        raise SystemExit("Chart accepted an absent or invalid release identity")
rendered = subprocess.check_output(base + ["--set", "image.tag=v0.1.0-beta.1"], text=True)
for required in ("replicas: 1", "type: Recreate", "automountServiceAccountToken: false", "readOnlyRootFilesystem: true", "runAsUser: 65532", "defaultMode: 0440", "path: /readyz", "path: /healthz"):
    if required not in rendered:
        raise SystemExit("Deployment safety contract missing: " + required)
if "kind: Ingress" in rendered or "kind: Secret" in rendered:
    raise SystemExit("The default chart must neither expose Ingress nor embed credentials")
k3s = subprocess.check_output(base + ["-f", str(root / "deploy/k3s-values.yaml"), "--set", "image.tag=v0.1.0-beta.1", "--set", "ingress.enabled=true", "--set", "ingress.host=control.example.test"], text=True)
if 'ingressClassName: "traefik"' not in k3s or 'host: "control.example.test"' not in k3s:
    raise SystemExit("k3s Ingress configuration did not reach the manifest")
policy = json.loads((root / ".release-policy.json").read_text())
assert policy["repository"] == "open-ott-play/ottplay-control-server"
assert policy["container_assets"] == {"ottplay-control-server-container.oci.tar": "ghcr.io/open-ott-play/ottplay-control-server"}
print("Deployment configuration contracts passed")
