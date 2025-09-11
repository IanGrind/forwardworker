import re
import random
import time as tm
import logging
from uuid import uuid4
from database import db
from config import temp
from translation import Translation
from .parser import parse_buttons
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

STATUS = {}
SYD = ["https://files.catbox.moe/3lwlbm.png"]
logger = logging.getLogger(__name__)

def get_readable_time(seconds: int) -> str:
    if seconds == 0: return "0s"
    result = ""
    (days, remainder) = divmod(seconds, 86400)
    if days > 0: result += f"{int(days)}d "
    (hours, remainder) = divmod(remainder, 3600)
    if hours > 0: result += f"{int(hours)}h "
    (minutes, seconds) = divmod(remainder, 60)
    if minutes > 0: result += f"{int(minutes)}m "
    if seconds > 0: result += f"{int(seconds)}s"
    return result.strip()

class STS:
    def __init__(self, id):
        self.id = id
        self.data = STATUS

    def verify(self):
        return self.data.get(self.id)

    def store(self, From, to, start_id, end_id):
        self.data[self.id] = {
            "id": self.id, "FROM": From, 'TO': to, 'total_files': 0,
            'start_id': start_id, 'end_id': end_id, 'fetched': 0, 
            'failed': 0, 'total': abs(end_id - start_id) + 1, 
            'start': tm.time(), 'status': 'running'
        }
        return self.get(full=True)

    def get(self, value=None, full=False):
        values = self.data.get(self.id)
        if not values: return None
        if not full: return values.get(value)
        
        for k, v in values.items(): setattr(self, k, v)
        return self

    def add(self, key=None, value=1):
        if self.data.get(self.id) and key in self.data[self.id]:
            self.data[self.id][key] += value

async def start_range_selection(bot, message: Message, from_chat_id, from_title, to_chat_id, start_id, end_id, bot_id):
    session_id = str(uuid4())
    temp.RANGE_SESSIONS[session_id] = {
        'user_id': message.chat.id, 'chat_id': message.chat.id,
        'from_chat_id': from_chat_id, 'from_title': from_title,
        'to_chat_id': to_chat_id, 'start_id': start_id, 'end_id': end_id,
        'order': 'asc', 'bot_id': bot_id, 'original_message_id': message.id
    }
    await update_range_message(bot, session_id)

async def update_range_message(bot, session_id, message_to_edit=None):
    session = temp.RANGE_SESSIONS.get(session_id)
    if not session: return

    order_text = "Oldest ➔ Newest" if session['order'] == 'asc' else "Newest ➔ Oldest"
    text = Translation.RANGE_SELECTION_TXT.format(
        start=min(session['start_id'], session['end_id']), 
        end=max(session['start_id'], session['end_id'])
    )
    
    buttons = [
        [InlineKeyboardButton(f"Range: {session['start_id']} ➔ {session['end_id']} ({order_text})", callback_data="noop")],
        [InlineKeyboardButton("✎ Edit Start", callback_data=f"range_edit_start_{session_id}"),
         InlineKeyboardButton("✎ Edit End", callback_data=f"range_edit_end_{session_id}")],
        [InlineKeyboardButton("⇄ Swap Order", callback_data=f"range_swap_{session_id}")],
        [InlineKeyboardButton("✓ Confirm Range", callback_data=f"range_confirm_{session_id}")],
        [InlineKeyboardButton("« Cancel", callback_data=f"range_cancel_{session_id}")]
    ]
    
    try:
        if message_to_edit:
            await message_to_edit.edit_text(text=text, reply_markup=InlineKeyboardMarkup(buttons))
        else:
            await bot.send_message(
                chat_id=session['chat_id'], text=text,
                reply_markup=InlineKeyboardMarkup(buttons),
                reply_to_message_id=session['original_message_id']
            )
    except Exception as e:
        logger.error(f"Error in update_range_message: {e}", exc_info=True)
