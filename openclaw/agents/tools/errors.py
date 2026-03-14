"""Tool error types matching TypeScript common.ts

Aligns with TS openclaw/src/agents/tools/common.ts lines 24-42
"""


class ToolInputError(Exception):
    """
    Tool parameter validation error (HTTP 400).
    
    Matches TS ToolInputError from common.ts line 26.
    """
    status: int = 400
    
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message
        self.name = "ToolInputError"


class ToolAuthorizationError(ToolInputError):
    """
    Tool authorization error (HTTP 403).
    
    Matches TS ToolAuthorizationError from common.ts line 35.
    """
    status: int = 403
    
    def __init__(self, message: str):
        super().__init__(message)
        self.name = "ToolAuthorizationError"


# Constant matching TS line 24
OWNER_ONLY_TOOL_ERROR = "Tool restricted to owner senders."
