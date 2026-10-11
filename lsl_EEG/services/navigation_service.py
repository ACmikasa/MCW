"""CSV-backed navigation configuration shared by all web pages."""

from __future__ import annotations

import csv
import os
from pathlib import Path
from threading import RLock


FIELDS = ["id", "title", "path", "icon", "permission", "sort_order", "is_visible"]
DEFAULT_ITEMS = [
    ("首页仪表盘", "/", "⌂", "dashboard:view", 10, True),
    ("实时监测", "/realtime", "⌁", "realtime:view", 20, True),
    ("离线检测", "/offline", "◫", "offline:analyze", 30, True),
    ("历史记录", "/history", "◷", "history:view", 35, True),
    ("数据分析", "/analysis", "⌁", "analysis:run", 37, True),
    ("数据管理", "/data", "▤", "data:view", 60, True),
    ("导航管理", "/navigation", "◇", "navigation:manage", 70, True),
    ("用户与权限", "/admin", "♙", "users:manage", 80, True),
]


class NavigationError(ValueError):
    pass


class NavigationService:
    def __init__(self, storage_dir: Path) -> None:
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.storage_dir / "navigation.csv"
        self._lock = RLock()
        if not self.path.exists():
            self._write([
                {"id": str(index), "title": title, "path": path, "icon": icon,
                 "permission": permission, "sort_order": str(order), "is_visible": "1" if visible else "0"}
                for index, (title, path, icon, permission, order, visible) in enumerate(DEFAULT_ITEMS, 1)
            ])
        else:
            self._add_missing_defaults()

    def _add_missing_defaults(self) -> None:
        """Add newly shipped system entries without overwriting user edits."""
        rows = self._read()
        existing_paths = {row["path"] for row in rows}
        next_id = max((int(row["id"]) for row in rows), default=0) + 1
        changed = False
        # Correct the temporary order used by the first release of these two
        # built-in entries while leaving all other customized rows untouched.
        order_migrations = {("/history", "40"): "35", ("/analysis", "50"): "37"}
        for row in rows:
            migrated = order_migrations.get((row["path"], row["sort_order"]))
            if migrated:
                row["sort_order"] = migrated
                changed = True
        for title, path, icon, permission, order, visible in DEFAULT_ITEMS:
            if path in existing_paths:
                continue
            rows.append({"id": str(next_id), "title": title, "path": path, "icon": icon,
                         "permission": permission, "sort_order": str(order),
                         "is_visible": "1" if visible else "0"})
            next_id += 1
            changed = True
        if changed:
            self._write(rows)

    def _read(self) -> list[dict[str, str]]:
        with self.path.open("r", encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))

    def _write(self, rows: list[dict]) -> None:
        temporary = self.path.with_suffix(".csv.tmp")
        with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.path)

    @staticmethod
    def _public(row: dict[str, str]) -> dict:
        return {
            "id": int(row["id"]), "title": row["title"], "path": row["path"],
            "icon": row["icon"], "permission": row["permission"],
            "sort_order": int(row["sort_order"]), "is_visible": row["is_visible"] == "1",
        }

    def list_items(self, permissions: list[str] | None = None, include_hidden: bool = False) -> list[dict]:
        with self._lock:
            items = [self._public(row) for row in self._read()]
        if not include_hidden:
            allowed = set(permissions or [])
            items = [item for item in items if item["is_visible"] and (not item["permission"] or item["permission"] in allowed)]
        return sorted(items, key=lambda item: (item["sort_order"], item["id"]))

    def create(self, payload: dict) -> dict:
        with self._lock:
            rows = self._read()
            item = self._validated(payload)
            item["id"] = str(max((int(row["id"]) for row in rows), default=0) + 1)
            rows.append(item)
            self._write(rows)
            return self._public(item)

    def update(self, item_id: int, payload: dict) -> dict:
        with self._lock:
            rows = self._read()
            row = next((item for item in rows if int(item["id"]) == item_id), None)
            if row is None:
                raise NavigationError("导航项不存在")
            row.update(self._validated(payload))
            self._write(rows)
            return self._public(row)

    def delete(self, item_id: int) -> None:
        with self._lock:
            rows = self._read()
            if not any(int(row["id"]) == item_id for row in rows):
                raise NavigationError("导航项不存在")
            self._write([row for row in rows if int(row["id"]) != item_id])

    @staticmethod
    def _validated(payload: dict) -> dict[str, str]:
        title = str(payload.get("title", "")).strip()
        path = str(payload.get("path", "")).strip()
        permission = str(payload.get("permission", "")).strip()
        if not title or len(title) > 30:
            raise NavigationError("导航名称须为 1-30 个字符")
        if not path.startswith("/") or path.startswith("//"):
            raise NavigationError("导航地址必须是站内绝对路径")
        try:
            order = int(payload.get("sort_order", 0))
        except (TypeError, ValueError) as error:
            raise NavigationError("排序值必须是整数") from error
        return {
            "title": title, "path": path, "icon": str(payload.get("icon", "•")).strip()[:4] or "•",
            "permission": permission, "sort_order": str(order),
            "is_visible": "1" if bool(payload.get("is_visible", True)) else "0",
        }
