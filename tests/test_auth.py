from __future__ import annotations

import concurrent.futures
import hmac
import unittest
from unittest import mock

from c2lab.auth import (
    MAX_OPERATOR_SESSIONS,
    PERMISSIONS,
    ROLE_PERMISSIONS,
    AuthError,
    OperatorSessionRegistry,
)


def token(label: str) -> str:
    return f"test-generated-{label}-" + "x" * 32


class OperatorSessionRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = OperatorSessionRegistry()

    def test_fixed_role_permission_mapping_is_immutable(self) -> None:
        self.assertEqual(
            ROLE_PERMISSIONS["admin"],
            frozenset(
                {
                    "read",
                    "task_write",
                    "exercise_write",
                    "containment_write",
                    "note_write",
                    "reset",
                    "operator_admin",
                }
            ),
        )
        self.assertEqual(
            ROLE_PERMISSIONS["operator"],
            frozenset({"read", "task_write", "exercise_write", "note_write"}),
        )
        self.assertEqual(ROLE_PERMISSIONS["viewer"], frozenset({"read"}))
        self.assertEqual(
            PERMISSIONS,
            {
                "read",
                "task_write",
                "exercise_write",
                "containment_write",
                "note_write",
                "reset",
                "operator_admin",
            },
        )
        with self.assertRaises(TypeError):
            ROLE_PERMISSIONS["admin"] = frozenset()  # type: ignore[index]

    def test_registration_authentication_and_permissions(self) -> None:
        session = self.registry.register(
            "training-operator",
            "operator",
            token("operator"),
            ttl_seconds=60,
            now=100.0,
        )

        self.assertEqual(session["status"], "active")
        self.assertEqual(session["expires_monotonic"], 160.0)
        self.assertEqual(session["expires_in_seconds"], 60.0)
        self.assertEqual(
            self.registry.authenticate(token("operator"), permission="task_write", now=159.9)[
                "id"
            ],
            session["id"],
        )
        self.assertEqual(
            self.registry.authenticate(token("operator"), permission="note_write", now=159.9)[
                "id"
            ],
            session["id"],
        )
        self.assertIsNone(
            self.registry.authenticate(token("operator"), permission="reset", now=159.9)
        )
        self.assertIsNone(self.registry.authenticate(token("wrong"), now=159.9))

    def test_expired_and_revoked_tokens_are_rejected(self) -> None:
        expired = self.registry.register(
            "expiry-viewer", "viewer", token("expired"), ttl_seconds=10, now=20.0
        )
        self.assertIsNotNone(self.registry.authenticate(token("expired"), now=29.999))
        self.assertIsNone(self.registry.authenticate(token("expired"), now=30.0))
        self.assertEqual(self.registry.list(now=30.0)[0]["status"], "expired")

        revoked = self.registry.register(
            "revoked-viewer", "viewer", token("revoked"), ttl_seconds=100, now=20.0
        )
        result = self.registry.revoke(revoked["id"], now=21.0)
        self.assertEqual(result["status"], "revoked")
        self.assertIsNone(self.registry.authenticate(token("revoked"), now=21.0))
        self.assertEqual(expired["principal_id"], "expiry-viewer")

    def test_registered_token_check_includes_expired_and_revoked_sessions(self) -> None:
        expired_secret = token("registered-expired")
        revoked_secret = token("registered-revoked")
        self.registry.register(
            "expired-session",
            "viewer",
            expired_secret,
            ttl_seconds=1,
            now=10.0,
        )
        revoked = self.registry.register(
            "revoked-session",
            "viewer",
            revoked_secret,
            ttl_seconds=100,
            now=10.0,
        )
        self.registry.revoke(revoked["id"], now=11.0)

        self.assertIsNone(self.registry.authenticate(expired_secret, now=11.0))
        self.assertIsNone(self.registry.authenticate(revoked_secret, now=11.0))
        self.assertTrue(self.registry.has_registered_token(expired_secret))
        self.assertTrue(self.registry.has_registered_token(revoked_secret))
        self.assertFalse(self.registry.has_registered_token(token("unknown")))
        self.assertFalse(self.registry.has_registered_token(None))

    def test_list_and_returned_sessions_never_expose_token_material(self) -> None:
        secret = token("redaction")
        registered = self.registry.register("audit-viewer", "viewer", secret, now=10.0)
        registered["permissions"].append("reset")

        listed = self.registry.list(now=11.0)
        authenticated = self.registry.authenticate(secret, now=11.0)
        self.assertNotIn(secret, repr(listed))
        for public_session in (listed[0], authenticated):
            self.assertIsNotNone(public_session)
            self.assertTrue(all("token" not in key for key in public_session))
            self.assertEqual(public_session["permissions"], ["read"])

    def test_authentication_uses_constant_time_digest_comparison(self) -> None:
        secret = token("constant-time")
        self.registry.register("constant-viewer", "viewer", secret, now=0.0)

        with mock.patch("c2lab.auth.hmac.compare_digest", wraps=hmac.compare_digest) as compare:
            self.assertIsNotNone(self.registry.authenticate(secret, now=1.0))

        compare.assert_called()
        left, right = compare.call_args.args
        self.assertIsInstance(left, bytes)
        self.assertEqual(len(left), len(right))

    def test_principal_role_token_and_ttl_are_strictly_validated(self) -> None:
        invalid_principals = (
            None,
            "ab",
            "Admin",
            "-admin",
            "admin-",
            "admin..one",
            "alice.ops",
            "admin/one",
            "admin one",
            "a" * 65,
        )
        for principal_id in invalid_principals:
            with self.subTest(principal_id=principal_id), self.assertRaises(AuthError):
                self.registry.register(principal_id, "viewer", token("principal"), now=0.0)

        with self.assertRaises(AuthError):
            self.registry.register("valid-user", "owner", token("role"), now=0.0)
        with self.assertRaises(AuthError):
            self.registry.register("valid-user", "viewer", "too-short", now=0.0)
        with self.assertRaises(AuthError):
            self.registry.register("valid-user", "viewer", token("ttl"), ttl_seconds=0, now=0.0)
        with self.assertRaises(AuthError):
            self.registry.register(
                "valid-user", "viewer", token("infinite"), ttl_seconds=float("inf"), now=0.0
            )

    def test_duplicate_generated_token_is_rejected(self) -> None:
        secret = token("duplicate")
        self.registry.register("first-user", "viewer", secret, now=0.0)
        with self.assertRaises(AuthError) as context:
            self.registry.register("second-user", "viewer", secret, now=0.0)
        self.assertEqual(context.exception.code, "token_conflict")
        self.assertEqual(context.exception.status, 409)

    def test_admin_can_revoke_another_session_but_operator_cannot(self) -> None:
        admin = self.registry.register("local-admin", "admin", token("admin"), now=0.0)
        operator = self.registry.register("task-operator", "operator", token("task"), now=0.0)
        viewer = self.registry.register("read-viewer", "viewer", token("read"), now=0.0)

        with self.assertRaises(AuthError) as context:
            self.registry.revoke(viewer["id"], actor_session_id=operator["id"], now=1.0)
        self.assertEqual(context.exception.code, "forbidden")

        revoked = self.registry.revoke(
            operator["id"], actor_session_id=admin["id"], now=2.0
        )
        self.assertEqual(revoked["status"], "revoked")
        self.assertIsNone(self.registry.authenticate(token("task"), now=2.0))
        self.assertIsNotNone(self.registry.authenticate(token("read"), now=2.0))

    def test_revoked_target_does_not_bypass_actor_authorization(self) -> None:
        admin = self.registry.register("revoke-admin", "admin", token("revoke-admin"), now=0.0)
        operator = self.registry.register(
            "revoke-operator",
            "operator",
            token("revoke-operator"),
            now=0.0,
        )
        viewer = self.registry.register("revoke-viewer", "viewer", token("revoke-viewer"), now=0.0)
        self.registry.revoke(viewer["id"], actor_session_id=admin["id"], now=1.0)

        with self.assertRaises(AuthError) as context:
            self.registry.revoke(
                viewer["id"],
                actor_session_id=operator["id"],
                now=2.0,
            )
        self.assertEqual(context.exception.code, "forbidden")
        replayed = self.registry.revoke(viewer["id"], actor_session_id=viewer["id"], now=2.0)
        self.assertEqual(replayed["status"], "revoked")

    def test_last_active_admin_session_cannot_revoke_itself(self) -> None:
        first = self.registry.register("first-admin", "admin", token("admin-one"), now=0.0)
        with self.assertRaises(AuthError) as context:
            self.registry.revoke(first["id"], actor_session_id=first["id"], now=1.0)
        self.assertEqual(context.exception.code, "last_admin_session")
        self.assertIsNotNone(self.registry.authenticate(token("admin-one"), now=1.0))

        self.registry.register("second-admin", "admin", token("admin-two"), now=1.0)
        revoked = self.registry.revoke(first["id"], now=2.0)
        self.assertEqual(revoked["status"], "revoked")
        self.assertIsNone(self.registry.authenticate(token("admin-one"), now=2.0))

    def test_registry_operations_are_thread_safe(self) -> None:
        def register_and_authenticate(index: int) -> str:
            principal_id = f"viewer-{index}"
            secret = token(f"thread-{index:03d}")
            self.registry.register(principal_id, "viewer", secret, now=100.0)
            authenticated = self.registry.authenticate(secret, permission="read", now=101.0)
            assert authenticated is not None
            return authenticated["principal_id"]

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            principals = set(executor.map(register_and_authenticate, range(40)))

        self.assertEqual(principals, {f"viewer-{index}" for index in range(40)})
        self.assertEqual(len(self.registry.list(now=101.0)), 40)

    def test_session_retention_is_bounded_and_only_inactive_records_are_pruned(self) -> None:
        expired_secret = token("expired-capacity")
        self.registry.register(
            "expired-capacity",
            "viewer",
            expired_secret,
            ttl_seconds=1,
            now=0.0,
        )
        for index in range(1, MAX_OPERATOR_SESSIONS):
            self.registry.register(
                f"active-{index}",
                "viewer",
                token(f"capacity-{index}"),
                now=0.0,
            )

        replacement = self.registry.register(
            "replacement-viewer",
            "viewer",
            token("replacement"),
            now=2.0,
        )
        self.assertEqual(len(self.registry.list(now=2.0)), MAX_OPERATOR_SESSIONS)
        self.assertIsNone(self.registry.authenticate(expired_secret, now=2.0))
        self.assertEqual(replacement["status"], "active")

        with self.assertRaises(AuthError) as context:
            self.registry.register(
                "overflow-viewer",
                "viewer",
                token("overflow"),
                now=2.0,
            )
        self.assertEqual(context.exception.code, "session_limit")
        self.assertEqual(context.exception.status, 429)


if __name__ == "__main__":
    unittest.main()
