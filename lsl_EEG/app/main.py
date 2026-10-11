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

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
import uvicorn

from acquisition.lsl_receiver import LSLReceiver
from api.offline_api import build_offline_router
from app.config import BASE_DIR, UPLOAD_DIR, WEB_DIR, stream_name, stream_type


receiver = LSLReceiver(stream_name=stream_name(), stream_type=stream_type())
app = FastAPI(title="LSL EEG Dashboard")
app.include_router(build_offline_router(UPLOAD_DIR))


@app.get("/")
async def index():
    return FileResponse(WEB_DIR / "frontend.html")


@app.get("/offline")
async def offline_index():
    return FileResponse(WEB_DIR / "offline-detection.html")


@app.get("/health")
async def health():
    return {"status": "ok", "source": "lsl", **receiver.status()}


@app.get("/overshoot_key")
async def overshoot_key():
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


async def handle_command(websocket: WebSocket, message: dict) -> None:
    command = message.get("command")
    if command == "get_state":
        await websocket.send_json(state_message())
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
        await websocket.send_json({"type": "start_result", "success": connected, "error": "尚未连接 LSL 数据流" if not connected else ""})
    elif command == "stop":
        receiver.stop_streaming()
        await websocket.send_json({"type": "stop_result", "success": True, "total_samples": receiver.status()["total_samples"]})
    elif command == "disconnect":
        receiver.disconnect()
        await websocket.send_json({"type": "disconnect_result", "success": True})
    elif command == "arduino_list_ports":
        await websocket.send_json({"type": "arduino_ports", "ports": []})
    elif command in {"wheelchair_enable", "wheelchair_disable"}:
        await websocket.send_json({"type": "wheelchair_status", "enabled": False})


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    last_sequence = 0
    last_state_signature = None
    try:
        while True:
            try:
                message = await asyncio.wait_for(websocket.receive_json(), timeout=0.02)
                await handle_command(websocket, message)
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
