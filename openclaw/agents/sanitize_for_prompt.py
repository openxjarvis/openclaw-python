"""Sanitize untrusted strings before embedding them into an LLM prompt.

Mirrors TypeScript src/agents/sanitize-for-prompt.ts implementation.

Threat model (OC-19): attacker-controlled directory names (or other runtime strings)
that contain newline/control characters can break prompt structure and inject
arbitrary instructions.

Strategy (Option 3 hardening):
- Strip Unicode "control" (Cc) + "format" (Cf) characters (includes CR/LF/NUL, bidi marks, zero-width chars).
- Strip explicit line/paragraph separators (Zl/Zp): U+2028/U+2029.

Notes:
- This is intentionally lossy; it trades edge-case path fidelity for prompt integrity.
- If you need lossless representation, escape instead of stripping.
"""

import re


def sanitize_for_prompt_literal(value: str) -> str:
    """Strip control and format characters from string for safe prompt embedding.
    
    Mirrors TS sanitizeForPromptLiteral function.
    
    Args:
        value: String to sanitize
        
    Returns:
        Sanitized string with control/format characters removed
    """
    # Remove Unicode control (Cc), format (Cf) characters, and line/paragraph separators
    # Python regex doesn't support \p{Cc} directly, so we use ranges and categories
    
    # Control characters (C0 and C1): U+0000-U+001F, U+007F-U+009F
    # Format characters: includes things like zero-width spaces, bidi marks
    # Line separator: U+2028
    # Paragraph separator: U+2029
    
    # Remove control characters, format characters, and line/paragraph separators
    sanitized = re.sub(r'[\x00-\x1f\x7f-\x9f\u2028\u2029]', '', value)
    
    # Also remove common format characters (Cf category)
    # Including: zero-width space, zero-width non-joiner, etc.
    sanitized = re.sub(r'[\u200b-\u200f\u202a-\u202e\u2060-\u2064\ufeff]', '', sanitized)
    
    return sanitized
