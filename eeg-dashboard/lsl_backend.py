#!/usr/bin/env python3
"""Receive an EEG stream through LSL and relay it to frontend.html."""

from __future__ import annotations

import argparse
import asyncio
import os
import threading
import time
from collections import deque
from pathlib import Path
from typing import Optional

import numpy as np
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pylsl import StreamInlet, resolve_streams


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_CHANNEL_LABELS = [
    "Fp1", "Fp2", "F4", "Fz", "F3", "T7", "C3", "Cz",
    "C4", "T8", "P4", "Pz", "P3", "O1", "Oz", "O2",
]
MAX_CHANNELS = 16
MAX_SAMPLES_PER_PULL = 250
PULL_TIMEOUT_SECONDS = 0.05
BACKEND_WINDOW_SECONDS = 2.0
CLIENT_BACKLOG_SECONDS = 0.25
LSL_RESOLVE_TIMEOUT_SECONDS = 0.25
DISCOVERY_INTERVAL_SECONDS = 0.25


class LSLReceiver:
    """Discover one LSL EEG stream and keep its chunks in a bounded buffer."""

    def __init__(self, stream_name: Optional[str] = None, stream_type: str = "EEG"):
        self.stream_name = stream_name
        self.stream_type = stream_type
        self.inlet: Optional[StreamInlet] = None
        self.connected = False
        self.streaming = False
        self.error = ""
        self.name = ""
        self.source_id = ""
        self.sample_rate = 500.0
        self.channel_count = MAX_CHANNELS
        self.channel_labels = DEFAULT_CHANNEL_LABELS.copy()
        self.total_samples = 0
        self.dropped_samples = 0
        self.started_at = time.time()

        # Each item is (sequence, channel-major data, timestamps, first sample index).
        self._chunks = deque()
        self._buffered_samples = 0
        self._sequence = 0
        self._lock = threading.Lock()
        self._lifecycle_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Start discovery only after an explicit Connect command."""
        with self._lifecycle_lock:
            if self._thread and self._thread.is_alive() and not self._stop_event.is_set():
                return

            # Give each worker its own event so an old worker cannot resume
            # accidentally when a new connection attempt starts.
            stop_event = threading.Event()
            self._stop_event = stop_event
            self.streaming = False
            self.error = ""
            self._thread = threading.Thread(
                target=self._run,
                args=(stop_event,),
                name="lsl-eeg-receiver",
                daemon=True,
            )
            self._thread.start()

    def stop_streaming(self) -> None:
        self.streaming = False

    def resume_streaming(self) -> None:
        inlet = self.inlet
        if not self.connected or inlet is None:
            return
        # Drop samples accumulated while paused; resume from live data.
        try:
            inlet.flush()
        except Exception:
            pass
        with self._lock:
            self._chunks.clear()
            self._buffered_samples = 0
            self.streaming = True
            self.started_at = time.time()

    def disconnect(self) -> None:
        with self._lifecycle_lock:
            stop_event = self._stop_event
            worker = self._thread
            stop_event.set()

        with self._lock:
            self.streaming = False
            inlet = self.inlet
            self.inlet = None
            self.connected = False
            self.name = ""
            self.source_id = ""
            self.error = ""
            self._chunks.clear()
            self._buffered_samples = 0

        if inlet is not None:
            try:
                inlet.close_stream()
            except Exception:
                pass

        if worker and worker.is_alive() and worker is not threading.current_thread():
            worker.join(timeout=1.0)

        with self._lifecycle_lock:
            if self._thread is worker and worker and not worker.is_alive():
                self._thread = None

    def status(self) -> dict:
        with self._lock:
            return {
                "connected": self.connected,
                "streaming": self.connected and self.streaming,
                "name": self.name,
                "source_id": self.source_id,
                "sampling_rate": self.sample_rate,
                "num_channels": self.channel_count,
                "channel_labels": self.channel_labels,
                "total_samples": self.total_samples,
                "dropped_samples": self.dropped_samples,
                "buffered_samples": self._buffered_samples,
                "sequence": self._sequence,
                "error": self.error,
            }

    def chunks_after(self, sequence: int):
        with self._lock:
            unread = [item for item in self._chunks if item[0] > sequence]
            if not unread:
                return []

            # If a browser falls behind, prioritize the newest quarter-second
            # instead of replaying stale EEG and increasing visible latency.
            sample_budget = max(1, int(self.sample_rate * CLIENT_BACKLOG_SECONDS))
            selected = []
            selected_samples = 0
            for item in reversed(unread):
                selected.append(item)
                selected_samples += item[1].shape[1]
                if selected_samples >= sample_budget:
                    break
            return list(reversed(selected))

    def _select_stream(self):
        streams = resolve_streams(wait_time=LSL_RESOLVE_TIMEOUT_SECONDS)
        if not streams:
            return None

        if self.stream_name:
            for stream in streams:
                if stream.name() == self.stream_name:
                    return stream

        for stream in streams:
            if stream.type().lower() == self.stream_type.lower():
                return stream

        # Keep the behavior of the supplied receiver: use the first stream as fallback.
        return streams[0]

    @staticmethod
    def _read_channel_labels(info, channel_count: int) -> list[str]:
        labels: list[str] = []
        try:
            channel = info.desc().child("channels").child("channel")
            for index in range(channel_count):
                label = channel.child_value("label")
                labels.append(label or f"通道{index + 1}")
                channel = channel.next_sibling()
        except Exception:
            labels = []

        if not labels or all(label.startswith("通道") for label in labels):
            labels = DEFAULT_CHANNEL_LABELS[:channel_count]
            labels.extend(f"通道{i + 1}" for i in range(len(labels), channel_count))
        return labels

    def _connect(self, stop_event: threading.Event) -> bool:
        info = self._select_stream()
        if info is None:
            self.error = "未发现 LSL 数据流，正在继续搜索"
            return False

        if stop_event.is_set():
            return False

        inlet = StreamInlet(info, max_buflen=60, recover=True)
        if stop_event.is_set():
            inlet.close_stream()
            return False
        channel_count = min(int(info.channel_count()), MAX_CHANNELS)
        nominal_rate = float(info.nominal_srate())

        with self._lock:
            self.inlet = inlet
            self.name = info.name()
            self.source_id = info.source_id()
            self.sample_rate = nominal_rate if nominal_rate > 0 else 500.0
            self.channel_count = channel_count
            self.channel_labels = self._read_channel_labels(info, channel_count)
            self.connected = True
            self.error = ""
            self.total_samples = 0
            self.dropped_samples = 0
            self.started_at = time.time()
            self._chunks.clear()
            self._buffered_samples = 0
            self._sequence = 0

        print(
            f"[LSL] 已连接：{self.name} | {self.channel_count} 通道 | "
            f"{self.sample_rate:g} Hz | source_id={self.source_id or '-'}"
        )
        return True

    def _run(self, stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            if self.inlet is None:
                try:
                    if not self._connect(stop_event):
                        stop_event.wait(DISCOVERY_INTERVAL_SECONDS)
                        continue
                except Exception as exc:
                    if stop_event.is_set():
                        break
                    with self._lock:
                        self.connected = False
                        self.error = f"LSL 连接失败：{exc}"
                    stop_event.wait(DISCOVERY_INTERVAL_SECONDS)
                    continue

            if not self.streaming:
                stop_event.wait(0.05)
                continue

            try:
                raw_samples, timestamps = self.inlet.pull_chunk(
                    timeout=PULL_TIMEOUT_SECONDS,
                    max_samples=MAX_SAMPLES_PER_PULL,
                )
                if stop_event.is_set():
                    break
                if not raw_samples:
                    continue

                # LSL gives (n_samples, n_channels); the web client expects
                # (n_channels, n_samples), matching the user's collect_data().
                sample_major = np.asarray(raw_samples, dtype=np.float64)
                if sample_major.ndim != 2:
                    continue
                sample_major = sample_major[:, : self.channel_count]
                channel_major = sample_major.T
                timestamps_array = np.asarray(timestamps, dtype=np.float64)

                if timestamps_array.size != sample_major.shape[0]:
                    timestamps_array = np.arange(sample_major.shape[0], dtype=np.float64)
                    timestamps_array = timestamps_array / self.sample_rate + time.time()

                with self._lock:
                    first_index = self.total_samples
                    self.total_samples += sample_major.shape[0]
                    self._sequence += 1
                    self._chunks.append(
                        (self._sequence, channel_major, timestamps_array, first_index)
                    )
                    self._buffered_samples += sample_major.shape[0]

                    # Sliding window: retain only the latest few seconds. This
                    # buffer exists for display fan-out, not long-term storage.
                    window_samples = max(1, int(self.sample_rate * BACKEND_WINDOW_SECONDS))
                    while self._chunks and self._buffered_samples > window_samples:
                        oldest = self._chunks.popleft()
                        self._buffered_samples -= oldest[1].shape[1]
            except Exception as exc:
                if stop_event.is_set():
                    break
                with self._lock:
                    self.connected = False
                    self.error = f"LSL 接收失败：{exc}"
                    self.inlet = None
                stop_event.wait(DISCOVERY_INTERVAL_SECONDS)


receiver = LSLReceiver(
    stream_name=os.environ.get("LSL_STREAM_NAME") or None,
    stream_type=os.environ.get("LSL_STREAM_TYPE", "EEG"),
)
app = FastAPI(title="LSL EEG Dashboard")


@app.get("/")
async def index():
    return FileResponse(BASE_DIR / "frontend.html")


@app.get("/health")
async def health():
    status = receiver.status()
    return {"status": "ok", "source": "lsl", **status}


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
        # LSL discovery runs in a worker thread. Wait briefly so one click
        # produces a real connection result instead of an immediate false error.
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
        await websocket.send_json({
            "type": "start_result",
            "success": connected,
            "error": "尚未连接 LSL 数据流" if not connected else "",
        })
    elif command == "stop":
        receiver.stop_streaming()
        await websocket.send_json({
            "type": "stop_result",
            "success": True,
            "total_samples": receiver.status()["total_samples"],
        })
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
            # A new inlet resets its sequence to zero. Reset this client's
            # cursor too, otherwise it waits for the new stream to catch up to
            # the old sequence number before displaying anything.
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
                chunks = receiver.chunks_after(last_sequence)
                for sequence, eeg, timestamps, first_index in chunks:
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


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="LSL EEG Web Dashboard")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8081)
    parser.add_argument("--stream-name", default=os.environ.get("LSL_STREAM_NAME"))
    parser.add_argument("--stream-type", default=os.environ.get("LSL_STREAM_TYPE", "EEG"))
    args = parser.parse_args()

    receiver.stream_name = args.stream_name or None
    receiver.stream_type = args.stream_type

    print(f"LSL 脑电后端：http://{args.host}:{args.port}")
    print(
        f"目标数据流：name={receiver.stream_name or '任意'}，"
        f"type={receiver.stream_type}；未匹配时使用搜索到的第一个流"
    )
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
