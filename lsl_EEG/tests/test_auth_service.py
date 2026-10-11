from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from services.auth_service import AuthError, AuthService, PERMISSIONS


class AuthServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.service = AuthService(Path(self.temp.name) / "auth")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_bootstrap_admin_can_authenticate_and_has_all_permissions(self) -> None:
        result = self.service.authenticate("admin", "Admin@123456")
        self.assertIsNotNone(result)
        assert result is not None
        token, user = result
        self.assertEqual(user["role"], "admin")
        self.assertEqual(set(user["permissions"]), set(PERMISSIONS))
        self.assertEqual(self.service.user_from_token(token)["id"], user["id"])

    def test_existing_system_roles_receive_new_default_permissions(self) -> None:
        admin = next(role for role in self.service.list_roles() if role["name"] == "admin")
        rows = self.service._read("role_permissions")
        self.service._write("role_permissions", [
            row for row in rows
            if not (row["role_id"] == str(admin["id"]) and row["permission_code"] == "navigation:manage")
        ])
        migrated = AuthService(Path(self.temp.name) / "auth")
        admin = next(role for role in migrated.list_roles() if role["name"] == "admin")
        self.assertIn("navigation:manage", admin["permissions"])

    def test_create_user_and_role_permissions(self) -> None:
        role = self.service.create_role("researcher", "研究人员", ["realtime:view", "offline:analyze"])
        user = self.service.create_user("tester", "测试用户", "Password123", role["id"])
        self.assertEqual(user["role"], "researcher")
        self.assertEqual(user["permissions"], ["offline:analyze", "realtime:view"])
        self.assertIsNotNone(self.service.authenticate("tester", "Password123"))

    def test_password_policy_and_last_admin_guard(self) -> None:
        with self.assertRaises(AuthError):
            self.service.set_password(1, "short")
        viewer = next(role for role in self.service.list_roles() if role["name"] == "viewer")
        with self.assertRaises(AuthError):
            self.service.update_user(1, "管理员", viewer["id"], True)


if __name__ == "__main__":
    unittest.main()
