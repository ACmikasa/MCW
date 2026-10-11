"""HTTP endpoints for offline file analysis jobs."""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from services.offline_service import offline_service


def build_offline_router(upload_dir: Path) -> APIRouter:
    router = APIRouter(prefix="/api/offline", tags=["offline"])
    upload_dir.mkdir(parents=True, exist_ok=True)

    @router.post("/jobs")
    async def create_job(
        background_tasks: BackgroundTasks,
        request: Request,
        profile: str = "全局快速筛查",
        window: str = "4 秒 · 步长 1 秒",
    ):
        content_type = request.headers.get("content-type", "")
        body = await request.body()
        original_name, file_bytes = _extract_upload(content_type, body)
        suffix = Path(original_name).suffix.lower()
        if suffix not in {".csv", ".edf", ".bdf"}:
            raise HTTPException(status_code=400, detail="仅支持 CSV、EDF、BDF 文件")

        destination = upload_dir / f"{uuid.uuid4().hex}{suffix}"
        destination.write_bytes(file_bytes)

        job_id = offline_service.create_job(destination, original_name, profile, window)
        background_tasks.add_task(offline_service.run_job, job_id)
        return {"job_id": job_id, "status": "queued"}

    @router.get("/jobs/{job_id}")
    async def get_job(job_id: str):
        job = offline_service.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="离线任务不存在")
        return job

    return router


def _extract_upload(content_type: str, body: bytes) -> tuple[str, bytes]:
    """Read the single FormData file without requiring python-multipart.

    Keeping this tiny parser local makes the app importable in minimal Python
    environments; production deployments can still install python-multipart.
    """
    if "multipart/form-data" not in content_type:
        raise HTTPException(status_code=400, detail="请使用 multipart/form-data 上传文件")
    boundary_token = next((part.strip() for part in content_type.split(";") if part.strip().startswith("boundary=")), "")
    boundary = boundary_token.removeprefix("boundary=").strip('"').encode()
    if not boundary:
        raise HTTPException(status_code=400, detail="上传请求缺少 boundary")
    for part in body.split(b"--" + boundary):
        if b"filename=" not in part:
            continue
        header, separator, payload = part.partition(b"\r\n\r\n")
        if not separator:
            continue
        filename_marker = b'filename="'
        start = header.find(filename_marker)
        if start < 0:
            continue
        start += len(filename_marker)
        end = header.find(b'"', start)
        filename = header[start:end if end >= 0 else len(header)].decode("utf-8", errors="replace")
        payload = payload.rstrip(b"\r\n-")
        return Path(filename).name or "recording.csv", payload
    raise HTTPException(status_code=400, detail="上传请求中没有找到文件")
