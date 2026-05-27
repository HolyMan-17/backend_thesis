import asyncio
import json
from typing import Dict, Set
from fastapi import WebSocket


class ConnectionManager:
    def __init__(self):
        self._connections: Dict[WebSocket, Set[str]] = {}
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket, allowed_macs: Set[str]):
        async with self._lock:
            self._connections[websocket] = allowed_macs

    async def disconnect(self, websocket: WebSocket):
        async with self._lock:
            self._connections.pop(websocket, None)

    async def broadcast_telemetry(self, mac: str, data: dict):
        async with self._lock:
            connections = dict(self._connections)

        message = json.dumps({"type": "telemetria", "mac": mac, "data": data})
        stale = []

        for ws, allowed_macs in connections.items():
            if mac not in allowed_macs:
                continue
            try:
                await ws.send_text(message)
            except Exception:
                stale.append(ws)

        for ws in stale:
            async with self._lock:
                self._connections.pop(ws, None)

    async def broadcast_event(self, mac: str, event_type: str, data: dict):
        async with self._lock:
            connections = dict(self._connections)

        message = json.dumps({"type": event_type, "mac": mac, "data": data})
        stale = []

        for ws, allowed_macs in connections.items():
            if mac not in allowed_macs:
                continue
            try:
                await ws.send_text(message)
            except Exception:
                stale.append(ws)

        for ws in stale:
            async with self._lock:
                self._connections.pop(ws, None)


ws_manager = ConnectionManager()