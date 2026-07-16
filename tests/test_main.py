from __future__ import annotations

import unittest
from contextlib import contextmanager
from unittest import mock

from c2lab import __main__ as cli


class SigtermContextTests(unittest.TestCase):
    def test_sigterm_becomes_keyboard_interrupt_and_restores_handler(self) -> None:
        main_thread = object()
        previous_handler = object()
        sigterm = cli.signal.SIGTERM

        with (
            mock.patch.object(cli.threading, "current_thread", return_value=main_thread),
            mock.patch.object(cli.threading, "main_thread", return_value=main_thread),
            mock.patch.object(cli.signal, "getsignal", return_value=previous_handler),
            mock.patch.object(cli.signal, "signal") as set_handler,
            self.assertRaises(KeyboardInterrupt),
        ):
            with cli._sigterm_as_keyboard_interrupt():
                installed_handler = set_handler.call_args_list[0].args[1]
                installed_handler(sigterm, None)

        self.assertEqual(
            set_handler.call_args_list,
            [
                mock.call(sigterm, installed_handler),
                mock.call(sigterm, previous_handler),
            ],
        )

    def test_non_main_thread_is_a_noop(self) -> None:
        with (
            mock.patch.object(cli.threading, "current_thread", return_value=object()),
            mock.patch.object(cli.threading, "main_thread", return_value=object()),
            mock.patch.object(cli.signal, "getsignal") as get_handler,
            mock.patch.object(cli.signal, "signal") as set_handler,
        ):
            with cli._sigterm_as_keyboard_interrupt():
                pass

        get_handler.assert_not_called()
        set_handler.assert_not_called()

    def test_missing_sigterm_is_a_noop(self) -> None:
        main_thread = object()
        with (
            mock.patch.object(cli.signal, "SIGTERM", None),
            mock.patch.object(cli.threading, "current_thread", return_value=main_thread),
            mock.patch.object(cli.threading, "main_thread", return_value=main_thread),
            mock.patch.object(cli.signal, "getsignal") as get_handler,
            mock.patch.object(cli.signal, "signal") as set_handler,
        ):
            with cli._sigterm_as_keyboard_interrupt():
                pass

        get_handler.assert_not_called()
        set_handler.assert_not_called()

    def test_handler_install_failure_is_a_noop(self) -> None:
        main_thread = object()
        with (
            mock.patch.object(cli.threading, "current_thread", return_value=main_thread),
            mock.patch.object(cli.threading, "main_thread", return_value=main_thread),
            mock.patch.object(cli.signal, "getsignal", return_value=object()),
            mock.patch.object(cli.signal, "signal", side_effect=ValueError("unsupported")),
        ):
            with cli._sigterm_as_keyboard_interrupt():
                pass


class MainSignalIntegrationTests(unittest.TestCase):
    def test_teamserver_execution_uses_sigterm_context(self) -> None:
        lifecycle: list[str] = []

        @contextmanager
        def tracked_context():
            lifecycle.append("enter")
            try:
                yield
            finally:
                lifecycle.append("exit")

        with (
            mock.patch.object(cli, "_sigterm_as_keyboard_interrupt", tracked_context),
            mock.patch.object(cli, "run_teamserver", return_value=17) as run_teamserver,
        ):
            result = cli.main(["teamserver", "--port", "0"])

        self.assertEqual(result, 17)
        self.assertEqual(lifecycle, ["enter", "exit"])
        run_teamserver.assert_called_once_with(0)

    def test_node_execution_uses_sigterm_context(self) -> None:
        lifecycle: list[str] = []

        @contextmanager
        def tracked_context():
            lifecycle.append("enter")
            try:
                yield
            finally:
                lifecycle.append("exit")

        with (
            mock.patch.object(cli, "_sigterm_as_keyboard_interrupt", tracked_context),
            mock.patch.object(cli, "run_node", return_value=23) as run_node,
        ):
            result = cli.main(
                [
                    "node",
                    "--name",
                    "signal-test-node",
                    "--enroll-token",
                    "enrollment-token-123456",
                ]
            )

        self.assertEqual(result, 23)
        self.assertEqual(lifecycle, ["enter", "exit"])
        run_node.assert_called_once_with(
            controller_url="http://127.0.0.1:8765",
            enrollment_token="enrollment-token-123456",
            name="signal-test-node",
            version=cli.__version__,
            profile="training",
            poll_interval_ms=1_000,
            jitter_percent=0,
        )


class TeamserverRoleBootstrapTests(unittest.TestCase):
    def test_teamserver_bootstraps_fixed_short_lived_roles_and_safe_access_log(self) -> None:
        state = object()
        runtime = mock.Mock()
        registry = mock.Mock()
        server = mock.Mock()
        server.server_address = ("127.0.0.1", 43210)
        server.serve_forever.side_effect = KeyboardInterrupt
        generated = [
            "admin-token-" + "a" * 24,
            "operator-token-" + "b" * 24,
            "viewer-token-" + "c" * 24,
            "enrollment-token-" + "d" * 24,
        ]

        with (
            mock.patch.object(cli, "LabState", return_value=state),
            mock.patch.object(cli, "LabRuntime", return_value=runtime),
            mock.patch.object(cli, "OperatorSessionRegistry", return_value=registry),
            mock.patch.object(cli.secrets, "token_urlsafe", side_effect=generated),
            mock.patch.object(cli, "create_server", return_value=server) as create_server,
            mock.patch("builtins.print"),
        ):
            result = cli.run_teamserver(0)

        self.assertEqual(result, 0)
        self.assertEqual(
            registry.register.call_args_list,
            [
                mock.call(
                    "local-admin",
                    "admin",
                    generated[0],
                    ttl_seconds=cli.DEFAULT_SESSION_TTL_SECONDS,
                ),
                mock.call(
                    "task-operator",
                    "operator",
                    generated[1],
                    ttl_seconds=cli.DEFAULT_SESSION_TTL_SECONDS,
                ),
                mock.call(
                    "read-viewer",
                    "viewer",
                    generated[2],
                    ttl_seconds=cli.DEFAULT_SESSION_TTL_SECONDS,
                ),
            ],
        )
        create_server.assert_called_once_with(
            state,
            generated[0],
            generated[3],
            0,
            operator_registry=registry,
            runtime=runtime,
            access_log=True,
        )
        runtime.start.assert_called_once_with()
        runtime.stop.assert_called_once_with()
        server.shutdown.assert_not_called()
        server.server_close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
