"""Wizard RPC methods for gateway - matches TypeScript implementation"""

import logging
from typing import Any, Optional
from ..wizard.session import WizardSession
from ..config.loader import save_config
from ..config.schema import OpenClawConfig, ModelsConfig

logger = logging.getLogger(__name__)


class WizardRPCHandler:
    """Handles wizard-related RPC methods"""
    
    def __init__(self, gateway):
        self.gateway = gateway
        self.sessions: dict[str, WizardSession] = {}
        self.active_session_id: Optional[str] = None
    
    async def wizard_start(self, params: dict) -> dict:
        """Start a new wizard session
        
        RPC method: wizard.start
        Params:
            - mode: "local" | "remote" | "quickstart" | "advanced"
            - workspace: Optional workspace directory
        
        Returns:
            - sessionId: UUID of the session
            - done: Whether wizard is complete
            - step: Current step details
            - error: Error message if any
        """
        # Only allow one running wizard at a time
        if self.active_session_id:
            return {
                "error": "A wizard session is already running",
                "activeSessionId": self.active_session_id
            }
        
        mode = params.get("mode", "quickstart")
        workspace = params.get("workspace")
        
        try:
            session = WizardSession(mode=mode, workspace=workspace)
            self.sessions[session.session_id] = session
            self.active_session_id = session.session_id
            
            logger.info(f"Started wizard session {session.session_id} in {mode} mode")
            
            return session.to_dict()
        
        except Exception as e:
            logger.error(f"Failed to start wizard: {e}", exc_info=True)
            return {"error": str(e)}
    
    async def wizard_next(self, params: dict) -> dict:
        """Advance wizard with optional answer
        
        RPC method: wizard.next
        Params:
            - sessionId: UUID of the session
            - answer: Optional answer to current step
                - stepId: ID of the step being answered
                - value: Answer value
        
        Returns:
            - done: Whether wizard is complete
            - step: Next step details
            - status: Status message
            - error: Error message if any
        """
        session_id = params.get("sessionId")
        answer_data = params.get("answer")
        
        if not session_id:
            return {"error": "sessionId is required"}
        
        session = self.sessions.get(session_id)
        if not session:
            return {"error": "Session not found"}
        
        try:
            # Process answer if provided
            if answer_data:
                step_id = answer_data.get("stepId")
                value = answer_data.get("value")
                
                current_step = session.get_current_step()
                if current_step and current_step.id == step_id:
                    # Validate answer based on step type
                    if not self._validate_answer(current_step, value):
                        return {
                            "error": "Invalid answer for step",
                            "step": current_step.to_dict()
                        }
                    
                    # Store answer and advance
                    session.advance(value)
                else:
                    return {"error": "Step ID mismatch"}
            else:
                # Just advance without answer
                session.advance()
            
            # If wizard is done, apply configuration
            if session.done:
                await self._apply_wizard_config(session)
                self.active_session_id = None
                logger.info(f"Wizard session {session_id} completed")
                
                return {
                    "done": True,
                    "status": "Configuration applied successfully"
                }
            
            return session.to_dict()
        
        except Exception as e:
            logger.error(f"Error advancing wizard: {e}", exc_info=True)
            session.error = str(e)
            return {"error": str(e)}
    
    async def wizard_cancel(self, params: dict) -> dict:
        """Cancel running wizard
        
        RPC method: wizard.cancel
        Params:
            - sessionId: UUID of the session
        
        Returns:
            - status: Cancellation status
            - error: Error message if any
        """
        session_id = params.get("sessionId")
        
        if not session_id:
            return {"error": "sessionId is required"}
        
        session = self.sessions.get(session_id)
        if not session:
            return {"error": "Session not found"}
        
        try:
            # Remove session
            del self.sessions[session_id]
            if self.active_session_id == session_id:
                self.active_session_id = None
            
            logger.info(f"Wizard session {session_id} cancelled")
            
            return {"status": "cancelled"}
        
        except Exception as e:
            logger.error(f"Error cancelling wizard: {e}", exc_info=True)
            return {"error": str(e)}
    
    async def wizard_status(self, params: dict) -> dict:
        """Get wizard session status
        
        RPC method: wizard.status
        Params:
            - sessionId: UUID of the session
        
        Returns:
            - status: Session details
            - error: Error message if any
        """
        session_id = params.get("sessionId")
        
        if not session_id:
            return {"error": "sessionId is required"}
        
        session = self.sessions.get(session_id)
        if not session:
            return {"error": "Session not found"}
        
        return session.to_dict()
    
    def _validate_answer(self, step, value) -> bool:
        """Validate answer for a step"""
        from ..wizard.session import WizardStepType
        
        if step.type == WizardStepType.CONFIRM:
            return isinstance(value, bool)
        
        elif step.type == WizardStepType.SELECT:
            if not step.options:
                return True
            valid_ids = [opt["id"] for opt in step.options]
            return value in valid_ids
        
        elif step.type == WizardStepType.MULTISELECT:
            if not step.options or not isinstance(value, list):
                return False
            valid_ids = [opt["id"] for opt in step.options]
            return all(v in valid_ids for v in value)
        
        elif step.type == WizardStepType.TEXT:
            return isinstance(value, str)
        
        # NOTE, PROGRESS, ACTION don't require validation
        return True
    
    async def _apply_wizard_config(self, session: WizardSession):
        """Apply wizard answers to configuration"""
        try:
            # Create or load config
            config = OpenClawConfig()
            
            # Apply answers
            agent_name = session.get_answer("agent_name") or "OpenClaw"
            provider = session.get_answer("provider_select") or "google"
            gateway_port = int(session.get_answer("gateway_port") or 18789)
            
            # Set agent defaults
            if not config.agents.agents:
                config.agents.agents = []
            
            # Model mapping
            model_map = {
                "google": "google/gemini-3-pro-preview",
                "anthropic": "anthropic/claude-opus-4-5-20250514",
                "openai": "openai/gpt-4o",
                "groq": "groq/llama-3-70b-8192",
                "ollama": "ollama/llama3"
            }
            
            model = model_map.get(provider, "google/gemini-3-pro-preview")
            config.agents.defaults.model = model

            # Write models.providers block for Ollama
            if provider == "ollama":
                ollama_base_url = session.get_answer("ollama_base_url") or "http://127.0.0.1:11434"
                if not config.models:
                    config.models = ModelsConfig()
                config.models.providers = config.models.providers or {}
                config.models.providers["ollama"] = {
                    "baseUrl": ollama_base_url,
                    "api": "ollama",
                    "apiKey": "ollama-local",
                    "models": [],  # auto-discovered at runtime by models_config.py
                }

            # Gateway configuration
            config.gateway.port = gateway_port
            config.gateway.mode = session.mode or "local"
            
            # Channel configuration
            telegram_enabled = session.get_answer("telegram_enable")
            if telegram_enabled:
                from ..config.schema import ChannelConfig
                if not config.channels.telegram:
                    config.channels.telegram = ChannelConfig(enabled=True)
            
            # Save configuration
            save_config(config)
            logger.info("Wizard configuration applied successfully")
        
        except Exception as e:
            logger.error(f"Failed to apply wizard config: {e}", exc_info=True)
            raise
