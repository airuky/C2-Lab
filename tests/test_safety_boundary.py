from __future__ import annotations

import ast
import json
import unittest
from pathlib import Path
from types import MappingProxyType

import c2lab
from c2lab.node import validate_controller_url


class SafetyBoundaryTests(unittest.TestCase):
    """AST-based static analysis verifying thirteen safety boundaries.

    §1  Shell/command execution — subprocess, os.system etc. statically forbidden
    §2  Host file access — lab_runtime ephemeral workspace only
    §3  External communication — 127.0.0.1 only, no external bind/connect
    §4  Persistence — memory-only state, no database or file-backed storage
    §5  OS reconnaissance — no hostname, PID, username, or NIC collection
    §6  Implant — foreground process only, no background persistence
    §7  Payload generation / listener — zero capability
    §8  Transport — no external listener, relay, P2P, tunnel, or remote operator
    §9  Evasion — no obfuscation, traffic shaping, or detection evasion
    §10 Plugin — no runtime plugin loading, dynamic modules, or extensions
    §11 TLS — HTTPS absent (loopback-only, plaintext by design)
    §12 Multi-tenant — no persistent accounts, dynamic RBAC, HA, or backup
    §13 Remote — no remote host administration or cross-machine management
    """

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

    # ── §4 永続化: state は全てメモリのみ、再起動で消滅 ──

    def test_no_persistence_or_database_modules_are_imported(self) -> None:
        """No database, ORM, or object-serialization module is imported."""

        package = Path(c2lab.__file__).parent
        persistence_modules = {
            "sqlite3",
            "shelve",
            "dbm",
            "lmdb",
            "redis",
            "pymongo",
            "sqlalchemy",
            "peewee",
            "pickle",
            "configparser",
        }
        findings: list[str] = []
        for source_path in package.rglob("*.py"):
            tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        root = alias.name.split(".", 1)[0]
                        if root in persistence_modules:
                            findings.append(f"{source_path.name}: import {alias.name}")
                elif isinstance(node, ast.ImportFrom) and node.module:
                    root = node.module.split(".", 1)[0]
                    if root in persistence_modules:
                        findings.append(f"{source_path.name}: from {node.module}")
        self.assertEqual(findings, [], "persistence module imports found")

    def test_state_containers_are_volatile_memory_structures(self) -> None:
        """LabState and OperatorSessionRegistry use only dict/deque, never files."""

        package = Path(c2lab.__file__).parent
        core_source = (package / "core.py").read_text(encoding="utf-8")
        auth_source = (package / "auth.py").read_text(encoding="utf-8")

        for forbidden in ("sqlite3", "shelve", "dbm", "pickle", "open(", "Path.home"):
            self.assertNotIn(forbidden, core_source, f"core.py: {forbidden}")
            self.assertNotIn(forbidden, auth_source, f"auth.py: {forbidden}")

        self.assertIn("self._nodes: dict", core_source)
        self.assertIn("self._tasks: dict", core_source)
        self.assertIn("deque(maxlen=", core_source)
        self.assertIn("self._sessions: dict", auth_source)

    # ── §3 外部通信: 127.0.0.1 以外への bind/connect が不可能 ──

    def test_server_binds_exclusively_to_loopback_address(self) -> None:
        """Server hardcodes 127.0.0.1 and never uses a wildcard bind."""

        package = Path(c2lab.__file__).parent
        server_source = (package / "server.py").read_text(encoding="utf-8")

        self.assertIn('LOOPBACK_HOST = "127.0.0.1"', server_source)
        self.assertNotIn("0.0.0.0", server_source)
        self.assertIn("(LOOPBACK_HOST, port)", server_source)

    def test_node_rejects_non_loopback_controller_urls(self) -> None:
        """validate_controller_url blocks external IPs, HTTPS, and wildcards."""

        for url in (
            "http://192.168.1.1:8765",
            "http://10.0.0.1:8765",
            "http://example.com:8765",
            "https://127.0.0.1:8765",
            "http://0.0.0.0:8765",
            "http://[::1]:8765",
            "http://172.16.0.1:8765",
            "ftp://127.0.0.1:8765",
        ):
            with self.assertRaises(ValueError, msg=f"should reject {url}"):
                validate_controller_url(url)

        self.assertEqual(validate_controller_url("http://127.0.0.1:8765"), "http://127.0.0.1:8765")
        self.assertEqual(validate_controller_url("http://localhost:8765"), "http://127.0.0.1:8765")

    def test_no_raw_socket_or_bind_connect_calls(self) -> None:
        """No raw socket creation, bind, or connect calls anywhere."""

        package = Path(c2lab.__file__).parent
        network_calls = {"bind", "connect", "listen", "accept", "sendto", "recvfrom"}
        findings: list[str] = []
        for source_path in package.rglob("*.py"):
            tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
                    if node.func.value.id == "socket" and node.func.attr in network_calls:
                        findings.append(f"{source_path.name}:{node.lineno}: socket.{node.func.attr}")
        self.assertEqual(findings, [], "raw socket calls found")

    def test_no_sse_long_poll_or_streaming_transport(self) -> None:
        """No Server-Sent Events, long poll, or streaming transport vocabulary."""

        package = Path(c2lab.__file__).parent
        combined = "\n".join(
            source_path.read_text(encoding="utf-8").lower()
            for source_path in package.rglob("*.py")
        )
        streaming_vocabulary = (
            "text/event-stream", "server_sent_event",
            "event_source", "eventsource",
            "long_poll", "long_polling", "comet_transport",
            "streaming_response", "chunked_transfer",
        )
        for term in streaming_vocabulary:
            self.assertNotIn(term, combined, f"streaming transport found: {term}")

    def test_node_disables_proxy_and_rejects_redirects(self) -> None:
        """NodeClient opener explicitly disables proxies and rejects redirects."""

        package = Path(c2lab.__file__).parent
        node_source = (package / "node.py").read_text(encoding="utf-8")
        self.assertIn("ProxyHandler({})", node_source)
        self.assertIn("_NoRedirectHandler()", node_source)
        self.assertIn("def redirect_request", node_source)
        self.assertIn("return None", node_source)

    # ── §5 OS偵察: hostname, PID, ユーザー名, NIC 情報を一切収集しない ──

    def test_no_host_identity_or_os_reconnaissance_calls(self) -> None:
        """No code path collects hostname, PID, username, or NIC information."""

        package = Path(c2lab.__file__).parent
        recon_calls = {
            "getlogin", "getuid", "getgid", "getpid", "getppid",
            "uname", "gethostname", "getfqdn", "getuser",
            "getnode", "uuid1", "ifaddresses", "getaddrinfo",
            "gethostbyname", "gethostbyaddr",
        }
        recon_imports = {"netifaces", "ifaddr", "scapy", "nmap"}
        findings: list[str] = []

        for source_path in package.rglob("*.py"):
            tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        root = alias.name.split(".", 1)[0]
                        if root in recon_imports:
                            findings.append(f"{source_path.name}: import {alias.name}")
                elif isinstance(node, ast.ImportFrom) and node.module:
                    root = node.module.split(".", 1)[0]
                    if root in recon_imports:
                        findings.append(f"{source_path.name}: from {node.module}")
                elif isinstance(node, ast.Call):
                    name = None
                    if isinstance(node.func, ast.Name):
                        name = node.func.id
                    elif isinstance(node.func, ast.Attribute):
                        name = node.func.attr
                    if name in recon_calls:
                        findings.append(f"{source_path.name}:{node.lineno}: recon call {name}")
        self.assertEqual(findings, [], "OS reconnaissance surface found")

    def test_no_environment_variable_access_outside_ephemeral_workspace(self) -> None:
        """os.environ is not accessed anywhere in the package."""

        package = Path(c2lab.__file__).parent
        for source_path in package.rglob("*.py"):
            source = source_path.read_text(encoding="utf-8")
            self.assertNotIn(
                "os.environ",
                source,
                f"{source_path.name} accesses os.environ",
            )

    # ── §6 Implant: foreground process のみ、バックグラウンド常駐なし ──

    def test_no_process_detach_or_background_persistence_primitives(self) -> None:
        """No fork, setsid, or process-detachment primitives in the package."""

        package = Path(c2lab.__file__).parent
        detach_calls = {"fork", "setsid", "setpgrp", "setuid", "setgid", "chroot"}
        detach_imports = {"daemon", "daemonize", "python_daemon"}
        findings: list[str] = []

        for source_path in package.rglob("*.py"):
            tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        root = alias.name.split(".", 1)[0]
                        if root in detach_imports:
                            findings.append(f"{source_path.name}: import {alias.name}")
                elif isinstance(node, ast.ImportFrom) and node.module:
                    root = node.module.split(".", 1)[0]
                    if root in detach_imports:
                        findings.append(f"{source_path.name}: from {node.module}")
                elif isinstance(node, ast.Call):
                    name = None
                    if isinstance(node.func, ast.Name):
                        name = node.func.id
                    elif isinstance(node.func, ast.Attribute):
                        name = node.func.attr
                    if name in detach_calls:
                        findings.append(f"{source_path.name}:{node.lineno}: detach call {name}")
        self.assertEqual(findings, [], "process detachment primitives found")

    def test_server_daemon_threads_is_disabled(self) -> None:
        """LabHTTPServer.daemon_threads is explicitly False."""

        package = Path(c2lab.__file__).parent
        server_source = (package / "server.py").read_text(encoding="utf-8")
        self.assertIn("daemon_threads = False", server_source)
        self.assertNotIn("daemon_threads = True", server_source)

    def test_no_autostart_service_or_scheduled_persistence(self) -> None:
        """No cron, systemd, launchd, or registry-based persistence mechanisms."""

        package = Path(c2lab.__file__).parent
        combined = "\n".join(
            source_path.read_text(encoding="utf-8").lower()
            for source_path in package.rglob("*.py")
        )
        persistence_vocabulary = (
            "crontab",
            "systemd",
            "launchd",
            "launchagent",
            "registry_key",
            "winreg",
            "startup_folder",
            "run_at_login",
            "install_service",
            "scheduled_task",
        )
        for term in persistence_vocabulary:
            self.assertNotIn(term, combined, f"persistence vocabulary found: {term}")

    # ── §7 Payload生成/リスナー: ゼロ ──

    def test_no_payload_generation_listener_or_implant_vocabulary(self) -> None:
        """No offensive-tooling vocabulary for payload generation or listeners."""

        package = Path(c2lab.__file__).parent
        combined = "\n".join(
            source_path.read_text(encoding="utf-8").lower()
            for source_path in package.rglob("*.py")
        )
        offensive_vocabulary = (
            "generate_payload",
            "shellcode",
            "bind_shell",
            "reverse_shell",
            "meterpreter",
            "staged_payload",
            "stageless",
            "stager",
            "payload_generator",
            "c2_channel",
            "exfiltrate",
            "reflective_load",
            "process_inject",
            "hollow_process",
            "persistence_mechanism",
            "dropper",
            "implant_factory",
            "beacon_config",
            "cobalt_strike",
            "sliver_generate",
            "file_transfer",
        )
        for term in offensive_vocabulary:
            self.assertNotIn(term, combined, f"offensive vocabulary found: {term}")

    # ── §8 Transport: 外部 listener、relay、P2P、tunnel、remote operator 接続なし ──

    def test_no_external_transport_or_relay_modules(self) -> None:
        """No tunnelling, relay, P2P, or external transport library is imported."""

        package = Path(c2lab.__file__).parent
        transport_modules = {
            "paramiko", "fabric", "asyncssh", "pexpect",
            "sshtunnel", "chisel", "rpyc", "pyro4", "pyro5",
            "zmq", "pika", "kombu", "celery", "dask",
            "grpc", "thrift", "xmlrpc", "twisted",
            "tornado", "websockets", "websocket",
        }
        findings: list[str] = []
        for source_path in package.rglob("*.py"):
            tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        root = alias.name.split(".", 1)[0]
                        if root in transport_modules:
                            findings.append(f"{source_path.name}: import {alias.name}")
                elif isinstance(node, ast.ImportFrom) and node.module:
                    root = node.module.split(".", 1)[0]
                    if root in transport_modules:
                        findings.append(f"{source_path.name}: from {node.module}")
        self.assertEqual(findings, [], "external transport module imports found")

    def test_no_transport_vocabulary_in_source(self) -> None:
        """No relay, tunnel, proxy-chain, or P2P vocabulary appears in source."""

        package = Path(c2lab.__file__).parent
        combined = "\n".join(
            source_path.read_text(encoding="utf-8").lower()
            for source_path in package.rglob("*.py")
        )
        transport_vocabulary = (
            "ssh_tunnel", "reverse_tunnel", "port_forward",
            "socks_proxy", "proxy_chain", "relay_server",
            "p2p_connect", "mesh_network", "pivot_host",
            "listener_bind", "external_listener",
            "remote_operator", "operator_relay",
        )
        for term in transport_vocabulary:
            self.assertNotIn(term, combined, f"transport vocabulary found: {term}")

    # ── §9 Evasion: obfuscation、traffic shaping、detection evasion なし ──

    def test_no_evasion_or_obfuscation_modules(self) -> None:
        """No encoding, encryption, or obfuscation library beyond hashlib."""

        package = Path(c2lab.__file__).parent
        evasion_modules = {
            "cryptography", "pycryptodome", "pycryptodomex",
            "nacl", "fernet", "pyaes", "rsa",
            "steganography", "stegano",
            "pyarmor", "pyobfuscate", "cython",
        }
        findings: list[str] = []
        for source_path in package.rglob("*.py"):
            tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        root = alias.name.split(".", 1)[0]
                        if root in evasion_modules:
                            findings.append(f"{source_path.name}: import {alias.name}")
                elif isinstance(node, ast.ImportFrom) and node.module:
                    root = node.module.split(".", 1)[0]
                    if root in evasion_modules:
                        findings.append(f"{source_path.name}: from {node.module}")
        self.assertEqual(findings, [], "evasion/obfuscation module imports found")

    def test_no_evasion_vocabulary_in_source(self) -> None:
        """No traffic shaping, obfuscation, or detection evasion vocabulary."""

        package = Path(c2lab.__file__).parent
        combined = "\n".join(
            source_path.read_text(encoding="utf-8").lower()
            for source_path in package.rglob("*.py")
        )
        evasion_vocabulary = (
            "obfuscate", "deobfuscate", "traffic_shaping",
            "jitter_payload", "sleep_obfuscation",
            "amsi_bypass", "etw_patch", "unhook_ntdll",
            "syscall_stub", "direct_syscall",
            "domain_fronting", "malleable_profile",
            "encrypt_traffic", "xor_encode", "base64_shellcode",
            "polymorphic", "metamorphic",
            "anti_debug", "anti_sandbox", "anti_vm",
            "edr_bypass", "av_evasion",
        )
        for term in evasion_vocabulary:
            self.assertNotIn(term, combined, f"evasion vocabulary found: {term}")

    # ── §10 Plugin: runtime plugin、動的 module loading、extension なし ──

    def test_no_dynamic_module_loading_primitives(self) -> None:
        """No importlib, pkgutil, or plugin-loading primitives in the package."""

        package = Path(c2lab.__file__).parent
        plugin_modules = {
            "importlib", "pkgutil", "pluggy", "stevedore",
            "pkg_resources", "setuptools",
        }
        plugin_calls = {
            "__import__", "import_module", "find_module",
            "load_module", "iter_modules", "walk_packages",
        }
        findings: list[str] = []
        for source_path in package.rglob("*.py"):
            tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        root = alias.name.split(".", 1)[0]
                        if root in plugin_modules:
                            findings.append(f"{source_path.name}: import {alias.name}")
                elif isinstance(node, ast.ImportFrom) and node.module:
                    root = node.module.split(".", 1)[0]
                    if root in plugin_modules:
                        findings.append(f"{source_path.name}: from {node.module}")
                elif isinstance(node, ast.Call):
                    name = None
                    if isinstance(node.func, ast.Name):
                        name = node.func.id
                    elif isinstance(node.func, ast.Attribute):
                        name = node.func.attr
                    if name in plugin_calls:
                        findings.append(f"{source_path.name}:{node.lineno}: plugin call {name}")
        self.assertEqual(findings, [], "dynamic module loading primitives found")

    def test_no_plugin_extension_vocabulary_in_source(self) -> None:
        """No plugin registry, extension point, or hook system vocabulary."""

        package = Path(c2lab.__file__).parent
        combined = "\n".join(
            source_path.read_text(encoding="utf-8").lower()
            for source_path in package.rglob("*.py")
        )
        plugin_vocabulary = (
            "plugin_registry", "load_plugin", "register_plugin",
            "extension_point", "hook_manager", "plugin_loader",
            "dynamic_module", "hot_reload", "addon_manager",
            "module_discovery", "entry_point",
        )
        for term in plugin_vocabulary:
            self.assertNotIn(term, combined, f"plugin vocabulary found: {term}")

    # ── §11 TLS: HTTPS なし — loopback 限定のため plaintext で十分 ──

    def test_no_tls_or_ssl_configuration(self) -> None:
        """No TLS/SSL context, certificate, or HTTPS binding in the package."""

        package = Path(c2lab.__file__).parent
        tls_modules = {"ssl", "certifi", "truststore"}
        tls_calls = {
            "create_default_context", "SSLContext", "wrap_socket",
            "load_cert_chain", "load_verify_locations",
        }
        findings: list[str] = []
        for source_path in package.rglob("*.py"):
            tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        root = alias.name.split(".", 1)[0]
                        if root in tls_modules:
                            findings.append(f"{source_path.name}: import {alias.name}")
                elif isinstance(node, ast.ImportFrom) and node.module:
                    root = node.module.split(".", 1)[0]
                    if root in tls_modules:
                        findings.append(f"{source_path.name}: from {node.module}")
                elif isinstance(node, ast.Call):
                    name = None
                    if isinstance(node.func, ast.Name):
                        name = node.func.id
                    elif isinstance(node.func, ast.Attribute):
                        name = node.func.attr
                    if name in tls_calls:
                        findings.append(f"{source_path.name}:{node.lineno}: TLS call {name}")
        self.assertEqual(findings, [], "TLS/SSL primitives found")

    def test_node_validates_http_scheme_only(self) -> None:
        """validate_controller_url rejects https:// — loopback needs no TLS."""

        with self.assertRaises(ValueError):
            validate_controller_url("https://127.0.0.1:8765")
        with self.assertRaises(ValueError):
            validate_controller_url("https://localhost:8765")

    # ── §12 Multi-tenant: 永続 account、動的 RBAC、HA、backup/restore なし ──

    def test_rbac_roles_are_fixed_not_dynamic(self) -> None:
        """ROLE_PERMISSIONS is a frozen mapping with exactly three roles."""

        from c2lab.auth import ROLE_PERMISSIONS, ROLES
        self.assertEqual(ROLES, frozenset({"admin", "operator", "viewer"}))
        self.assertIsInstance(ROLE_PERMISSIONS, type(MappingProxyType({})))
        for role, perms in ROLE_PERMISSIONS.items():
            self.assertIsInstance(perms, frozenset)

    def test_tracked_launcher_does_not_use_external_scripts_or_token_files(self) -> None:
        """The optional launcher starts the package directly and never persists secrets."""

        package = Path(c2lab.__file__).parent
        launcher_path = package.parent / ".claude" / "launch.json"
        if not launcher_path.exists():
            return
        launcher = json.loads(launcher_path.read_text(encoding="utf-8"))
        configurations = launcher.get("configurations")
        self.assertIsInstance(configurations, list)
        self.assertGreater(len(configurations), 0)
        for configuration in configurations:
            self.assertEqual(
                configuration.get("runtimeArgs"),
                ["-m", "c2lab", "teamserver", "--port", "8765"],
            )
        serialized = json.dumps(launcher).lower()
        for forbidden in ("scratchpad", "tokens.json", "/private/tmp", "/tmp/"):
            self.assertNotIn(forbidden, serialized)

    def test_no_multi_tenant_or_ha_vocabulary_in_source(self) -> None:
        """No clustering, replication, backup, or multi-tenant vocabulary."""

        package = Path(c2lab.__file__).parent
        combined = "\n".join(
            source_path.read_text(encoding="utf-8").lower()
            for source_path in package.rglob("*.py")
        )
        multitenant_vocabulary = (
            "cluster_join", "raft_consensus", "leader_election",
            "replication_factor", "failover", "hot_standby",
            "backup_state", "restore_state", "snapshot_persist",
            "tenant_id", "multi_tenant", "organization_id",
            "dynamic_role", "create_role", "delete_role",
            "role_hierarchy", "permission_grant",
        )
        for term in multitenant_vocabulary:
            self.assertNotIn(term, combined, f"multi-tenant vocabulary found: {term}")

    def test_no_persistent_account_storage(self) -> None:
        """Operator sessions are memory-only with no disk-backed user store."""

        package = Path(c2lab.__file__).parent
        auth_source = (package / "auth.py").read_text(encoding="utf-8")
        self.assertIn("self._sessions: dict", auth_source)
        for forbidden in ("save_sessions", "load_sessions", "dump(", "user_database"):
            self.assertNotIn(forbidden, auth_source, f"auth.py: {forbidden}")

    # ── §13 Remote: 別物理端末の管理、remote administration なし ──

    def test_no_remote_administration_modules(self) -> None:
        """No WMI, WinRM, SSH, or remote execution library is imported."""

        package = Path(c2lab.__file__).parent
        remote_modules = {
            "paramiko", "fabric", "ansible", "salt",
            "pywinrm", "wmi", "impacket", "psexec",
            "winreg", "pypsexec", "smbclient",
        }
        findings: list[str] = []
        for source_path in package.rglob("*.py"):
            tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        root = alias.name.split(".", 1)[0]
                        if root in remote_modules:
                            findings.append(f"{source_path.name}: import {alias.name}")
                elif isinstance(node, ast.ImportFrom) and node.module:
                    root = node.module.split(".", 1)[0]
                    if root in remote_modules:
                        findings.append(f"{source_path.name}: from {node.module}")
        self.assertEqual(findings, [], "remote administration module imports found")

    def test_no_remote_administration_vocabulary_in_source(self) -> None:
        """No remote host management, lateral movement, or cross-machine vocabulary."""

        package = Path(c2lab.__file__).parent
        combined = "\n".join(
            source_path.read_text(encoding="utf-8").lower()
            for source_path in package.rglob("*.py")
        )
        remote_vocabulary = (
            "remote_host", "remote_execute", "lateral_movement",
            "wmi_query", "winrm_session", "psexec_command",
            "ssh_execute", "remote_shell", "remote_admin",
            "deploy_agent", "install_agent", "agent_callback",
            "smb_share", "net_use", "pass_the_hash",
        )
        for term in remote_vocabulary:
            self.assertNotIn(term, combined, f"remote administration vocabulary found: {term}")


if __name__ == "__main__":
    unittest.main()
