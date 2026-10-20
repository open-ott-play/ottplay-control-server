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
from package_deployment import package, stage_android_tools
from version_plan import create_plan

EXPECTED_USB_TOOLS = {
    "android_usb_manager.py", "android_usb_supervisor.py",
    "android_usb_transaction.py", "android_usb_manager_remote.py",
    "android_usb_legacy_redirect.py", "android_usb_remote.py",
}


class AndroidToolStagingTests(unittest.TestCase):
    def test_only_runtime_files_are_staged_even_in_a_private_working_tree(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "tools"
            shutil.copytree(ROOT / "tools", source)
            for name in (
                "config.json", "adbkey", "adbkey.pub", "private.adb_key",
                "private.adb_key.pub", "extra_helper.py",
                "adb-keys/vendor/private.adb_key", "__pycache__/cached.pyc",
            ):
                path = source / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("must not be published")
            destination = root / "stage"
            stage_android_tools(root, destination)
            self.assertEqual({path.name for path in destination.iterdir()}, EXPECTED_USB_TOOLS)
            for name in EXPECTED_USB_TOOLS:
                self.assertEqual((destination / name).read_bytes(), (ROOT / "tools" / name).read_bytes())

    def test_missing_dependency_refuses_incomplete_runtime(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shutil.copytree(ROOT / "tools", root / "tools")
            (root / "tools/android_usb_supervisor.py").unlink()
            with self.assertRaisesRegex(ValueError, "android_usb_supervisor.py"):
                stage_android_tools(root, root / "stage")
            self.assertFalse((root / "stage").exists())

    @unittest.skipIf(sys.platform == "win32", "Creating symlinks needs extra Windows privileges")
    def test_symlink_cannot_substitute_private_data_for_a_tool(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shutil.copytree(ROOT / "tools", root / "tools")
            tool = root / "tools/android_usb_supervisor.py"
            tool.unlink()
            private = root / "private"
            private.write_text("must not be published")
            tool.symlink_to(private)
            with self.assertRaisesRegex(ValueError, "android_usb_supervisor.py"):
                stage_android_tools(root, root / "stage")


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
                self.assertIsNotNone(archive.getmember("README.ru.md"))
                self.assertIsNotNone(archive.getmember("deploy/compose.journal.yml"))
            with tarfile.open(chart) as archive:
                metadata = archive.extractfile("ottplay-control-server/Chart.yaml").read().decode()
                self.assertIn("version: 0.1.0-rc.7", metadata)
                self.assertIn("appVersion: 0.1.0", metadata)
        self.assertEqual(original, (ROOT / "charts/ottplay-control-server/values.yaml").read_bytes())

    def test_archive_contains_complete_isolated_android_runtime(self):
        policy = json.loads((ROOT / ".release-policy.json").read_text())
        plan = create_plan("0.1.0", "beta", 7, "b" * 40, policy)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package(ROOT, plan, root / "dist")
            extracted = root / "extracted"
            tools = extracted / "tools"
            tools.mkdir(parents=True)
            with tarfile.open(root / "dist/ottplay-control-server-deployment.tar.gz") as archive:
                files = {member.name for member in archive.getmembers() if member.isfile()}
                self.assertEqual(
                    {name for name in files if name.startswith("tools/")},
                    {"tools/" + name for name in EXPECTED_USB_TOOLS},
                )
                for name in files:
                    parts = Path(name).parts
                    self.assertNotIn("__pycache__", parts)
                    self.assertNotIn("adb-keys", parts)
                    self.assertNotIn("config.json", parts)
                    self.assertNotIn("adbkey", parts)
                    self.assertNotIn("adbkey.pub", parts)
                    self.assertFalse(name.endswith((".pyc", ".pyo", ".adb_key", ".adb_key.pub")))
                for name in EXPECTED_USB_TOOLS:
                    data = archive.extractfile("tools/" + name).read()
                    self.assertEqual(data, (ROOT / "tools" / name).read_bytes())
                    (tools / name).write_bytes(data)
                config = json.loads(archive.extractfile("deploy/android-usb-manager/config.example.json").read())
                for name in (
                    "docs/android-usb-manager.md", "docs/android-usb-manager.ru.md",
                    "docs/assets/android-usb-manager.svg", "docs/assets/android-usb-manager.ru.svg",
                    "deploy/android-usb-manager/Dockerfile",
                    "deploy/android-usb-manager/requirements.txt",
                    "deploy/android-usb-manager/ott-android-manager.service",
                    "deploy/android-usb-manager/S99ott-android-manager.sh",
                    "deploy/android-usb-recovery/ott-k8a-recovery.service",
                ):
                    self.assertIn(name, files)
            for name in EXPECTED_USB_TOOLS:
                compile((tools / name).read_bytes(), name, "exec")
                # The host runtime needs POSIX fcntl; the remote wrappers are portable.
                if sys.platform == "win32" and name in {
                    "android_usb_manager.py", "android_usb_supervisor.py",
                }:
                    continue
                result = subprocess.run(
                    [sys.executable, "-B", "-E", "-s", str(tools / name), "--help"],
                    cwd=extracted, capture_output=True, text=True,
                )
                self.assertEqual(result.returncode, 0, f"{name}: {result.stderr}")
            if sys.platform != "win32":
                # Exercise transaction's lazy manager import and the manager's sibling
                # supervisor dependency without contacting Docker, SSH or a device.
                config["state_dir"] = str(root / "state")
                config["adb_key_dir"] = str(root / "unused-keys")
                config_path = root / "test-config.json"
                config_path.write_text(json.dumps(config))
                result = subprocess.run(
                    [sys.executable, "-B", "-E", "-s", str(tools / "android_usb_transaction.py"),
                     "--config", str(config_path)],
                    input="{}", cwd=extracted, capture_output=True, text=True,
                )
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stderr.strip(), "Unknown USB transaction")
            self.assertFalse(list(extracted.rglob("__pycache__")))

    def test_optional_journal_volume_preserves_single_writer_and_root_isolation(self):
        base = ["helm", "template", "control", str(ROOT / "charts/ottplay-control-server"), "--set", "image.tag=v0.1.0"]
        plain = subprocess.check_output(base, text=True)
        self.assertNotIn("name: journal", plain)
        rendered = subprocess.check_output(base + ["--set", "journalExistingClaim=control-state"], text=True)
        for expected in ("replicas: 1", "type: Recreate", "readOnlyRootFilesystem: true", "mountPath: /var/lib/ottplay-control", 'claimName: "control-state"', "fsGroupChangePolicy: OnRootMismatch"):
            self.assertIn(expected, rendered)


if __name__ == "__main__":
    unittest.main()
