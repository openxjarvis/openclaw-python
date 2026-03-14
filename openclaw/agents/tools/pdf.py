"""PDF analysis tool matching TypeScript pdf-tool.ts

Analyze PDF documents using AI models with native PDF support or fallback extraction.

Aligns with TS openclaw/src/agents/tools/pdf-tool.ts (560 lines)
"""
from __future__ import annotations

import asyncio
import base64
import logging
import re
from pathlib import Path
from typing import Any, Literal

import aiohttp

from .base import AgentToolBase, AgentToolResult, TextContent

logger = logging.getLogger(__name__)

# Constants matching TS pdf-tool.ts lines 42-50
DEFAULT_PROMPT = "Analyze this PDF document."
DEFAULT_MAX_PDFS = 10
DEFAULT_MAX_BYTES_MB = 10
DEFAULT_MAX_PAGES = 20
ANTHROPIC_PDF_PRIMARY = "anthropic/claude-opus-4-6"
ANTHROPIC_PDF_FALLBACK = "anthropic/claude-opus-4-5"

PDF_MIN_TEXT_CHARS = 200
PDF_MAX_PIXELS = 4_000_000

# Providers with native PDF support (matches TS pdf-tool.helpers.ts line 16)
NATIVE_PDF_PROVIDERS = {"anthropic", "google"}


def parse_page_range(range_str: str, max_pages: int) -> list[int]:
    """
    Parse page range string into list of 1-based page numbers.
    
    Matches TS pdf-tool.helpers.ts parsePageRange() lines 28-56
    
    Examples:
        "1-5"       → [1,2,3,4,5]
        "1,3,5-7"   → [1,3,5,6,7]
        "2-4,6"     → [2,3,4,6]
    
    Args:
        range_str: Page range string
        max_pages: Maximum page number allowed
        
    Returns:
        Sorted list of unique page numbers (1-based)
        
    Raises:
        ValueError: If range is invalid
    """
    pages = set()
    parts = [p.strip() for p in range_str.split(",")]
    
    for part in parts:
        if not part:
            continue
            
        # Check for range (e.g., "1-5")
        dash_match = re.match(r"^(\d+)\s*-\s*(\d+)$", part)
        if dash_match:
            start = int(dash_match.group(1))
            end = int(dash_match.group(2))
            
            if start < 1 or end < start:
                raise ValueError(f"Invalid page range: '{part}'")
            
            for i in range(start, min(end, max_pages) + 1):
                pages.add(i)
        else:
            # Single page number
            try:
                num = int(part)
            except ValueError:
                raise ValueError(f"Invalid page number: '{part}'")
            
            if num < 1:
                raise ValueError(f"Invalid page number: '{part}'")
            
            if num <= max_pages:
                pages.add(num)
    
    return sorted(pages)


def provider_supports_native_pdf(provider: str) -> bool:
    """Check if provider supports native PDF input (matches TS line 21)"""
    return provider.lower().strip() in NATIVE_PDF_PROVIDERS


async def anthropic_analyze_pdf(
    api_key: str,
    model_id: str,
    prompt: str,
    pdfs: list[dict[str, str]],
    max_tokens: int = 4096,
    base_url: str = "https://api.anthropic.com",
) -> str:
    """
    Analyze PDF using Anthropic's native PDF API.
    
    Matches TS pdf-native-providers.ts anthropicAnalyzePdf() lines 36-107
    
    Args:
        api_key: Anthropic API key
        model_id: Model identifier (e.g., "claude-opus-4-6")
        prompt: Analysis prompt
        pdfs: List of PDFs with base64 data and optional filename
        max_tokens: Max response tokens
        base_url: API base URL
        
    Returns:
        Analysis text from model
        
    Raises:
        Exception: If API call fails
    """
    if not api_key:
        raise ValueError("Anthropic PDF: apiKey required")
    
    # Build content blocks
    content = []
    for pdf in pdfs:
        content.append({
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": "application/pdf",
                "data": pdf["base64"],
            }
        })
    content.append({"type": "text", "text": prompt})
    
    # API request
    url = f"{base_url.rstrip('/')}/v1/messages"
    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "anthropic-beta": "pdfs-2024-09-25",
    }
    
    async with aiohttp.ClientSession() as session:
        async with session.post(
            url,
            headers=headers,
            json={
                "model": model_id,
                "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": content}],
            }
        ) as response:
            if not response.ok:
                body = await response.text()
                raise Exception(
                    f"Anthropic PDF request failed ({response.status} {response.reason}): "
                    f"{body[:400]}"
                )
            
            result = await response.json()
            
            if "content" not in result or not isinstance(result["content"], list):
                raise Exception("Anthropic PDF response missing content array")
            
            # Extract text from response
            text_parts = [
                block.get("text", "")
                for block in result["content"]
                if block.get("type") == "text"
            ]
            text = "".join(text_parts).strip()
            
            if not text:
                raise Exception("Anthropic PDF returned no text")
            
            return text


async def gemini_analyze_pdf(
    api_key: str,
    model_id: str,
    prompt: str,
    pdfs: list[dict[str, str]],
    max_tokens: int = 4096,
    base_url: str = "https://generativelanguage.googleapis.com",
) -> str:
    """
    Analyze PDF using Google Gemini's native PDF API.
    
    Matches TS pdf-native-providers.ts geminiAnalyzePdf() lines 109-195
    
    Args:
        api_key: Google API key
        model_id: Model identifier (e.g., "gemini-2.5-pro")
        prompt: Analysis prompt
        pdfs: List of PDFs with base64 data
        max_tokens: Max response tokens
        base_url: API base URL
        
    Returns:
        Analysis text from model
    """
    if not api_key:
        raise ValueError("Google Gemini PDF: apiKey required")
    
    # Build parts list
    parts = []
    for pdf in pdfs:
        parts.append({
            "inline_data": {
                "mime_type": "application/pdf",
                "data": pdf["base64"]
            }
        })
    parts.append({"text": prompt})
    
    # API request
    url = f"{base_url.rstrip('/')}/v1beta/models/{model_id}:generateContent?key={api_key}"
    
    async with aiohttp.ClientSession() as session:
        async with session.post(
            url,
            json={
                "contents": [{"parts": parts}],
                "generationConfig": {
                    "maxOutputTokens": max_tokens,
                }
            }
        ) as response:
            if not response.ok:
                body = await response.text()
                raise Exception(
                    f"Gemini PDF request failed ({response.status} {response.reason}): "
                    f"{body[:400]}"
                )
            
            result = await response.json()
            
            # Extract text from candidates
            candidates = result.get("candidates", [])
            if not candidates:
                raise Exception("Gemini PDF response has no candidates")
            
            text_parts = []
            for candidate in candidates:
                content = candidate.get("content", {})
                parts = content.get("parts", [])
                for part in parts:
                    if "text" in part:
                        text_parts.append(part["text"])
            
            text = "".join(text_parts).strip()
            
            if not text:
                raise Exception("Gemini PDF returned no text")
            
            return text


async def extract_pdf_fallback(pdf_path: Path, page_numbers: list[int] | None = None) -> dict:
    """
    Extract text and images from PDF as fallback.
    
    Uses PyPDF2 for text and pdf2image for images.
    Matches TS media/pdf-extract.ts functionality.
    
    Args:
        pdf_path: Path to PDF file
        page_numbers: Optional list of page numbers (1-based)
        
    Returns:
        Dict with 'text' and 'images' keys
    """
    try:
        import PyPDF2
    except ImportError:
        raise ImportError("PyPDF2 not installed. Run: pip install pypdf2")
    
    try:
        from pdf2image import convert_from_path
    except ImportError:
        logger.warning("pdf2image not installed. Images will not be extracted.")
        convert_from_path = None
    
    extracted_text = []
    images = []
    
    # Extract text
    with open(pdf_path, "rb") as f:
        reader = PyPDF2.PdfReader(f)
        total_pages = len(reader.pages)
        
        # Determine which pages to process
        if page_numbers:
            pages_to_process = [p - 1 for p in page_numbers if 0 < p <= total_pages]
        else:
            pages_to_process = list(range(total_pages))
        
        for page_num in pages_to_process:
            page = reader.pages[page_num]
            text = page.extract_text()
            if text.strip():
                extracted_text.append(text)
    
    # Extract images if pdf2image is available
    if convert_from_path and len(extracted_text) == 0:
        try:
            # Convert PDF pages to images
            pil_images = convert_from_path(
                pdf_path,
                first_page=min(pages_to_process) + 1 if page_numbers else 1,
                last_page=max(pages_to_process) + 1 if page_numbers else total_pages,
                dpi=150  # Balance quality vs size
            )
            
            for img in pil_images[:10]:  # Limit to 10 images
                # Convert to base64
                import io
                buffer = io.BytesIO()
                img.save(buffer, format="PNG")
                img_base64 = base64.b64encode(buffer.getvalue()).decode()
                
                images.append({
                    "data": img_base64,
                    "mimeType": "image/png"
                })
        except Exception as e:
            logger.warning(f"Failed to extract images from PDF: {e}")
    
    return {
        "text": "\n\n".join(extracted_text),
        "images": images
    }


class PdfAnalysisTool(AgentToolBase):
    """
    Analyze PDF documents with AI models.
    
    Matches TS createPdfTool() in pdf-tool.ts lines 295-560.
    
    Supports:
    - Native PDF analysis (Anthropic Claude, Google Gemini)
    - Fallback text/image extraction for other models
    - Multiple PDFs (up to 10)
    - Page range selection ("1-5", "1,3,5-7")
    - HTTP URLs and local paths
    - File size limits
    """
    
    def __init__(
        self,
        workspace_dir: Path | None = None,
        config: dict | None = None,
        max_bytes_mb: float = DEFAULT_MAX_BYTES_MB,
        max_pdfs: int = DEFAULT_MAX_PDFS,
        max_pages: int = DEFAULT_MAX_PAGES,
    ):
        """
        Initialize PDF analysis tool.
        
        Args:
            workspace_dir: Workspace root directory
            config: OpenClaw configuration
            max_bytes_mb: Maximum PDF file size in MB
            max_pdfs: Maximum number of PDFs
            max_pages: Maximum pages per PDF
        """
        super().__init__()
        self.name = "pdf"
        self.label = "PDF"
        self.description = (
            "Analyze one or more PDF documents with a model. Supports native PDF analysis "
            "for Anthropic and Google models, with text/image extraction fallback for other "
            "providers. Use pdf for a single path/URL, or pdfs for multiple (up to 10). "
            "Provide a prompt describing what to analyze."
        )
        
        self.workspace_dir = workspace_dir
        self.config = config or {}
        self.max_bytes = int(max_bytes_mb * 1024 * 1024)
        self.max_pdfs = max_pdfs
        self.max_pages = max_pages
    
    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "Analysis prompt (optional)"
                },
                "pdf": {
                    "type": "string",
                    "description": "Single PDF path or URL"
                },
                "pdfs": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Multiple PDF paths or URLs (up to 10)"
                },
                "pages": {
                    "type": "string",
                    "description": 'Page range to process, e.g. "1-5", "1,3,5-7". Defaults to all pages.'
                },
                "model": {
                    "type": "string",
                    "description": "Model override (e.g., 'anthropic/claude-opus-4-6')"
                },
                "maxBytesMb": {
                    "type": "number",
                    "description": "Maximum file size in MB (optional)"
                }
            }
        }
    
    async def execute(
        self,
        tool_call_id: str,
        params: dict,
        signal: asyncio.Event | None = None,
        on_update: Any = None,
    ) -> AgentToolResult:
        """Execute PDF analysis (matches TS execute logic lines 357-559)"""
        
        # Normalize pdf + pdfs input (matches TS lines 360-381)
        pdf_candidates = []
        if params.get("pdf"):
            pdf_candidates.append(params["pdf"])
        if params.get("pdfs") and isinstance(params["pdfs"], list):
            pdf_candidates.extend(params["pdfs"])
        
        # Dedupe while preserving order
        seen = set()
        pdf_inputs = []
        for candidate in pdf_candidates:
            trimmed = str(candidate).strip()
            if trimmed and trimmed not in seen:
                seen.add(trimmed)
                pdf_inputs.append(trimmed)
        
        if not pdf_inputs:
            return AgentToolResult(
                content=[TextContent(text="pdf required: provide a path or URL to a PDF document")]
            )
        
        # Enforce max PDFs cap (matches TS lines 383-394)
        if len(pdf_inputs) > self.max_pdfs:
            return AgentToolResult(
                content=[TextContent(
                    text=f"Too many PDFs: {len(pdf_inputs)} provided, maximum is {self.max_pdfs}. "
                    f"Please reduce the number."
                )],
                details={"error": "too_many_pdfs", "count": len(pdf_inputs), "max": self.max_pdfs}
            )
        
        prompt = params.get("prompt", DEFAULT_PROMPT)
        model_override = params.get("model")
        max_bytes_mb = params.get("maxBytesMb", DEFAULT_MAX_BYTES_MB)
        max_bytes = int(max_bytes_mb * 1024 * 1024)
        pages_raw = params.get("pages", "").strip()
        
        # Parse page range if provided
        page_numbers = None
        if pages_raw:
            try:
                page_numbers = parse_page_range(pages_raw, self.max_pages)
            except ValueError as e:
                return AgentToolResult(
                    content=[TextContent(text=f"Invalid page range: {e}")]
                )
        
        # Load PDFs (matches TS lines 420-506)
        loaded_pdfs = []
        
        for pdf_input in pdf_inputs:
            try:
                # Check if it's a URL
                is_http_url = pdf_input.startswith(("http://", "https://"))
                
                if is_http_url:
                    # Download from URL
                    async with aiohttp.ClientSession() as session:
                        async with session.get(pdf_input) as response:
                            if not response.ok:
                                return AgentToolResult(
                                    content=[TextContent(
                                        text=f"Failed to download PDF from {pdf_input}: "
                                        f"{response.status} {response.reason}"
                                    )]
                                )
                            
                            pdf_bytes = await response.read()
                            
                            if len(pdf_bytes) > max_bytes:
                                return AgentToolResult(
                                    content=[TextContent(
                                        text=f"PDF too large: {len(pdf_bytes) / 1024 / 1024:.1f}MB "
                                        f"(max {max_bytes / 1024 / 1024:.1f}MB)"
                                    )]
                                )
                            
                            pdf_base64 = base64.b64encode(pdf_bytes).decode()
                            filename = pdf_input.split("/")[-1] or "document.pdf"
                            
                            loaded_pdfs.append({
                                "base64": pdf_base64,
                                "bytes": pdf_bytes,
                                "filename": filename,
                                "path": None
                            })
                else:
                    # Load from local path
                    pdf_path = Path(pdf_input).expanduser()
                    
                    if not pdf_path.exists():
                        return AgentToolResult(
                            content=[TextContent(text=f"PDF not found: {pdf_input}")]
                        )
                    
                    pdf_bytes = pdf_path.read_bytes()
                    
                    if len(pdf_bytes) > max_bytes:
                        return AgentToolResult(
                            content=[TextContent(
                                text=f"PDF too large: {len(pdf_bytes) / 1024 / 1024:.1f}MB "
                                f"(max {max_bytes / 1024 / 1024:.1f}MB)"
                            )]
                        )
                    
                    pdf_base64 = base64.b64encode(pdf_bytes).decode()
                    
                    loaded_pdfs.append({
                        "base64": pdf_base64,
                        "bytes": pdf_bytes,
                        "filename": pdf_path.name,
                        "path": pdf_path
                    })
            
            except Exception as e:
                logger.error(f"Failed to load PDF {pdf_input}: {e}", exc_info=True)
                return AgentToolResult(
                    content=[TextContent(text=f"Failed to load PDF {pdf_input}: {str(e)}")]
                )
        
        # Determine model to use
        # For now, try native PDF with Anthropic if available
        # TODO: Integrate with model registry and API keys
        
        try:
            # Try native PDF with Anthropic (if API key available)
            from openclaw.agents.llm_providers import get_api_key
            
            api_key = get_api_key("anthropic")
            if api_key:
                model_id = model_override or ANTHROPIC_PDF_PRIMARY
                
                # Extract just the model name if it includes provider
                if "/" in model_id:
                    model_id = model_id.split("/", 1)[1]
                
                try:
                    result_text = await anthropic_analyze_pdf(
                        api_key=api_key,
                        model_id=model_id,
                        prompt=prompt,
                        pdfs=loaded_pdfs,
                        max_tokens=4096
                    )
                    
                    return AgentToolResult(
                        content=[TextContent(text=result_text)],
                        details={
                            "provider": "anthropic",
                            "model": model_id,
                            "pdfs_analyzed": len(loaded_pdfs),
                            "method": "native"
                        }
                    )
                except Exception as e:
                    logger.warning(f"Native PDF analysis failed: {e}, falling back to extraction")
        
        except ImportError:
            pass
        
        # Fallback: Extract text and images
        logger.info("Using fallback PDF extraction mode")
        
        all_extractions = []
        
        for pdf_data in loaded_pdfs:
            if pdf_data["path"]:
                extraction = await extract_pdf_fallback(pdf_data["path"], page_numbers)
                all_extractions.append(extraction)
            else:
                # Write bytes to temp file for extraction
                import tempfile
                with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                    tmp.write(pdf_data["bytes"])
                    tmp_path = Path(tmp.name)
                
                try:
                    extraction = await extract_pdf_fallback(tmp_path, page_numbers)
                    all_extractions.append(extraction)
                finally:
                    tmp_path.unlink()
        
        # Build fallback context
        extracted_texts = []
        for i, extraction in enumerate(all_extractions):
            if extraction["text"].strip():
                label = f"[PDF {i + 1} text]\n" if len(all_extractions) > 1 else "[PDF text]\n"
                extracted_texts.append(label + extraction["text"])
        
        if not extracted_texts:
            return AgentToolResult(
                content=[TextContent(
                    text="Could not extract text from PDF(s). The document may be image-based or encrypted."
                )]
            )
        
        # Return extracted text with prompt
        combined_text = "\n\n".join(extracted_texts)
        response_text = f"{prompt}\n\n{combined_text}"
        
        return AgentToolResult(
            content=[TextContent(text=response_text)],
            details={
                "provider": "fallback",
                "method": "extraction",
                "pdfs_analyzed": len(loaded_pdfs),
                "total_text_length": len(combined_text)
            }
        )
