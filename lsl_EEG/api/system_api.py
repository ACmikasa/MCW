"""Dashboard, navigation, and EEG data-management endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse

from services.auth_service import AuthService, PERMISSIONS
from services.data_service import DataError, DataService
from services.history_service import HistoryService
from services.navigation_service import NavigationError, NavigationService
from services.offline_service import offline_service
from data.loaders import load_eeg_file
from processing.signal_analysis import analyze_signal, preview_chunk


def build_system_router(auth: AuthService, navigation: NavigationService, data: DataService,
                        history: HistoryService, receiver, current_user, require) -> APIRouter:
    router = APIRouter(prefix="/api/system", tags=["system"])

    async def body(request: Request) -> dict:
        try:
            value = await request.json()
            return value if isinstance(value, dict) else {}
        except Exception as error:
            raise HTTPException(status_code=400, detail="请求内容必须是 JSON") from error

    def translate(action):
        try:
            return action()
        except (NavigationError, DataError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @router.get("/dashboard")
    async def dashboard(_: dict = Depends(require("dashboard:view"))):
        status = receiver.status()
        users = auth.list_users()
        records = data.list_records()
        jobs = offline_service.list_jobs(6)
        return {
            "stream": status,
            "counts": {
                "users": len(users), "active_users": sum(user["is_active"] for user in users),
                "files": len(records), "storage_bytes": sum(item["size_bytes"] for item in records),
                "completed_jobs": sum(job["status"] == "completed" for job in jobs),
            },
            "recent_files": records[:5], "recent_jobs": jobs,
        }

    @router.get("/navigation")
    async def visible_navigation(user: dict = Depends(current_user)):
        return {"items": navigation.list_items(user["permissions"])}

    @router.get("/navigation/all")
    async def all_navigation(_: dict = Depends(require("navigation:manage"))):
        return {"items": navigation.list_items(include_hidden=True), "permissions": PERMISSIONS}

    @router.post("/navigation", status_code=201)
    async def create_navigation(request: Request, _: dict = Depends(require("navigation:manage"))):
        payload = await body(request)
        if payload.get("permission", "") not in PERMISSIONS and payload.get("permission", "") != "":
            raise HTTPException(status_code=400, detail="所选权限不存在")
        return translate(lambda: navigation.create(payload))

    # FastAPI handlers are kept explicit so malformed JSON receives a clear response.
    @router.put("/navigation/{item_id}")
    async def update_navigation(item_id: int, request: Request, _: dict = Depends(require("navigation:manage"))):
        payload = await body(request)
        if payload.get("permission", "") not in PERMISSIONS and payload.get("permission", "") != "":
            raise HTTPException(status_code=400, detail="所选权限不存在")
        return translate(lambda: navigation.update(item_id, payload))

    @router.delete("/navigation/{item_id}")
    async def delete_navigation(item_id: int, _: dict = Depends(require("navigation:manage"))):
        translate(lambda: navigation.delete(item_id))
        return {"success": True}

    @router.get("/data")
    async def list_data(
        q: str = Query(default="", max_length=100), file_type: str = Query(default="", max_length=10),
        _: dict = Depends(require("data:view")),
    ):
        items = data.list_records(q, file_type)
        for item in items:
            job = offline_service.get_job(item["job_id"])
            if job:
                item["status"] = job["status"]
        return {"items": items, "stats": data.stats()}

    @router.get("/data/{record_id}/download")
    async def download_data(record_id: str, _: dict = Depends(require("data:view"))):
        path, record = translate(lambda: data.get_path(record_id))
        return FileResponse(path, filename=record["original_name"], media_type="application/octet-stream")

    @router.delete("/data/{record_id}")
    async def delete_data(record_id: str, _: dict = Depends(require("data:manage"))):
        translate(lambda: data.delete(record_id))
        return {"success": True}

    @router.get("/history")
    async def list_history(
        q: str = Query(default="", max_length=100), record_type: str = Query(default="", max_length=20),
        status: str = Query(default="", max_length=20), date_from: str = Query(default="", max_length=10),
        date_to: str = Query(default="", max_length=10), _: dict = Depends(require("history:view")),
    ):
        items = [{"record_type": "acquisition", "name": task["title"], "created_at": task["started_at"], **task}
                 for task in history.list_tasks()]
        for item in data.list_records():
            job = offline_service.get_job(item["job_id"])
            if job:
                item["status"] = job["status"]
            items.append({"record_type": "file", "name": item["original_name"], **item})
        query = q.strip().casefold()
        items = [item for item in items
                 if (not query or query in item["name"].casefold())
                 and (not record_type or item["record_type"] == record_type)
                 and (not status or item.get("status") == status)
                 and (not date_from or item["created_at"][:10] >= date_from)
                 and (not date_to or item["created_at"][:10] <= date_to)]
        return {"items": sorted(items, key=lambda item: item["created_at"], reverse=True),
                "counts": {"all": len(items), "acquisition": sum(x["record_type"] == "acquisition" for x in items),
                           "file": sum(x["record_type"] == "file" for x in items)}}

    @router.get("/history/files/{record_id}/preview")
    async def history_preview(record_id: str, _: dict = Depends(require("history:view"))):
        try:
            path, record = data.get_path(record_id)
            return {"record": record, **preview_chunk(load_eeg_file(path))}
        except (DataError, ValueError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @router.get("/history/files/{record_id}/download")
    async def history_download(record_id: str, _: dict = Depends(require("history:view"))):
        try:
            path, record = data.get_path(record_id)
            return FileResponse(path, filename=record["original_name"], media_type="application/octet-stream")
        except DataError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @router.get("/analysis/files")
    async def analysis_files(_: dict = Depends(require("analysis:run"))):
        return {"items": [item for item in data.list_records() if item["file_type"] in {"csv", "edf", "bdf"}]}

    @router.post("/analysis/run")
    async def run_analysis(request: Request, _: dict = Depends(require("analysis:run"))):
        payload = await body(request)
        try:
            record_id = str(payload.get("record_id", ""))
            path, record = data.get_path(record_id)
            chunk = load_eeg_file(path, float(payload.get("sample_rate", 500)))
            result = analyze_signal(chunk, int(payload.get("channel", 0)), float(payload.get("low", 1)),
                                    float(payload.get("high", 45)), float(payload.get("notch", 50)))
            return {"record": record, **result}
        except (DataError, ValueError, TypeError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    return router
