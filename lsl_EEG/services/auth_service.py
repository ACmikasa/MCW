"""Portable CSV-backed users, roles, permissions, and login sessions."""

from __future__ import annotations

import csv
import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import RLock


PERMISSIONS = {
    "dashboard:view": "查看首页仪表盘",
    "realtime:view": "查看实时监测",
    "realtime:control": "控制实时采集与轮椅",
    "offline:analyze": "上传并执行离线检测",
    "history:view": "查看历史记录与回放波形",
    "analysis:run": "执行数据分析",
    "data:view": "查看和下载数据文件",
    "data:manage": "删除数据文件",
    "navigation:manage": "管理系统导航",
    "users:manage": "管理用户",
    "roles:manage": "管理角色与权限",
}

DEFAULT_ROLES = {
    "admin": ("系统管理员", tuple(PERMISSIONS)),
    "operator": ("操作员", ("dashboard:view", "realtime:view", "realtime:control", "offline:analyze", "history:view", "analysis:run", "data:view")),
    "viewer": ("观察员", ("dashboard:view", "realtime:view", "history:view")),
}

SCHEMAS = {
    "users": ["id", "username", "display_name", "password_hash", "role_id", "is_active", "created_at", "updated_at"],
    "roles": ["id", "name", "description", "is_system"],
    "role_permissions": ["role_id", "permission_code"],
    "permissions": ["code", "description"],
    "sessions": ["token_hash", "user_id", "expires_at", "created_at"],
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def _hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 310_000)
    return f"pbkdf2_sha256$310000${salt.hex()}${digest.hex()}"


def _verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt_hex, expected_hex = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations)
        )
        return hmac.compare_digest(actual, bytes.fromhex(expected_hex))
    except (ValueError, TypeError):
        return False


class AuthError(ValueError):
    pass


class AuthService:
    """Small CSV repository suitable for a single-process local deployment."""

    def __init__(self, storage_dir: Path, session_hours: int = 12) -> None:
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.session_hours = session_hours
        self._lock = RLock()
        self._initialize()

    def _path(self, table: str) -> Path:
        return self.storage_dir / f"{table}.csv"

    def _read(self, table: str) -> list[dict[str, str]]:
        path = self._path(table)
        if not path.exists():
            return []
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))

    def _write(self, table: str, rows: list[dict]) -> None:
        """Atomically replace one CSV file after a complete successful write."""
        path = self._path(table)
        temporary = path.with_suffix(".csv.tmp")
        with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=SCHEMAS[table], extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)

    @staticmethod
    def _next_id(rows: list[dict[str, str]]) -> int:
        return max((int(row["id"]) for row in rows), default=0) + 1

    def _initialize(self) -> None:
        with self._lock:
            self._write("permissions", [
                {"code": code, "description": description} for code, description in PERMISSIONS.items()
            ])
            roles = self._read("roles")
            role_permissions = self._read("role_permissions")
            changed = False
            for name, (description, permissions) in DEFAULT_ROLES.items():
                role = next((row for row in roles if row["name"] == name), None)
                if role is None:
                    role = {"id": str(self._next_id(roles)), "name": name, "description": description, "is_system": "1"}
                    roles.append(role)
                    existing = {(row["role_id"], row["permission_code"]) for row in role_permissions}
                    role_permissions.extend(
                        {"role_id": role["id"], "permission_code": permission}
                        for permission in permissions if (role["id"], permission) not in existing
                    )
                    changed = True
                # Keep built-in roles forward-compatible when new system
                # permissions are introduced. Custom roles remain untouched.
                existing = {(row["role_id"], row["permission_code"]) for row in role_permissions}
                for permission in permissions:
                    if (role["id"], permission) not in existing:
                        role_permissions.append({"role_id": role["id"], "permission_code": permission})
                        existing.add((role["id"], permission))
                        changed = True
            if changed or not self._path("roles").exists():
                self._write("roles", roles)
                self._write("role_permissions", role_permissions)
            users = self._read("users")
            if not users:
                username = os.environ.get("AUTH_ADMIN_USERNAME", "admin").strip() or "admin"
                password = os.environ.get("AUTH_ADMIN_PASSWORD", "Admin@123456")
                admin = next(role for role in roles if role["name"] == "admin")
                now = _iso(_utcnow())
                users.append({
                    "id": "1", "username": username, "display_name": "系统管理员",
                    "password_hash": _hash_password(password), "role_id": admin["id"],
                    "is_active": "1", "created_at": now, "updated_at": now,
                })
                self._write("users", users)
            if not self._path("sessions").exists():
                self._write("sessions", [])

    @staticmethod
    def validate_password(password: str) -> None:
        if len(password) < 8 or not any(c.isalpha() for c in password) or not any(c.isdigit() for c in password):
            raise AuthError("密码至少 8 位，且必须同时包含字母和数字")

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def authenticate(self, username: str, password: str) -> tuple[str, dict] | None:
        with self._lock:
            user = next(
                (row for row in self._read("users") if row["username"].casefold() == username.strip().casefold()), None
            )
            if user is None or user["is_active"] != "1" or not _verify_password(password, user["password_hash"]):
                return None
            token = secrets.token_urlsafe(32)
            now = _utcnow()
            sessions = [row for row in self._read("sessions") if row["expires_at"] > _iso(now)]
            sessions.append({
                "token_hash": self._token_hash(token), "user_id": user["id"],
                "expires_at": _iso(now + timedelta(hours=self.session_hours)), "created_at": _iso(now),
            })
            self._write("sessions", sessions)
            public_user = self.get_user(int(user["id"]))
            assert public_user is not None
            return token, public_user

    def user_from_token(self, token: str | None) -> dict | None:
        if not token:
            return None
        with self._lock:
            now = _iso(_utcnow())
            current = next(
                (row for row in self._read("sessions") if row["token_hash"] == self._token_hash(token) and row["expires_at"] > now), None
            )
            return self.get_user(int(current["user_id"])) if current else None

    def logout(self, token: str | None) -> None:
        if not token:
            return
        with self._lock:
            digest = self._token_hash(token)
            self._write("sessions", [row for row in self._read("sessions") if row["token_hash"] != digest])

    def verify_user_password(self, user_id: int, password: str) -> bool:
        with self._lock:
            row = next((item for item in self._read("users") if int(item["id"]) == user_id), None)
            return bool(row and row["is_active"] == "1" and _verify_password(password, row["password_hash"]))

    def get_user(self, user_id: int) -> dict | None:
        with self._lock:
            row = next((item for item in self._read("users") if int(item["id"]) == user_id), None)
            if row is None:
                return None
            role = next((item for item in self._read("roles") if item["id"] == row["role_id"]), None)
            if role is None:
                return None
            permissions = sorted(
                item["permission_code"] for item in self._read("role_permissions") if item["role_id"] == role["id"]
            )
            return {
                "id": int(row["id"]), "username": row["username"], "display_name": row["display_name"],
                "is_active": row["is_active"] == "1", "created_at": row["created_at"],
                "updated_at": row["updated_at"], "role": role["name"],
                "role_description": role["description"], "permissions": permissions,
            }

    def list_users(self) -> list[dict]:
        with self._lock:
            return [user for row in self._read("users") if (user := self.get_user(int(row["id"]))) is not None]

    def create_user(self, username: str, display_name: str, password: str, role_id: int, active: bool = True) -> dict:
        username, display_name = username.strip(), display_name.strip()
        if len(username) < 3 or len(username) > 50 or not all(c.isalnum() or c in "._-" for c in username):
            raise AuthError("用户名须为 3-50 位字母、数字、点、下划线或连字符")
        if not display_name:
            raise AuthError("显示名称不能为空")
        self.validate_password(password)
        with self._lock:
            users, roles = self._read("users"), self._read("roles")
            if any(row["username"].casefold() == username.casefold() for row in users):
                raise AuthError("用户名已存在")
            if not any(int(row["id"]) == role_id for row in roles):
                raise AuthError("所选角色无效")
            user_id, now = self._next_id(users), _iso(_utcnow())
            users.append({
                "id": str(user_id), "username": username, "display_name": display_name,
                "password_hash": _hash_password(password), "role_id": str(role_id),
                "is_active": "1" if active else "0", "created_at": now, "updated_at": now,
            })
            self._write("users", users)
            created = self.get_user(user_id)
            assert created is not None
            return created

    def update_user(self, user_id: int, display_name: str, role_id: int, active: bool) -> dict:
        if not display_name.strip():
            raise AuthError("显示名称不能为空")
        with self._lock:
            users, roles = self._read("users"), self._read("roles")
            current = next((row for row in users if int(row["id"]) == user_id), None)
            target_role = next((row for row in roles if int(row["id"]) == role_id), None)
            if current is None:
                raise AuthError("用户不存在")
            if target_role is None:
                raise AuthError("所选角色不存在")
            current_role = next(row for row in roles if row["id"] == current["role_id"])
            if current["is_active"] == "1" and current_role["name"] == "admin" and (not active or target_role["name"] != "admin"):
                if self._active_admin_count(users, roles) <= 1:
                    raise AuthError("系统必须保留至少一名启用的管理员")
            current.update({
                "display_name": display_name.strip(), "role_id": str(role_id),
                "is_active": "1" if active else "0", "updated_at": _iso(_utcnow()),
            })
            self._write("users", users)
            if not active:
                self._remove_user_sessions(user_id)
            updated = self.get_user(user_id)
            assert updated is not None
            return updated

    def set_password(self, user_id: int, password: str) -> None:
        self.validate_password(password)
        with self._lock:
            users = self._read("users")
            user = next((row for row in users if int(row["id"]) == user_id), None)
            if user is None:
                raise AuthError("用户不存在")
            user.update({"password_hash": _hash_password(password), "updated_at": _iso(_utcnow())})
            self._write("users", users)
            self._remove_user_sessions(user_id)

    def delete_user(self, user_id: int, acting_user_id: int) -> None:
        if user_id == acting_user_id:
            raise AuthError("不能删除当前登录用户")
        with self._lock:
            users, roles = self._read("users"), self._read("roles")
            target = next((row for row in users if int(row["id"]) == user_id), None)
            if target is None:
                raise AuthError("用户不存在")
            role = next(row for row in roles if row["id"] == target["role_id"])
            if target["is_active"] == "1" and role["name"] == "admin" and self._active_admin_count(users, roles) <= 1:
                raise AuthError("系统必须保留至少一名启用的管理员")
            self._write("users", [row for row in users if int(row["id"]) != user_id])
            self._remove_user_sessions(user_id)

    @staticmethod
    def _active_admin_count(users: list[dict], roles: list[dict]) -> int:
        admin_ids = {row["id"] for row in roles if row["name"] == "admin"}
        return sum(row["is_active"] == "1" and row["role_id"] in admin_ids for row in users)

    def _remove_user_sessions(self, user_id: int) -> None:
        self._write("sessions", [row for row in self._read("sessions") if int(row["user_id"]) != user_id])

    def list_roles(self) -> list[dict]:
        with self._lock:
            role_permissions = self._read("role_permissions")
            return [{
                "id": int(row["id"]), "name": row["name"], "description": row["description"],
                "is_system": row["is_system"] == "1",
                "permissions": sorted(
                    item["permission_code"] for item in role_permissions if item["role_id"] == row["id"]
                ),
            } for row in self._read("roles")]

    def list_permissions(self) -> list[dict]:
        with self._lock:
            return self._read("permissions")

    def create_role(self, name: str, description: str, permissions: list[str]) -> dict:
        name = name.strip().lower()
        self._validate_role(name, permissions)
        with self._lock:
            roles = self._read("roles")
            if any(row["name"].casefold() == name.casefold() for row in roles):
                raise AuthError("角色名已存在")
            role_id = self._next_id(roles)
            roles.append({"id": str(role_id), "name": name, "description": description.strip(), "is_system": "0"})
            self._write("roles", roles)
            self._replace_permissions(role_id, permissions)
            return next(role for role in self.list_roles() if role["id"] == role_id)

    def update_role(self, role_id: int, description: str, permissions: list[str]) -> dict:
        self._validate_role("valid", permissions)
        with self._lock:
            roles = self._read("roles")
            role = next((row for row in roles if int(row["id"]) == role_id), None)
            if role is None:
                raise AuthError("角色不存在")
            if role["name"] == "admin" and not set(PERMISSIONS).issubset(permissions):
                raise AuthError("管理员角色必须保留全部系统权限")
            role["description"] = description.strip()
            self._write("roles", roles)
            self._replace_permissions(role_id, permissions)
            return next(item for item in self.list_roles() if item["id"] == role_id)

    def delete_role(self, role_id: int) -> None:
        with self._lock:
            roles = self._read("roles")
            role = next((row for row in roles if int(row["id"]) == role_id), None)
            if role is None:
                raise AuthError("角色不存在")
            if role["is_system"] == "1":
                raise AuthError("系统预置角色不能删除")
            if any(int(row["role_id"]) == role_id for row in self._read("users")):
                raise AuthError("该角色仍被用户使用，不能删除")
            self._write("roles", [row for row in roles if int(row["id"]) != role_id])
            self._write("role_permissions", [
                row for row in self._read("role_permissions") if int(row["role_id"]) != role_id
            ])

    @staticmethod
    def _validate_role(name: str, permissions: list[str]) -> None:
        if len(name) < 2 or len(name) > 30 or not all(c.isalnum() or c in "_-" for c in name):
            raise AuthError("角色名须为 2-30 位字母、数字、下划线或连字符")
        invalid = set(permissions) - set(PERMISSIONS)
        if invalid:
            raise AuthError(f"包含未知权限：{', '.join(sorted(invalid))}")

    def _replace_permissions(self, role_id: int, permissions: list[str]) -> None:
        rows = [row for row in self._read("role_permissions") if int(row["role_id"]) != role_id]
        rows.extend({"role_id": str(role_id), "permission_code": code} for code in sorted(set(permissions)))
        self._write("role_permissions", rows)
