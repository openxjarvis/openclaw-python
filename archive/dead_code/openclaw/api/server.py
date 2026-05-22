"""
FastAPI REST API server for ClawdBot
"""
from __future__ import annotations


import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel

from ..agents.runtime import AgentRuntime
from ..agents.session import SessionManager
from ..channels.registry import ChannelRegistry
from ..monitoring import get_health_check, get_metrics
from .openai_compat import (
    router as openai_router,
)
from .openai_compat import (
    set_runtime as set_openai_runtime,
)
from .openai_compat import (
    set_session_manager as set_openai_session_manager,
)

logger = logging.getLogger(__name__)


# Global instances
_runtime: AgentRuntime | None = None
_session_manager: SessionManager | None = None
_channel_registry: ChannelRegistry | None = None


def set_runtime(runtime: AgentRuntime) -> None:
    """Set global runtime instance"""
    global _runtime
    _runtime = runtime


def set_session_manager(manager: SessionManager) -> None:
    """Set global session manager"""
    global _session_manager
    _session_manager = manager


def set_channel_registry(registry: ChannelRegistry) -> None:
    """Set global channel registry"""
    global _channel_registry
    _channel_registry = registry


def _init_openai_compat() -> None:
    """Initialize OpenAI-compatible API"""
    if _runtime:
        set_openai_runtime(_runtime)
    if _session_manager:
        set_openai_session_manager(_session_manager)


# Request/Response models
class AgentRequest(BaseModel):
    """Agent execution request"""

    session_id: str
    message: str
    model: str | None = None
    max_tokens: int | None = 4096


class AgentResponse(BaseModel):
    """Agent execution response"""

    session_id: str
    response: str
    metadata: dict[str, Any] = {}


class ChannelSendRequest(BaseModel):
    """Channel send message request"""

    channel_id: str
    target: str
    text: str
    reply_to: str | None = None


class ChannelSendResponse(BaseModel):
    """Channel send message response"""

    message_id: str
    channel_id: str
    success: bool


# Import authentication
from ..auth import setup_auth_middleware, verify_api_key


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager"""
    logger.info("Starting API server...")

    # Register health checks if components are available
    health = get_health_check()

    if _runtime:

        async def runtime_check():
            return True  # Runtime is initialized

        health.register("runtime", runtime_check, critical=True)

    if _session_manager:

        async def session_check():
            return True  # Session manager is initialized

        health.register("sessions", session_check, critical=False)

    if _channel_registry:

        async def channels_check():
            channels = _channel_registry.get_all()
            return len(channels) > 0  # At least one channel registered

        health.register("channels", channels_check, critical=False)

    # Initialize OpenAI-compatible API
    _init_openai_compat()

    yield

    logger.info("Shutting down API server...")


def create_app() -> FastAPI:
    """Create FastAPI application"""
    app = FastAPI(
        title="ClawdBot API",
        description="REST API for ClawdBot Agent Platform",
        version="0.3.2",
        lifespan=lifespan,
    )

    # Add CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # Configure appropriately in production
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Setup authentication middleware
    # Note: This adds API key validation and rate limiting to all endpoints
    # except those in skip_auth_paths
    setup_auth_middleware(app, enable_rate_limiting=True)

    # Add OpenAI-compatible router
    app.include_router(openai_router)

    # Health check endpoints
    @app.get("/health", tags=["Health"])
    async def health_check():
        """
        Comprehensive health check

        Returns overall system health and component status
        """
        health = get_health_check()
        result = await health.check_all()

        # Return appropriate status code
        if result.status == "unhealthy":
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content=result.model_dump()
            )

        return result

    @app.get("/health/live", tags=["Health"])
    async def liveness():
        """
        Liveness probe (Kubernetes-ready)

        Returns 200 if service is running
        """
        health = get_health_check()
        is_alive = await health.liveness()

        if not is_alive:
            raise HTTPException(status_code=503, detail="Not alive")

        return {"status": "ok", "timestamp": datetime.now(UTC).isoformat()}

    @app.get("/health/ready", tags=["Health"])
    async def readiness():
        """
        Readiness probe (Kubernetes-ready)

        Returns 200 if service is ready to handle requests
        """
        health = get_health_check()
        is_ready = await health.readiness()

        if not is_ready:
            raise HTTPException(status_code=503, detail="Not ready")

        return {"status": "ready", "timestamp": datetime.now(UTC).isoformat()}

    # Metrics endpoints
    @app.get("/metrics", tags=["Metrics"])
    async def get_metrics_json():
        """
        Get metrics in JSON format

        Returns all collected metrics
        """
        metrics = get_metrics()
        return metrics.to_dict()

    @app.get("/metrics/prometheus", response_class=PlainTextResponse, tags=["Metrics"])
    async def get_metrics_prometheus():
        """
        Get metrics in Prometheus format

        Returns metrics compatible with Prometheus scraping
        """
        metrics = get_metrics()
        return metrics.to_prometheus()

    # Agent endpoints
    @app.post("/agent/chat", response_model=AgentResponse, tags=["Agent"])
    async def agent_chat(request: AgentRequest, api_key: str = Depends(verify_api_key)):
        """
        Send message to agent

        Execute agent turn with given message and return response
        """
        if not _runtime or not _session_manager:
            raise HTTPException(status_code=503, detail="Agent runtime not initialized")

        try:
            # Get or create session
            session = _session_manager.get_session(request.session_id)

            # Create runtime with specified model if provided
            runtime = _runtime
            if request.model:
                runtime = AgentRuntime(model=request.model)

            # Execute agent turn
            response_text = ""
            async for event in runtime.run_turn(
                session, request.message, max_tokens=request.max_tokens
            ):
                if event.type == "assistant" and "delta" in event.data:
                    delta = event.data["delta"]
                    if "text" in delta:
                        response_text += delta["text"]

            return AgentResponse(
                session_id=request.session_id,
                response=response_text,
                metadata={"message_count": len(session.messages), "model": runtime.model},
            )

        except Exception as e:
            logger.error(f"Agent chat error: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/agent/sessions", tags=["Agent"])
    async def list_sessions(api_key: str = Depends(verify_api_key)):
        """
        List all sessions

        Returns list of active session IDs
        """
        if not _session_manager:
            raise HTTPException(status_code=503, detail="Session manager not initialized")

        sessions = _session_manager.list_sessions()
        return {"sessions": sessions, "count": len(sessions)}

    @app.get("/agent/sessions/{session_id}", tags=["Agent"])
    async def get_session(session_id: str, api_key: str = Depends(verify_api_key)):
        """
        Get session details

        Returns session information and message history
        """
        if not _session_manager:
            raise HTTPException(status_code=503, detail="Session manager not initialized")

        session = _session_manager.get_session(session_id)
        return session.to_dict()

    @app.delete("/agent/sessions/{session_id}", tags=["Agent"])
    async def delete_session(session_id: str, api_key: str = Depends(verify_api_key)):
        """
        Delete session

        Removes session and its history
        """
        if not _session_manager:
            raise HTTPException(status_code=503, detail="Session manager not initialized")

        _session_manager.delete_session(session_id)
        return {"status": "deleted", "session_id": session_id}

    # Channel endpoints
    @app.get("/channels", tags=["Channels"])
    async def list_channels(api_key: str = Depends(verify_api_key)):
        """
        List all channels

        Returns list of registered channels with status
        """
        if not _channel_registry:
            raise HTTPException(status_code=503, detail="Channel registry not initialized")

        channels = _channel_registry.get_all()
        return {"channels": [ch.to_dict() for ch in channels], "count": len(channels)}

    @app.get("/channels/{channel_id}", tags=["Channels"])
    async def get_channel(channel_id: str, api_key: str = Depends(verify_api_key)):
        """
        Get channel details

        Returns channel status and configuration
        """
        if not _channel_registry:
            raise HTTPException(status_code=503, detail="Channel registry not initialized")

        channel = _channel_registry.get(channel_id)
        if not channel:
            raise HTTPException(status_code=404, detail="Channel not found")

        return channel.to_dict()

    @app.post("/channels/{channel_id}/start", tags=["Channels"])
    async def start_channel(
        channel_id: str, config: dict[str, Any], api_key: str = Depends(verify_api_key)
    ):
        """
        Start a channel

        Initializes and starts the specified channel
        """
        if not _channel_registry:
            raise HTTPException(status_code=503, detail="Channel registry not initialized")

        channel = _channel_registry.get(channel_id)
        if not channel:
            raise HTTPException(status_code=404, detail="Channel not found")

        try:
            await channel.start(config)
            return {"status": "started", "channel_id": channel_id}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/channels/{channel_id}/stop", tags=["Channels"])
    async def stop_channel(channel_id: str, api_key: str = Depends(verify_api_key)):
        """
        Stop a channel

        Stops the specified channel
        """
        if not _channel_registry:
            raise HTTPException(status_code=503, detail="Channel registry not initialized")

        channel = _channel_registry.get(channel_id)
        if not channel:
            raise HTTPException(status_code=404, detail="Channel not found")

        try:
            await channel.stop()
            return {"status": "stopped", "channel_id": channel_id}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/channels/send", response_model=ChannelSendResponse, tags=["Channels"])
    async def send_message(request: ChannelSendRequest, api_key: str = Depends(verify_api_key)):
        """
        Send message through channel

        Sends a text message to the specified target
        """
        if not _channel_registry:
            raise HTTPException(status_code=503, detail="Channel registry not initialized")

        channel = _channel_registry.get(request.channel_id)
        if not channel:
            raise HTTPException(status_code=404, detail="Channel not found")

        try:
            message_id = await channel.send_text(request.target, request.text, request.reply_to)

            return ChannelSendResponse(
                message_id=message_id, channel_id=request.channel_id, success=True
            )
        except Exception as e:
            logger.error(f"Send message error: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))

    # Info endpoint
    @app.get("/", tags=["Info"])
    async def root():
        """
        API information

        Returns basic API info and available endpoints
        """
        return {
            "name": "ClawdBot API",
            "version": "0.3.2",
            "status": "running",
            "timestamp": datetime.now(UTC).isoformat(),
            "docs": "/docs",
            "health": "/health",
            "metrics": "/metrics",
        }

    return app


async def run_api_server(
    host: str = "0.0.0.0",
    port: int = 8000,
    runtime: AgentRuntime | None = None,
    session_manager: SessionManager | None = None,
    channel_registry: ChannelRegistry | None = None,
) -> None:
    """
    Run API server

    Args:
        host: Host to bind to
        port: Port to bind to
        runtime: Optional AgentRuntime instance
        session_manager: Optional SessionManager instance
        channel_registry: Optional ChannelRegistry instance
    """
    import uvicorn

    # Set global instances
    if runtime:
        set_runtime(runtime)
    if session_manager:
        set_session_manager(session_manager)
    if channel_registry:
        set_channel_registry(channel_registry)

    # Create app
    app = create_app()

    # Run server
    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()
