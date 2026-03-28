"""Sandbox constants

Matches TypeScript openclaw/src/agents/sandbox/constants.ts
"""

# Default Docker image for sandbox
DEFAULT_SANDBOX_IMAGE = "openclaw-sandbox:bookworm-slim"

# Mount path for agent workspace inside container
SANDBOX_AGENT_WORKSPACE_MOUNT = "/agent"

# Hot container reuse window (5 minutes)
HOT_CONTAINER_WINDOW_MS = 5 * 60 * 1000
