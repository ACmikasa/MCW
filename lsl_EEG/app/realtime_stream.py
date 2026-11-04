"""Small protocol helpers for the realtime display stream."""

from __future__ import annotations

import json


def sse_event(event: str, data: dict, event_id: int | None = None) -> str:
    """Encode one compact SSE event without retaining a per-client queue."""
    fields = []
    if event_id is not None:
        fields.append(f"id: {event_id}")
    fields.append(f"event: {event}")
    fields.append(f"data: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}")
    return "\n".join(fields) + "\n\n"
