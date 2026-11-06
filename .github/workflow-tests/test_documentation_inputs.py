"""Validate mandatory deployment inputs without building charts or containers."""

import ast
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def deployment_inputs(root):
    """Read the packager's literal inventory without importing or executing it."""
    tree = ast.parse((root / "scripts/package_deployment.py").read_text())
    functions = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "package"
    ]
    if len(functions) != 1:
        raise ValueError("Expected one deployment package function")
    inventories = [
        node.value
        for node in ast.walk(functions[0])
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "names"
            for target in node.targets
        )
    ]
    if len(inventories) != 1:
        raise ValueError("Expected one literal deployment input inventory")
    names = ast.literal_eval(inventories[0])
    if (
        not isinstance(names, list)
        or not names
        or any(not isinstance(name, str) for name in names)
    ):
        raise ValueError("Expected nonempty deployment path list")
    if len(names) != len(set(names)):
        raise ValueError("Duplicate deployment path")
    for name in names:
        if (
            name.startswith("/")
            or "\\" in name
            or any(part in {"", ".", ".."} for part in name.split("/"))
        ):
            raise ValueError("Unsafe deployment path")
        path = root / name
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise ValueError("Missing or non-regular deployment input: " + name)
    return names


class DocumentationInputsTests(unittest.TestCase):
    def test_committed_deployment_inputs_are_present(self):
        deployment_inputs(ROOT)

    def test_missing_document_rejects_lightweight_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "scripts").mkdir()
            (root / "scripts/package_deployment.py").write_text(
                "def package():\n    names = ['README.md']\n"
            )
            with self.assertRaisesRegex(ValueError, "Missing or non-regular"):
                deployment_inputs(root)
            (root / "README.md").write_text("Documentation")
            self.assertEqual(deployment_inputs(root), ["README.md"])

    def test_every_actual_package_input_rejects_deletion_or_rename(self):
        names = deployment_inputs(ROOT)
        source = (ROOT / "scripts/package_deployment.py").read_text()
        for missing in names:
            with self.subTest(path=missing), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "scripts").mkdir()
                (root / "scripts/package_deployment.py").write_text(source)
                for name in names:
                    if (ROOT / name).is_dir():
                        (root / name).mkdir()
                    else:
                        (root / name).write_text("Documentation")
                self.assertEqual(deployment_inputs(root), names)
                (root / missing).rename(root / (missing + ".renamed"))
                with self.assertRaisesRegex(ValueError, "Missing or non-regular"):
                    deployment_inputs(root)

    def test_ordinary_document_edits_preserve_lightweight_validation(self):
        names = deployment_inputs(ROOT)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "scripts").mkdir()
            (root / "scripts/package_deployment.py").write_text(
                (ROOT / "scripts/package_deployment.py").read_text()
            )
            for name in names:
                if (ROOT / name).is_dir():
                    (root / name).mkdir()
                else:
                    (root / name).write_text("Documentation")
            for name in ("README.md", "README.ru.md"):
                (root / name).write_text("Updated ordinary prose\n")
            self.assertEqual(deployment_inputs(root), names)

    def test_dynamic_inventory_requires_explicit_review(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "scripts").mkdir()
            (root / "scripts/package_deployment.py").write_text(
                "def package():\n    names = discover_inputs()\n"
            )
            with self.assertRaises(ValueError):
                deployment_inputs(root)


if __name__ == "__main__":
    unittest.main()
