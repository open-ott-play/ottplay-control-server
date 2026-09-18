"""Exercise the actual packaged RC chart and manifest, without a live cluster."""

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from package_deployment import package
from version_plan import create_plan


@unittest.skipUnless(shutil.which("helm"), "Helm is required for the deployment package integration test")
class PackageTests(unittest.TestCase):
    def test_rc_chart_and_manifest_reference_the_published_rc_tag(self):
        policy = json.loads((ROOT / ".release-policy.json").read_text())
        plan = create_plan("0.1.0", "rc", 7, "a" * 40, policy)
        original = (ROOT / "charts/ottplay-control-server/values.yaml").read_bytes()
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            package(ROOT, plan, output)
            chart = output / "ottplay-control-server-0.1.0-rc.7.tgz"
            self.assertTrue(chart.is_file())
            rendered = subprocess.check_output(["helm", "template", "control", str(chart)], text=True)
            expected = "ghcr.io/open-ott-play/ottplay-control-server:v0.1.0-rc.7"
            self.assertIn(expected, rendered)
            with tarfile.open(output / "ottplay-control-server-deployment.tar.gz") as archive:
                self.assertIn(expected, archive.extractfile("deploy/kubernetes.yaml").read().decode())
                self.assertIsNotNone(archive.getmember("docs/deployment.md"))
            with tarfile.open(chart) as archive:
                metadata = archive.extractfile("ottplay-control-server/Chart.yaml").read().decode()
                self.assertIn("version: 0.1.0-rc.7", metadata)
                self.assertIn("appVersion: 0.1.0", metadata)
        self.assertEqual(original, (ROOT / "charts/ottplay-control-server/values.yaml").read_bytes())


if __name__ == "__main__":
    unittest.main()
