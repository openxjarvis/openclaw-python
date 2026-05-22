"""Telegram inline buttons."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class InlineButton:
    """Inline button definition."""
    
    text: str
    callback_data: Optional[str] = None
    url: Optional[str] = None
    switch_inline_query: Optional[str] = None


def create_inline_keyboard(buttons: list[list[InlineButton]]) -> dict:
    """Create inline keyboard markup.
    
    Args:
        buttons: 2D list of InlineButton (rows and columns)
    
    Returns:
        Telegram inline keyboard markup dict
    """
    keyboard = []
    
    for row in buttons:
        keyboard_row = []
        for button in row:
            button_dict = {"text": button.text}
            
            if button.callback_data:
                button_dict["callback_data"] = button.callback_data
            elif button.url:
                button_dict["url"] = button.url
            elif button.switch_inline_query is not None:
                button_dict["switch_inline_query"] = button.switch_inline_query
            
            keyboard_row.append(button_dict)
        
        keyboard.append(keyboard_row)
    
    return {"inline_keyboard": keyboard}
