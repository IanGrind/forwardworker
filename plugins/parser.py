import rere
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

def parse_buttons(text: str):
    """
    Parses a string to create inline keyboard buttons.
    Expected format:
    [Button Text](button_url) | [Another Button](another_url)
    Each button on the same line is separated by a '|'.
    Each line of buttons is separated by a newline character.
    """
    if not text or not text.strip():
        return None
    
    lines = text.split('\n')
    keyboard = []

    for line in lines:
        line_buttons = []
        # Split buttons oonn the same line by '|'
        buttons_data = line.split('|')
        for button_data in buttons_data:
            button_data = button_data.strip()
            # Regex to find [text](url) format
            match = re.match(r"\[(.+?)\]\((.+?)\)", button_data)
            if match:
                text, url = match.groups()
                line_buttons.append(InlineKeyboardButton(text.strip(), url=url.strip()))
        if line_buttons:
            keyboard.append(line_buttons)
            
    return InlineKeyboardMarkup(keyboard) if keyboard else None
