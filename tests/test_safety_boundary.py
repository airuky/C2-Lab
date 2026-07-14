from __future__ import annotations

import ast
import unittest
from pathlib import Path

import c2lab


class SafetyBoundaryTests(unittest.TestCase):
    def test_runtime_has_no_process_dynamic_or_host_inspection_path(self) -> None:
        package = Path(c2lab.__file__).parent
        forbidden_import_roots = {
            "subprocess",
            "socket",
            "requests",
            "ftplib",
            "smtplib",
            "paramiko",
            "ctypes",
            "pty",
            "shlex",
            "platform",
            "psutil",
        }
        forbidden_calls = {
            "eval",
            "exec",
            "compile",
            "__import__",
            "system",
            "popen",
            "run",
            "call",
            "check_call",
            "check_output",
            "create_subprocess_exec",
            "create_subprocess_shell",
        }
        forbidden_file_calls = {
            "NamedTemporaryFile",
            "TemporaryDirectory",
            "remove",
            "scandir",
            "read_text",
            "write_text",
            "write_bytes",
            "unlink",
            "rename",
            "mkdir",
            "rmdir",
        }
        allowed_lab_file_calls = {
            "NamedTemporaryFile",
            "TemporaryDirectory",
            "remove",
            "scandir",
        }
        findings: list[str] = []

        for source_path in package.rglob("*.py"):
            tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        root = alias.name.split(".", 1)[0]
                        if root in forbidden_import_roots:
                            findings.append(f"{source_path.name}: import {alias.name}")
                        if alias.name == "urllib.request" and source_path.name != "node.py":
                            findings.append(f"{source_path.name}: network client import {alias.name}")
                elif isinstance(node, ast.ImportFrom) and node.module:
                    root = node.module.split(".", 1)[0]
                    if root in forbidden_import_roots:
                        findings.append(f"{source_path.name}: from {node.module}")
                    if node.module == "urllib.request" and source_path.name != "node.py":
                        findings.append(f"{source_path.name}: network client import {node.module}")
                elif isinstance(node, ast.Call):
                    name = None
                    if isinstance(node.func, ast.Name):
                        name = node.func.id
                    elif isinstance(node.func, ast.Attribute):
                        name = node.func.attr
                    if name in forbidden_calls or (name and name.startswith(("execv", "spawn"))):
                        findings.append(f"{source_path.name}:{node.lineno}: call {name}")
                    if name == "open":
                        allowed_http_open = (
                            source_path.name == "node.py"
                            and isinstance(node.func, ast.Attribute)
                            and isinstance(node.func.value, ast.Attribute)
                            and node.func.value.attr == "_opener"
                        )
                        if not allowed_http_open:
                            findings.append(f"{source_path.name}:{node.lineno}: file call {name}")
                    if name in forbidden_file_calls and not (
                        source_path.name == "lab_runtime.py" and name in allowed_lab_file_calls
                    ):
                        findings.append(f"{source_path.name}:{node.lineno}: file call {name}")
                    if name == "read_bytes" and source_path.name != "server.py":
                        findings.append(f"{source_path.name}:{node.lineno}: file call {name}")

        self.assertEqual(findings, [])

    def test_file_io_surface_is_confined_to_the_ephemeral_lab_module(self) -> None:
        package = Path(c2lab.__file__).parent
        lab_runtime = (package / "lab_runtime.py").read_text(encoding="utf-8")

        for required in (
            "tempfile.TemporaryDirectory",
            "tempfile.NamedTemporaryFile",
            "os.scandir",
            "os.fstat",
        ):
            self.assertIn(required, lab_runtime)
        for forbidden in (
            "os.environ",
            "os.getcwd",
            "Path.home",
            "expanduser",
            "os.walk",
            "glob(",
            "shutil.make_archive",
        ):
            self.assertNotIn(forbidden, lab_runtime)

        for source_path in package.rglob("*.py"):
            if source_path.name == "lab_runtime.py":
                continue
            source = source_path.read_text(encoding="utf-8")
            self.assertNotIn("tempfile.TemporaryDirectory", source)
            self.assertNotIn("tempfile.NamedTemporaryFile", source)

    def test_only_the_foreground_node_contains_an_http_client(self) -> None:
        package = Path(c2lab.__file__).parent
        users = []
        for source_path in package.rglob("*.py"):
            source = source_path.read_text(encoding="utf-8")
            if "urllib.request" in source:
                users.append(source_path.name)
        self.assertEqual(users, ["node.py"])

    def test_no_dangerous_capability_vocabulary_is_exposed(self) -> None:
        package = Path(c2lab.__file__).parent
        combined = "\n".join(
            source_path.read_text(encoding="utf-8").lower()
            for source_path in package.rglob("*.py")
        )
        forbidden_identifiers = (
            "run_command",
            "upload_file",
            "download_file",
            "execute_shell",
            "keylogger",
            "credential_dump",
            "persistence_install",
        )
        for identifier in forbidden_identifiers:
            self.assertNotIn(identifier, combined)


if __name__ == "__main__":
    unittest.main()
