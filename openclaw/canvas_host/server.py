"""
Canvas Host Server.

WebSocket server for A2UI canvas rendering with live reload.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Set

import websockets
from websockets.server import WebSocketServerProtocol
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from openclaw.config.paths import resolve_state_dir

logger = logging.getLogger(__name__)


def _default_index_html() -> str:
    """Generate default canvas index.html template (TS alignment)
    
    Matches TypeScript openclaw/src/canvas-host/default-canvas.html exactly
    """
    return """<!doctype html>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>OpenClaw Canvas</title>
<style>
  html, body { height: 100%; margin: 0; background: #000; color: #fff; font: 16px/1.4 -apple-system, BlinkMacSystemFont, system-ui, Segoe UI, Roboto, Helvetica, Arial, sans-serif; }
  .wrap { min-height: 100%; display: grid; place-items: center; padding: 24px; }
  .card { width: min(720px, 100%); background: rgba(255,255,255,0.06); border: 1px solid rgba(255,255,255,0.10); border-radius: 16px; padding: 18px 18px 14px; }
  .title { display: flex; align-items: baseline; gap: 10px; }
  h1 { margin: 0; font-size: 22px; letter-spacing: 0.2px; }
  .sub { opacity: 0.75; font-size: 13px; }
  .row { display: flex; gap: 10px; flex-wrap: wrap; margin-top: 14px; }
  button { appearance: none; border: 1px solid rgba(255,255,255,0.14); background: rgba(255,255,255,0.10); color: #fff; padding: 10px 12px; border-radius: 12px; font-weight: 600; cursor: pointer; }
  button:active { transform: translateY(1px); }
  .ok { color: #24e08a; }
  .bad { color: #ff5c5c; }
  .log { margin-top: 14px; opacity: 0.85; font: 12px/1.4 ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace; white-space: pre-wrap; background: rgba(0,0,0,0.35); border: 1px solid rgba(255,255,255,0.08); padding: 10px; border-radius: 12px; }
</style>
<div class="wrap">
  <div class="card">
    <div class="title">
      <h1>OpenClaw Canvas</h1>
      <div class="sub">Interactive test page (auto-reload enabled)</div>
    </div>

    <div class="row">
      <button id="btn-hello">Hello</button>
      <button id="btn-time">Time</button>
      <button id="btn-photo">Photo</button>
      <button id="btn-dalek">Dalek</button>
    </div>

    <div id="status" class="sub" style="margin-top: 10px;"></div>
    <div id="log" class="log">Ready.</div>
  </div>
</div>
<script>
(() => {
  const logEl = document.getElementById("log");
  const statusEl = document.getElementById("status");
  const log = (msg) => { logEl.textContent = String(msg); };

  const hasIOS = () =>
    !!(
      window.webkit &&
      window.webkit.messageHandlers &&
      window.webkit.messageHandlers.openclawCanvasA2UIAction
    );
  const hasAndroid = () =>
    !!(
      (window.openclawCanvasA2UIAction &&
        typeof window.openclawCanvasA2UIAction.postMessage === "function")
    );
  const hasHelper = () => typeof window.openclawSendUserAction === "function";
  statusEl.innerHTML =
    "Bridge: " +
    (hasHelper() ? "<span class='ok'>ready</span>" : "<span class='bad'>missing</span>") +
    " · iOS=" + (hasIOS() ? "yes" : "no") +
    " · Android=" + (hasAndroid() ? "yes" : "no");

  const onStatus = (ev) => {
    const d = ev && ev.detail || {};
    log("Action status: id=" + (d.id || "?") + " ok=" + String(!!d.ok) + (d.error ? (" error=" + d.error) : ""));
  };
  window.addEventListener("openclaw:a2ui-action-status", onStatus);

  function send(name, sourceComponentId) {
    if (!hasHelper()) {
      log("No action bridge found. Ensure you're viewing this on an iOS/Android OpenClaw node canvas.");
      return;
    }
    const sendUserAction =
      typeof window.openclawSendUserAction === "function"
        ? window.openclawSendUserAction
        : undefined;
    const ok = sendUserAction({
      name,
      surfaceId: "main",
      sourceComponentId,
      context: { t: Date.now() },
    });
    log(ok ? ("Sent action: " + name) : ("Failed to send action: " + name));
  }

  document.getElementById("btn-hello").onclick = () => send("hello", "demo.hello");
  document.getElementById("btn-time").onclick = () => send("time", "demo.time");
  document.getElementById("btn-photo").onclick = () => send("photo", "demo.photo");
  document.getElementById("btn-dalek").onclick = () => send("dalek", "demo.dalek");
})();
</script>
"""


class CanvasFileWatcher(FileSystemEventHandler):
    """Watch canvas files for changes"""

    def __init__(self, server: "CanvasHostServer", loop: asyncio.AbstractEventLoop):
        self.server = server
        self._loop = loop

    def on_modified(self, event):
        """Handle file modification — called from watchdog thread, not the event loop."""
        if not event.is_directory:
            # asyncio.create_task() requires the running loop; use
            # call_soon_threadsafe() to schedule safely from a foreign thread.
            self._loop.call_soon_threadsafe(
                lambda: asyncio.ensure_future(self.server._broadcast_reload(), loop=self._loop)
            )


class CanvasHostServer:
    """
    Canvas host server for A2UI rendering.
    
    Paths:
    - /__openclaw__/a2ui/* - A2UI resources (scaffold)
    - /__openclaw__/canvas/* - User-editable HTML/CSS/JS
    - /__openclaw__/ws - Live reload WebSocket
    
    Features:
    - Static file serving from ~/.openclaw/canvas
    - File monitoring with watchdog
    - Live reload on file changes
    """
    
    def __init__(self, canvas_root: Path | None = None):
        self.canvas_root = canvas_root or (resolve_state_dir() / "canvas")
        self.canvas_root.mkdir(parents=True, exist_ok=True)
        
        # Ensure default index.html exists (TS alignment)
        self._prepare_canvas_root()
        
        self.host = "127.0.0.1"
        self.port = 0
        self._server: Any = None
        self._clients: Set[WebSocketServerProtocol] = set()
        self._observer: Observer | None = None
        self._running = False
        self._loop: asyncio.AbstractEventLoop | None = None
    
    def _prepare_canvas_root(self) -> None:
        """Prepare canvas root with default index.html if missing (TS alignment)"""
        index_path = self.canvas_root / "index.html"
        if not index_path.exists():
            try:
                index_path.write_text(_default_index_html(), encoding="utf-8")
                logger.info(f"Created default canvas index.html")
            except Exception as e:
                logger.warning(f"Failed to create default canvas index.html: {e}")
    
    async def start(self, host: str | None = None, port: int | None = None):
        """
        Start canvas host server.
        
        Args:
            host: Host to bind to
            port: Port to bind to (0 for auto-assignment)
        """
        if host:
            self.host = host
        if port is not None:
            self.port = port
        
        self._running = True
        self._loop = asyncio.get_running_loop()

        # Start WebSocket server for live reload
        self._server = await websockets.serve(
            self._handle_ws_connection,
            self.host,
            self.port
        )
        
        # Get actual port
        if self.port == 0:
            self.port = self._server.sockets[0].getsockname()[1]
        
        # Start file watcher
        self._start_file_watcher()
        
        logger.info(f"Canvas host started on {self.host}:{self.port}")
        logger.info(f"Canvas root: {self.canvas_root}")
    
    async def stop(self):
        """Stop canvas host server"""
        self._running = False
        
        # Stop file watcher
        if self._observer:
            self._observer.stop()
            self._observer.join()
        
        # Close server
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        
        # Close all clients
        for client in list(self._clients):
            await client.close()
        
        logger.info("Canvas host stopped")
    
    def _start_file_watcher(self):
        """Start watching canvas files"""
        handler = CanvasFileWatcher(self, self._loop)
        self._observer = Observer()
        self._observer.schedule(handler, str(self.canvas_root), recursive=True)
        self._observer.start()
        logger.info(f"Watching canvas files in {self.canvas_root}")
    
    async def _handle_ws_connection(self, websocket: WebSocketServerProtocol, path: str):
        """Handle WebSocket connection for live reload"""
        self._clients.add(websocket)
        logger.info(f"Canvas client connected ({len(self._clients)} total)")
        
        try:
            async for message in websocket:
                # Echo back or handle commands
                pass
        
        finally:
            self._clients.discard(websocket)
            logger.info(f"Canvas client disconnected ({len(self._clients)} total)")
    
    async def _broadcast_reload(self):
        """Broadcast reload message to all connected clients"""
        if not self._clients:
            return
        
        message = '{"type": "reload"}'
        
        for client in list(self._clients):
            try:
                await client.send(message)
            except Exception as e:
                logger.error(f"Error sending reload: {e}")
                self._clients.discard(client)
        
        logger.debug(f"Broadcasted reload to {len(self._clients)} clients")
    
    def get_canvas_file(self, path: str) -> bytes | None:
        """
        Get canvas file content.
        
        Args:
            path: Relative path within canvas root
            
        Returns:
            File content or None if not found
        """
        file_path = self.canvas_root / path.lstrip('/')
        
        if not file_path.exists() or not file_path.is_file():
            return None
        
        try:
            return file_path.read_bytes()
        except Exception as e:
            logger.error(f"Error reading canvas file {file_path}: {e}")
            return None


__all__ = [
    "CanvasHostServer",
]
