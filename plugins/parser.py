import re
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

def parse_buttons(text: str):
    """
    Parses a string to create inline keyboard buttons.
    Supports multiple buttons per line separated by '|' and multiple lines.
    Format: [Text](buttonurl:url)
    """
    if not text or not text.strip():
        return None
    
    lines = text.strip().split('\n')
    keyboard = []

    for line in lines:
        line_buttons = []
        buttons_data = line.split('|')
        for button_data in buttons_data:
            button_data = button_data.strip()
            # Regex to find [text](buttonurl:url) format
            match = re.match(r"\[(.+?)\]\(buttonurl:(.+?)\)", button_data)
            if match:
                text, url = match.groups()
                line_buttons.append(InlineKeyboardButton(text.strip(), url=url.strip()))
        if line_buttons:
            keyboard.append(line_buttons)
            
    return InlineKeyboardMarkup(keyboard) if keyboard else None
