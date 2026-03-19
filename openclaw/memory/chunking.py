"""Text chunking utilities for memory indexing.

Aligned with TypeScript openclaw/src/memory/internal.ts chunking logic.

Provides token-based chunking with overlap (default: 400 tokens, 80 overlap).
"""
from dataclasses import dataclass
from typing import List
import logging

logger = logging.getLogger(__name__)

# Align with TS DEFAULT_CHUNK_TOKENS and DEFAULT_CHUNK_OVERLAP
DEFAULT_CHUNK_TOKENS = 400
DEFAULT_CHUNK_OVERLAP = 80


@dataclass
class TextChunk:
    """A chunk of text with position info."""
    text: str
    start_line: int
    end_line: int
    start_char: int = 0
    end_char: int = 0


def estimate_tokens(text: str) -> int:
    """
    Estimate token count using character-based heuristic.
    
    Rough approximation: ~4 characters per token for English text.
    This matches the TS implementation's char-based fallback.
    
    Args:
        text: Input text
        
    Returns:
        Estimated token count
    """
    return max(1, len(text) // 4)


def chunk_text_by_tokens(
    text: str,
    tokens: int = DEFAULT_CHUNK_TOKENS,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> List[TextChunk]:
    """
    Chunk text by token count with overlap.
    
    Mirrors TS chunkMarkdown() from internal.ts with token-based splitting.
    
    Args:
        text: Text to chunk
        tokens: Target tokens per chunk (default: 400)
        overlap: Overlap tokens between chunks (default: 80)
        
    Returns:
        List of text chunks
    """
    if not text or not text.strip():
        return []
    
    lines = text.split('\n')
    chunks: List[TextChunk] = []
    
    # Estimate characters per chunk (tokens * 4)
    chars_per_chunk = tokens * 4
    overlap_chars = overlap * 4
    
    # If very short, return single chunk
    if len(text) <= chars_per_chunk:
        return [TextChunk(
            text=text,
            start_line=1,
            end_line=len(lines),
            start_char=0,
            end_char=len(text)
        )]
    
    # Split into chunks with overlap
    current_chunk_lines: List[str] = []
    current_chunk_chars = 0
    chunk_start_line = 1
    
    for line_idx, line in enumerate(lines, start=1):
        line_with_newline = line + '\n' if line_idx < len(lines) else line
        line_chars = len(line_with_newline)
        
        # If adding this line would exceed chunk size
        if current_chunk_chars + line_chars > chars_per_chunk and current_chunk_lines:
            # Save current chunk
            chunk_text = ''.join(current_chunk_lines).rstrip('\n')
            if chunk_text.strip():
                chunks.append(TextChunk(
                    text=chunk_text,
                    start_line=chunk_start_line,
                    end_line=line_idx - 1,
                    start_char=sum(len(lines[i]) + 1 for i in range(chunk_start_line - 1)) if chunk_start_line > 1 else 0,
                    end_char=sum(len(lines[i]) + 1 for i in range(line_idx - 1))
                ))
            
            # Calculate overlap lines to keep
            overlap_size = 0
            overlap_lines = []
            for prev_line in reversed(current_chunk_lines):
                if overlap_size + len(prev_line) <= overlap_chars:
                    overlap_lines.insert(0, prev_line)
                    overlap_size += len(prev_line)
                else:
                    break
            
            # Start new chunk with overlap
            current_chunk_lines = overlap_lines
            current_chunk_chars = overlap_size
            chunk_start_line = line_idx - len(overlap_lines)
        
        # Add line to current chunk
        current_chunk_lines.append(line_with_newline)
        current_chunk_chars += line_chars
    
    # Save final chunk
    if current_chunk_lines:
        chunk_text = ''.join(current_chunk_lines).rstrip('\n')
        if chunk_text.strip():
            chunks.append(TextChunk(
                text=chunk_text,
                start_line=chunk_start_line,
                end_line=len(lines),
                start_char=0,  # Simplified
                end_char=len(text)
            ))
    
    return chunks


def chunk_file_by_tokens(
    file_path: str,
    content: str,
    tokens: int = DEFAULT_CHUNK_TOKENS,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> List[dict]:
    """
    Chunk file content by tokens for indexing.
    
    Args:
        file_path: Path to file (for metadata)
        content: File content
        tokens: Target tokens per chunk
        overlap: Overlap tokens
        
    Returns:
        List of chunk dicts with metadata
    """
    chunks = chunk_text_by_tokens(content, tokens, overlap)
    
    result = []
    for chunk in chunks:
        result.append({
            'text': chunk.text,
            'start_line': chunk.start_line,
            'end_line': chunk.end_line,
            'path': file_path,
        })
    
    return result


__all__ = [
    'DEFAULT_CHUNK_TOKENS',
    'DEFAULT_CHUNK_OVERLAP',
    'TextChunk',
    'estimate_tokens',
    'chunk_text_by_tokens',
    'chunk_file_by_tokens',
]
