"""
Image analysis tool with vision models

Matches TypeScript src/agents/tools/image-tool.ts

Features:
- Multiple model support (Claude, GPT-4 Vision, MiniMax VL)
- Model fallback mechanism
- Image optimization
- HEIC conversion
- Data URL support
- Sandbox path validation
"""
from __future__ import annotations

import base64
import logging
import os
from pathlib import Path
from typing import Any

from openclaw.media.image_ops import ImageProcessor, convert_heic_to_jpeg
from openclaw.media.loader import MediaLoader, load_media
from openclaw.media.mime import MediaKind, extension_for_mime, is_heic_file, is_heic_mime

from .base import AgentTool, ToolResult

logger = logging.getLogger(__name__)


class ImageTool(AgentTool):
    """
    Analyze images using vision models.
    
    Matches TypeScript createImageTool functionality with:
    - Multi-model support (Claude, GPT-4, MiniMax)
    - Automatic model fallback
    - Image optimization
    - HEIC conversion
    - Sandbox path validation
    """

    def __init__(
        self,
        workspace_root: Path | None = None,
        model_has_vision: bool = False,
        max_bytes_mb: float = 20.0,
        optimize_images: bool = True,
    ):
        """
        Initialize image tool.
        
        Args:
            workspace_root: Workspace root for sandboxed access
            model_has_vision: Whether primary model has vision (affects description)
            max_bytes_mb: Maximum image size in MB
            optimize_images: Whether to optimize images
        """
        super().__init__()
        self.name = "image"
        self.workspace_root = workspace_root
        self.max_bytes = int(max_bytes_mb * 1024 * 1024) if max_bytes_mb else None
        self.optimize_images = optimize_images
        
        # Adjust description based on model vision capability (matches TS lines 329-331)
        if model_has_vision:
            self.description = (
                "Analyze an image with a vision model. Only use this tool when the image was NOT "
                "already provided in the user's message. Images mentioned in the prompt are "
                "automatically visible to you."
            )
        else:
            self.description = (
                "Analyze an image with a vision model. Provide a prompt and image path or URL."
            )

    def get_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "image": {"type": "string", "description": "Image file path, URL, or data URL"},
                "prompt": {
                    "type": "string",
                    "description": "Question or instruction about the image",
                },
                "model": {
                    "type": "string",
                    "description": "Model to use (claude-3-5-sonnet, gpt-4-vision)",
                    "default": "claude-3-5-sonnet",
                },
            },
            "required": ["image", "prompt"],
        }

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        """
        Analyze image with vision model.
        
        Matches TS image-tool.ts execute logic with:
        - Model fallback support
        - HEIC conversion
        - Image optimization
        - Sandbox path validation
        """
        image_input = params.get("image", "")
        prompt = params.get("prompt", "Describe the image.")
        model = params.get("model")
        max_bytes_mb = params.get("maxBytesMb")

        if not image_input:
            return ToolResult(success=False, content="", error="image parameter required")

        # Remove @ prefix if present (matches TS line 346)
        if image_input.startswith("@"):
            image_input = image_input[1:].strip()

        try:
            # Determine max bytes
            max_bytes = None
            if max_bytes_mb:
                max_bytes = int(max_bytes_mb * 1024 * 1024)
            elif self.max_bytes:
                max_bytes = self.max_bytes

            # Load media — pass url_or_path positionally so load_media's
            # source/url_or_path first-arg works regardless of name.
            # ✅ FIX: Pass workspace_root to resolve relative paths correctly
            media = await load_media(
                image_input, 
                max_bytes,
                workspace_root=self.workspace_root
            )

            # Validate that we received an image
            kind = media.kind or MediaKind.UNKNOWN
            if kind not in (MediaKind.IMAGE, MediaKind.UNKNOWN):
                return ToolResult(
                    success=False,
                    content="",
                    error=f"Unsupported media type: {kind.value}",
                )

            buffer = media.buffer
            mime_type = media.content_type or "image/png"

            # Convert HEIC to JPEG if needed
            if is_heic_mime(mime_type):
                logger.info("Converting HEIC to JPEG")
                buffer = await convert_heic_to_jpeg(buffer)
                mime_type = "image/jpeg"

            # Optimize if too large
            if self.optimize_images and max_bytes and len(buffer) > max_bytes:
                logger.info(f"Optimizing image: {len(buffer)} -> target {max_bytes}")
                optimized = await ImageProcessor.optimize_image(
                    buffer,
                    max_bytes=max_bytes,
                    preserve_alpha=True,
                )
                buffer = optimized.buffer
                mime_type = f"image/{optimized.format}"
                logger.info(f"Optimized to {len(buffer)} bytes")

            # --- Save to workspace (mirrors TS imageResultFromFile) -----------
            # When a workspace root is available, persist the loaded image so
            # the delivery layer can send it as an attachment.  The saved path
            # is emitted as a MEDIA: token in the tool-result text so that
            # split_media_from_output() in channel_manager picks it up and
            # send_media() delivers it to the user (same as TS imageResult).
            saved_path: str | None = None
            if self.workspace_root:
                import hashlib as _hashlib

                ext = extension_for_mime(mime_type) or ".jpg"
                # Prefer the original filename; fall back to a content hash.
                base_name = (media.file_name or "").strip()
                if not base_name:
                    digest = _hashlib.md5(buffer[:256]).hexdigest()[:8]
                    base_name = f"image_{digest}{ext}"
                elif not Path(base_name).suffix:
                    base_name = f"{base_name}{ext}"

                dest = Path(self.workspace_root) / base_name
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(buffer)
                saved_path = str(dest)
                logger.info("Image saved to workspace: %s", saved_path)

            # Encode to base64 for vision-model analysis
            image_data = base64.b64encode(buffer).decode("utf-8")

            # Analyze with model (with fallback)
            result = await self._analyze_with_fallback(
                image_data, mime_type, prompt, model
            )

            # Prepend MEDIA: token so delivery layer sends the file to the user
            # (mirrors TS imageResult where text = "MEDIA:${path}\n<analysis>")
            if saved_path and result.success:
                result.content = f"MEDIA:{saved_path}\n{result.content}"

            return result

        except FileNotFoundError as e:
            logger.error(f"Image analysis error: {e}", exc_info=True)
            return ToolResult(
                success=False,
                content="",
                error=(
                    f"{e}. "
                    "If the image was sent by the user, it is available in your vision context — "
                    "you can analyze it directly without calling this tool. "
                    "Alternatively, check the `incoming/` folder in the session workspace for "
                    "the materialized image file."
                ),
            )
        except Exception as e:
            logger.error(f"Image analysis error: {e}", exc_info=True)
            return ToolResult(success=False, content="", error=str(e))

    async def _analyze_with_fallback(
        self,
        image_data: str,
        media_type: str,
        prompt: str,
        model_override: str | None = None,
    ) -> ToolResult:
        """
        Analyze image with model fallback (matches TS runWithImageModelFallback).
        
        Priority order:
        1. Model override (if specified)
        2. Claude Sonnet 4
        3. GPT-4 Vision
        4. Claude Sonnet 3.5
        
        Args:
            image_data: Base64 image data
            media_type: MIME type
            prompt: Analysis prompt
            model_override: Override model
        
        Returns:
            ToolResult
        """
        attempts = []
        
        # Try models in order
        models_to_try = []
        
        if model_override:
            models_to_try.append(("override", model_override))
        
        # Add default fallback order
        models_to_try.extend([
            ("anthropic", "claude-3-5-sonnet-20241022"),
            ("openai", "gpt-4-vision-preview"),
            ("anthropic", "claude-3-5-sonnet-20240620"),
        ])
        
        for provider, model_name in models_to_try:
            try:
                if provider == "anthropic" or "claude" in model_name.lower():
                    result = await self._analyze_with_claude(image_data, media_type, prompt, model_name)
                    if result.success:
                        result.metadata = result.metadata or {}
                        result.metadata["attempts"] = attempts
                        return result
                    else:
                        attempts.append({
                            "provider": "anthropic",
                            "model": model_name,
                            "error": result.error or "Unknown error"
                        })
                
                elif provider == "openai" or "gpt" in model_name.lower():
                    result = await self._analyze_with_openai(image_data, media_type, prompt, model_name)
                    if result.success:
                        result.metadata = result.metadata or {}
                        result.metadata["attempts"] = attempts
                        return result
                    else:
                        attempts.append({
                            "provider": "openai",
                            "model": model_name,
                            "error": result.error or "Unknown error"
                        })
            
            except Exception as e:
                attempts.append({
                    "provider": provider,
                    "model": model_name,
                    "error": str(e)
                })
                logger.warning(f"Model {provider}/{model_name} failed: {e}")
        
        # All models failed
        return ToolResult(
            success=False,
            content="",
            error=f"All vision models failed. Attempts: {len(attempts)}",
            metadata={"attempts": attempts}
        )

    async def _analyze_with_claude(
        self,
        image_data: str,
        media_type: str,
        prompt: str,
        model: str = "claude-3-5-sonnet-20241022",
    ) -> ToolResult:
        """
        Analyze image using Claude (matches TS Claude logic).
        
        Args:
            image_data: Base64 encoded image
            media_type: MIME type
            prompt: Analysis prompt
            model: Claude model name
        
        Returns:
            ToolResult
        """
        try:
            from anthropic import AsyncAnthropic

            api_key = os.getenv("ANTHROPIC_API_KEY")
            if not api_key:
                return ToolResult(
                    success=False,
                    content="",
                    error="ANTHROPIC_API_KEY not set"
                )

            client = AsyncAnthropic(api_key=api_key)

            response = await client.messages.create(
                model=model,
                max_tokens=4096,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": media_type,
                                    "data": image_data,
                                },
                            },
                            {"type": "text", "text": prompt},
                        ],
                    }
                ],
            )

            # Extract text from response
            text_content = ""
            for block in response.content:
                if block.type == "text":
                    text_content += block.text

            return ToolResult(
                success=True,
                content=text_content,
                metadata={
                    "provider": "anthropic",
                    "model": response.model,
                    "usage": {
                        "input_tokens": response.usage.input_tokens,
                        "output_tokens": response.usage.output_tokens,
                    },
                },
            )

        except ImportError:
            return ToolResult(
                success=False,
                content="",
                error="anthropic package not installed"
            )
        except Exception as e:
            logger.error(f"Claude image analysis failed: {e}")
            return ToolResult(success=False, content="", error=str(e))

    async def _analyze_with_openai(
        self,
        image_data: str,
        media_type: str,
        prompt: str,
        model: str = "gpt-4-vision-preview",
    ) -> ToolResult:
        """
        Analyze image using OpenAI (matches TS OpenAI logic).
        
        Args:
            image_data: Base64 encoded image
            media_type: MIME type
            prompt: Analysis prompt
            model: OpenAI model name
        
        Returns:
            ToolResult
        """
        try:
            from openai import AsyncOpenAI

            api_key = os.getenv("OPENAI_API_KEY")
            if not api_key:
                return ToolResult(
                    success=False,
                    content="",
                    error="OPENAI_API_KEY not set"
                )

            client = AsyncOpenAI(api_key=api_key)

            response = await client.chat.completions.create(
                model=model,
                max_tokens=4096,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{media_type};base64,{image_data}"
                                },
                            },
                            {"type": "text", "text": prompt},
                        ],
                    }
                ],
            )

            content = response.choices[0].message.content or ""

            return ToolResult(
                success=True,
                content=content,
                metadata={
                    "provider": "openai",
                    "model": response.model,
                    "usage": {
                        "prompt_tokens": response.usage.prompt_tokens if response.usage else 0,
                        "completion_tokens": response.usage.completion_tokens if response.usage else 0,
                    },
                },
            )

        except ImportError:
            return ToolResult(
                success=False,
                content="",
                error="openai package not installed"
            )
        except Exception as e:
            logger.error(f"OpenAI image analysis failed: {e}")
            return ToolResult(success=False, content="", error=str(e))
