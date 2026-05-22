"""
Enhanced WebSocket streaming for real-time agent communication
"""
from __future__ import annotations


import asyncio
import json
import logging
from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)


class MessageType(str, Enum):
    """WebSocket message types"""

    # Client -> Server
    PING = "ping"
    REQUEST = "request"
    CANCEL = "cancel"

    # Server -> Client
    PONG = "pong"
    RESPONSE = "response"
    ERROR = "error"
    STREAM_START = "stream_start"
    STREAM_DATA = "stream_data"
    STREAM_END = "stream_end"
    HEARTBEAT = "heartbeat"


class ConnectionState(str, Enum):
    """WebSocket connection state"""

    CONNECTING = "connecting"
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    ERROR = "error"


class WebSocketMessage:
    """WebSocket message wrapper"""

    def __init__(
        self,
        type: MessageType,
        data: Any = None,
        request_id: str | None = None,
        error: str | None = None,
    ):
        self.type = type
        self.data = data
        self.request_id = request_id or str(uuid4())
        self.error = error
        self.timestamp = datetime.now(UTC).isoformat()

    def to_dict(self) -> dict:
        """Convert to dictionary"""
        return {
            "type": self.type.value,
            "data": self.data,
            "request_id": self.request_id,
            "error": self.error,
            "timestamp": self.timestamp,
        }

    def to_json(self) -> str:
        """Convert to JSON string"""
        return json.dumps(self.to_dict())

    @classmethod
    def from_dict(cls, data: dict) -> "WebSocketMessage":
        """Create from dictionary"""
        return cls(
            type=MessageType(data.get("type")),
            data=data.get("data"),
            request_id=data.get("request_id"),
            error=data.get("error"),
        )

    @classmethod
    def from_json(cls, json_str: str) -> "WebSocketMessage":
        """Create from JSON string"""
        return cls.from_dict(json.loads(json_str))


class WebSocketConnection:
    """
    Enhanced WebSocket connection with:
    - Heartbeat/keepalive
    - Message queuing
    - Error recovery
    - Request tracking
    """

    def __init__(
        self, websocket: WebSocket, connection_id: str | None = None, heartbeat_interval: int = 30
    ):
        """
        Initialize WebSocket connection

        Args:
            websocket: FastAPI WebSocket instance
            connection_id: Optional connection ID
            heartbeat_interval: Heartbeat interval in seconds
        """
        self.websocket = websocket
        self.connection_id = connection_id or str(uuid4())
        self.heartbeat_interval = heartbeat_interval

        self.state = ConnectionState.CONNECTING
        self.connected_at: datetime | None = None
        self.last_activity: datetime | None = None

        self._send_queue: asyncio.Queue = asyncio.Queue()
        self._active_requests: dict[str, Any] = {}
        self._heartbeat_task: asyncio.Task | None = None
        self._send_task: asyncio.Task | None = None

    async def accept(self) -> None:
        """Accept WebSocket connection"""
        await self.websocket.accept()
        self.state = ConnectionState.CONNECTED
        self.connected_at = datetime.now(UTC)
        self.last_activity = self.connected_at

        # Start background tasks
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        self._send_task = asyncio.create_task(self._send_loop())

        logger.info(f"WebSocket connection {self.connection_id} accepted")

    async def close(self, code: int = 1000, reason: str = "Normal closure") -> None:
        """Close WebSocket connection"""
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
        if self._send_task:
            self._send_task.cancel()

        try:
            await self.websocket.close(code=code, reason=reason)
        except Exception as e:
            logger.warning(f"Error closing WebSocket: {e}")

        self.state = ConnectionState.DISCONNECTED
        logger.info(f"WebSocket connection {self.connection_id} closed")

    async def send_message(self, message: WebSocketMessage) -> None:
        """
        Send message to client

        Args:
            message: WebSocketMessage to send
        """
        await self._send_queue.put(message)
        self.last_activity = datetime.now(UTC)

    async def receive_message(self) -> WebSocketMessage:
        """
        Receive message from client

        Returns:
            WebSocketMessage

        Raises:
            WebSocketDisconnect: If connection closed
        """
        try:
            data = await self.websocket.receive_json()
            self.last_activity = datetime.now(UTC)
            return WebSocketMessage.from_dict(data)
        except WebSocketDisconnect:
            logger.info(f"WebSocket {self.connection_id} disconnected")
            self.state = ConnectionState.DISCONNECTED
            raise
        except Exception as e:
            logger.error(f"Error receiving message: {e}")
            self.state = ConnectionState.ERROR
            raise

    async def stream_response(self, request_id: str, data_iterator: Any) -> None:
        """
        Stream response data

        Args:
            request_id: Request ID
            data_iterator: Async iterator of data chunks
        """
        # Send stream start
        await self.send_message(
            WebSocketMessage(type=MessageType.STREAM_START, request_id=request_id)
        )

        try:
            # Stream data chunks
            async for chunk in data_iterator:
                await self.send_message(
                    WebSocketMessage(
                        type=MessageType.STREAM_DATA, data=chunk, request_id=request_id
                    )
                )

            # Send stream end
            await self.send_message(
                WebSocketMessage(type=MessageType.STREAM_END, request_id=request_id)
            )

        except Exception as e:
            logger.error(f"Error streaming response: {e}")
            await self.send_message(
                WebSocketMessage(type=MessageType.ERROR, request_id=request_id, error=str(e))
            )

    async def _send_loop(self) -> None:
        """Background task to send queued messages"""
        try:
            while self.state == ConnectionState.CONNECTED:
                message = await self._send_queue.get()

                try:
                    await self.websocket.send_json(message.to_dict())
                except Exception as e:
                    logger.error(f"Error sending message: {e}")
                    self.state = ConnectionState.ERROR
                    break

        except asyncio.CancelledError:
            logger.debug("Send loop cancelled")

    async def _heartbeat_loop(self) -> None:
        """Background task to send heartbeats"""
        try:
            while self.state == ConnectionState.CONNECTED:
                await asyncio.sleep(self.heartbeat_interval)

                await self.send_message(WebSocketMessage(type=MessageType.HEARTBEAT))

        except asyncio.CancelledError:
            logger.debug("Heartbeat loop cancelled")

    def is_alive(self) -> bool:
        """Check if connection is alive"""
        return self.state == ConnectionState.CONNECTED

    def get_uptime_seconds(self) -> float:
        """Get connection uptime in seconds"""
        if not self.connected_at:
            return 0.0

        now = datetime.now(UTC)
        delta = now - self.connected_at
        return delta.total_seconds()


class WebSocketManager:
    """
    Manage multiple WebSocket connections

    Features:
    - Connection pooling
    - Broadcast messaging
    - Connection monitoring
    - Automatic cleanup
    """

    def __init__(self):
        """Initialize WebSocket manager"""
        self.connections: dict[str, WebSocketConnection] = {}

    def add_connection(self, connection: WebSocketConnection) -> None:
        """
        Add a connection

        Args:
            connection: WebSocketConnection instance
        """
        self.connections[connection.connection_id] = connection
        logger.info(f"Added connection {connection.connection_id}, total: {len(self.connections)}")

    def remove_connection(self, connection_id: str) -> None:
        """
        Remove a connection

        Args:
            connection_id: Connection ID
        """
        if connection_id in self.connections:
            del self.connections[connection_id]
            logger.info(f"Removed connection {connection_id}, total: {len(self.connections)}")

    def get_connection(self, connection_id: str) -> WebSocketConnection | None:
        """
        Get a connection by ID

        Args:
            connection_id: Connection ID

        Returns:
            WebSocketConnection or None
        """
        return self.connections.get(connection_id)

    async def broadcast(self, message: WebSocketMessage, exclude: list[str] | None = None) -> None:
        """
        Broadcast message to all connections

        Args:
            message: Message to broadcast
            exclude: List of connection IDs to exclude
        """
        exclude = exclude or []

        for conn_id, conn in list(self.connections.items()):
            if conn_id in exclude:
                continue

            if conn.is_alive():
                try:
                    await conn.send_message(message)
                except Exception as e:
                    logger.error(f"Error broadcasting to {conn_id}: {e}")

    async def cleanup_inactive(self, timeout_seconds: int = 300) -> int:
        """
        Clean up inactive connections

        Args:
            timeout_seconds: Inactivity timeout in seconds

        Returns:
            Number of connections cleaned up
        """
        now = datetime.now(UTC)
        to_remove = []

        for conn_id, conn in self.connections.items():
            if not conn.is_alive():
                to_remove.append(conn_id)
            elif conn.last_activity:
                inactive = (now - conn.last_activity).total_seconds()
                if inactive > timeout_seconds:
                    to_remove.append(conn_id)
                    await conn.close(code=1000, reason="Inactive timeout")

        for conn_id in to_remove:
            self.remove_connection(conn_id)

        if to_remove:
            logger.info(f"Cleaned up {len(to_remove)} inactive connections")

        return len(to_remove)

    def get_stats(self) -> dict[str, Any]:
        """
        Get connection statistics

        Returns:
            Statistics dictionary
        """
        active = sum(1 for conn in self.connections.values() if conn.is_alive())

        return {
            "total_connections": len(self.connections),
            "active_connections": active,
            "inactive_connections": len(self.connections) - active,
        }
