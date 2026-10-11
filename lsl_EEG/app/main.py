"""FastAPI application for realtime LSL streaming and offline analysis."""

from __future__ import annotations

import asyncio
import argparse
import os
import sys
import time
from pathlib import Path

# Support both ``python -m app.main`` and VS Code's direct-file execution.
# The latter sets sys.path[0] to ``app/`` instead of the ``lsl_EEG/`` root.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import Depends, FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from acquisition.lsl_receiver import LSLReceiver
from api.auth_api import SESSION_COOKIE, build_auth_router
from api.offline_api import build_offline_router
from api.system_api import build_system_router
from app.config import SYSTEM_DATA_DIR, UPLOAD_DIR, WEB_DIR, auth_data_dir, session_hours, stream_name, stream_type
from services.auth_service import AuthService
from services.data_service import DataService
from services.history_service import HistoryService
from services.navigation_service import NavigationService


receiver = LSLReceiver(stream_name=stream_name(), stream_type=stream_type())
auth_service = AuthService(auth_data_dir(), session_hours())
navigation_service = NavigationService(SYSTEM_DATA_DIR)
data_service = DataService(UPLOAD_DIR, SYSTEM_DATA_DIR)
history_service = HistoryService(SYSTEM_DATA_DIR)
app = FastAPI(title="LSL EEG Dashboard")
app.mount("/assets", StaticFiles(directory=WEB_DIR / "assets"), name="assets")
auth_router = build_auth_router(auth_service)
app.include_router(auth_router)
app.include_router(
    build_offline_router(UPLOAD_DIR, data_service),
    dependencies=[Depends(auth_router.require("offline:analyze"))],  # type: ignore[attr-defined]
)
app.include_router(build_system_router(
    auth_service, navigation_service, data_service, history_service, receiver,
    auth_router.current_user, auth_router.require,  # type: ignore[attr-defined]
))


def _page_user(request: Request, permission: str | None = None) -> dict | None:
    user = auth_service.user_from_token(request.cookies.get(SESSION_COOKIE))
    if user is None or (permission and permission not in user["permissions"]):
        return None
    return user


def _forbidden() -> HTMLResponse:
    return HTMLResponse(
        "<meta charset='utf-8'><title>无权访问</title>"
        "<body style='font-family:sans-serif;background:#07101f;color:#fff;padding:48px'>"
        "<h1>无权访问</h1><p>当前账号没有访问此页面的权限。</p><a href='/' style='color:#72b5ff'>返回首页</a></body>",
        status_code=403,
    )


@app.get("/login")
async def login_page(request: Request):
    user = _page_user(request)
    if user:
        if "dashboard:view" in user["permissions"]:
            return RedirectResponse("/")
        if "realtime:view" in user["permissions"]:
            return RedirectResponse("/realtime")
        if "offline:analyze" in user["permissions"]:
            return RedirectResponse("/offline")
        if "history:view" in user["permissions"]:
            return RedirectResponse("/history")
        if "analysis:run" in user["permissions"]:
            return RedirectResponse("/analysis")
        if "data:view" in user["permissions"]:
            return RedirectResponse("/data")
        if "navigation:manage" in user["permissions"]:
            return RedirectResponse("/navigation")
        if "users:manage" in user["permissions"]:
            return RedirectResponse("/admin")
        return _forbidden()
    return FileResponse(WEB_DIR / "login.html")


@app.get("/")
async def index(request: Request):
    if not _page_user(request):
        return RedirectResponse("/login")
    if not _page_user(request, "dashboard:view"):
        return _forbidden()
    return FileResponse(WEB_DIR / "dashboard.html")


@app.get("/realtime")
async def realtime_index(request: Request):
    if not _page_user(request):
        return RedirectResponse("/login")
    if not _page_user(request, "realtime:view"):
        return _forbidden()
    return FileResponse(WEB_DIR / "frontend.html")


@app.get("/offline")
async def offline_index(request: Request):
    if not _page_user(request):
        return RedirectResponse("/login")
    if not _page_user(request, "offline:analyze"):
        return _forbidden()
    return FileResponse(WEB_DIR / "offline-detection.html")


@app.get("/admin")
async def admin_index(request: Request):
    if not _page_user(request):
        return RedirectResponse("/login")
    if not _page_user(request, "users:manage"):
        return _forbidden()
    return FileResponse(WEB_DIR / "admin.html")


@app.get("/navigation")
async def navigation_index(request: Request):
    if not _page_user(request):
        return RedirectResponse("/login")
    if not _page_user(request, "navigation:manage"):
        return _forbidden()
    return FileResponse(WEB_DIR / "navigation.html")


@app.get("/data")
async def data_index(request: Request):
    if not _page_user(request):
        return RedirectResponse("/login")
    if not _page_user(request, "data:view"):
        return _forbidden()
    return FileResponse(WEB_DIR / "data-management.html")


@app.get("/history")
async def history_index(request: Request):
    if not _page_user(request):
        return RedirectResponse("/login")
    if not _page_user(request, "history:view"):
        return _forbidden()
    return FileResponse(WEB_DIR / "history.html")


@app.get("/analysis")
async def analysis_index(request: Request):
    if not _page_user(request):
        return RedirectResponse("/login")
    if not _page_user(request, "analysis:run"):
        return _forbidden()
    return FileResponse(WEB_DIR / "analysis.html")


@app.get("/health")
async def health():
    return {"status": "ok", "source": "lsl", **receiver.status()}


@app.get("/overshoot_key")
async def overshoot_key(_: dict = Depends(auth_router.require("realtime:control"))):  # type: ignore[attr-defined]
    return {"key": os.environ.get("OVERSHOOT_API_KEY", "")}


def state_message() -> dict:
    status = receiver.status()
    port_label = f"LSL: {status['name']}" if status["name"] else "LSL 数据流（自动发现）"
    return {
        "type": "state",
        "is_connected": status["connected"],
        "is_streaming": status["streaming"],
        "serial_port": port_label,
        "sampling_rate": status["sampling_rate"],
        "num_channels": status["num_channels"],
        "channel_labels": status["channel_labels"],
        "ports": [port_label],
        "wheelchair_enabled": False,
        "has_calibration": False,
        "source": "lsl",
        "error": status["error"],
    }


async def handle_command(websocket: WebSocket, message: dict, user: dict) -> None:
    command = message.get("command")
    if command == "get_state":
        await websocket.send_json(state_message())
    elif "realtime:control" not in user["permissions"]:
        await websocket.send_json({"type": "error", "error": "没有实时控制权限"})
    elif command == "detect_ports":
        status = receiver.status()
        label = f"LSL: {status['name']}" if status["name"] else "LSL 数据流（自动发现）"
        await websocket.send_json({"type": "ports", "ports": [label]})
    elif command == "connect":
        receiver.start()
        deadline = asyncio.get_running_loop().time() + 2.0
        while not receiver.status()["connected"] and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.05)
        status = receiver.status()
        await websocket.send_json({
            "type": "connect_result",
            "success": status["connected"],
            "error": status["error"] or "正在搜索 LSL 数据流，请稍候",
            "num_channels": status["num_channels"],
            "sampling_rate": status["sampling_rate"],
            "has_calibration": False,
        })
    elif command == "start":
        receiver.resume_streaming()
        connected = receiver.status()["connected"]
        if connected:
            history_service.start(user, receiver.status())
        await websocket.send_json({"type": "start_result", "success": connected, "error": "尚未连接 LSL 数据流" if not connected else ""})
    elif command == "stop":
        receiver.stop_streaming()
        history_service.stop(user["id"], receiver.status())
        await websocket.send_json({"type": "stop_result", "success": True, "total_samples": receiver.status()["total_samples"]})
    elif command == "disconnect":
        history_service.stop(user["id"], receiver.status(), "interrupted")
        receiver.disconnect()
        await websocket.send_json({"type": "disconnect_result", "success": True})
    elif command == "arduino_list_ports":
        await websocket.send_json({"type": "arduino_ports", "ports": []})
    elif command in {"wheelchair_enable", "wheelchair_disable"}:
        await websocket.send_json({"type": "wheelchair_status", "enabled": False})


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    user = auth_service.user_from_token(websocket.cookies.get(SESSION_COOKIE))
    if user is None or "realtime:view" not in user["permissions"]:
        await websocket.close(code=4401)
        return
    await websocket.accept()
    last_sequence = 0
    last_state_signature = None
    try:
        while True:
            try:
                message = await asyncio.wait_for(websocket.receive_json(), timeout=0.02)
                await handle_command(websocket, message, user)
            except asyncio.TimeoutError:
                pass

            status = receiver.status()
            if not status["connected"] or status["sequence"] < last_sequence:
                last_sequence = 0
            state_signature = (
                status["connected"], status["streaming"], status["name"],
                status["sampling_rate"], status["num_channels"], status["error"],
            )
            if state_signature != last_state_signature:
                await websocket.send_json(state_message())
                last_state_signature = state_signature

            if status["connected"] and status["streaming"]:
                for sequence, eeg, timestamps, first_index in receiver.chunks_after(last_sequence):
                    sample_count = eeg.shape[1]
                    elapsed = max(time.time() - receiver.started_at, 0.001)
                    await websocket.send_json({
                        "type": "data",
                        "eeg": eeg.tolist(),
                        "accel": [],
                        "timestamps": timestamps.tolist(),
                        "sample_indices": list(range(first_index, first_index + sample_count)),
                        "stats": {
                            "total_samples": status["total_samples"],
                            "dropped_packets": status["dropped_samples"],
                            "elapsed_seconds": elapsed,
                            "effective_rate": status["total_samples"] / elapsed,
                        },
                    })
                    last_sequence = sequence
            await asyncio.sleep(0.01)
    except WebSocketDisconnect:
        pass


def run(argv: list[str] | None = None) -> None:
    """Start the application from the canonical module entrypoint."""
    parser = argparse.ArgumentParser(description="LSL EEG Web Dashboard")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8081)
    parser.add_argument("--stream-name")
    parser.add_argument("--stream-type", default="EEG")
    args = parser.parse_args(argv)

    if args.stream_name is not None:
        receiver.stream_name = args.stream_name or None
    receiver.stream_type = args.stream_type

    print(f"LSL 脑电后端：http://{args.host}:{args.port}")
    print(
        f"目标数据流：name={receiver.stream_name or '任意'}，"
        f"type={receiver.stream_type}；未匹配时使用搜索到的第一个流"
    )
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    run()
