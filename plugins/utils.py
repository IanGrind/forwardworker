# mistaldrin/fwd/fwd-dawn-improve-v2/plugins/utils.py
import re
import random
import time as tm
import logging
from uuid import uuid4
from database import db
from config import temp
from translation import Translation
from .test import parse_buttons
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

STATUS = {}
SYD = ["https://files.catbox.moe/3lwlbm.png"]
logger = logging.getLogger(__name__)

def get_readable_time(seconds: int) -> str:
    if seconds == 0:
        return "0s"
    result = ""
    (days, remainder) = divmod(seconds, 86400)
    days = int(days)
    if days != 0:
        result += f"{days}d "
    (hours, remainder) = divmod(remainder, 3600)
    hours = int(hours)
    if hours != 0:
        result += f"{hours}h "
    (minutes, seconds) = divmod(remainder, 60)
    minutes = int(minutes)
    if minutes != 0:
        result += f"{minutes}m "
    seconds = int(seconds)
    if seconds != 0:
        result += f"{seconds}s"
    return result.strip()

class STS:
    def __init__(self, id):
        self.id = id
        self.data = STATUS

    def verify(self):
        return self.data.get(self.id)

    def store(self, From, to, start_id, end_id):
        self.data[self.id] = {
            "id": self.id,
            "FROM": From, 'TO': to, 'total_files': 0,
            'start_id': start_id, 'end_id': end_id,
            'fetched': 0, 'filtered': 0, 'deleted': 0, 'failed': 0, # Added failed counter
            'duplicate': 0, 'total': abs(end_id - start_id) + 1,
            'start': tm.time(), 'status': 'running', 'batch': []
        }
        self.get(full=True)
        return STS(self.id)

    def get(self, value=None, full=False):
        values = self.data.get(self.id)
        if not values: return None
        if not full:
           return values.get(value)
        for k, v in values.items():
            setattr(self, k, v)
        return self
    
    def add_to_batch(self, message_id):
        """Adds a message ID to the current batch for forwarding."""
        batch = self.get('batch')
        if batch is not None:
            batch.append(message_id)

    def get_batch(self):
        """Returns the current batch of message IDs."""
        return self.get('batch')

    def clear_batch(self):
        """Clears the batch."""
        self.data[self.id]['batch'] = []

    def set_status(self, status):
        """Sets the current task status (e.g., 'running', 'floodwait')."""
        self.data[self.id]['status'] = status
    
    def get_readable_time(self, seconds: int) -> str:
        return get_readable_time(seconds)

    def add(self, key=None, value=1):
        current_value = self.get(key)
        if current_value is not None:
            self.data[self.id].update({key: current_value + value})

    def divide(self, no, by):
       by = 1 if int(by) == 0 else by
       return int(no) / by

    async def get_data(self, user_id):
        bot_id = temp.FORWARD_BOT_ID.get(user_id)
        if not bot_id:
            bot_id = temp.UNEQUIFY_USERBOT_ID.get(user_id)
            if not bot_id:
                raise ValueError("Bot ID not found in session.")

        bot = await db.get_bot(user_id, bot_id)
        k, filters = self, await db.get_filters(user_id)
        size, configs = None, await db.get_configs(user_id)
        if configs['duplicate']:
           duplicate = [configs['db_uri'], self.TO]
        else:
           duplicate = False
        button = parse_buttons(configs['button'] if configs['button'] else '')
        if configs['file_size'] != 0:
            size = [configs['file_size'], configs['size_limit']]

        return bot, configs['caption'], configs['forward_tag'], {
            'filters': filters, 'keywords': configs['keywords'],
            'media_size': size, 'extensions': configs['extension'],
            'skip_duplicate': duplicate,
            'forward_delay': configs.get('forward_delay', 0.5)
        }, configs['protect'], button

async def start_range_selection(bot, message: Message, from_chat_id, from_title, to_chat_id, start_id, end_id, final_callback_prefix="fwd_final"):
    """Initiates an interactive message range selection process."""
    session_id = str(uuid4())
    temp.RANGE_SESSIONS[session_id] = {
        'user_id': message.chat.id,
        'chat_id': message.chat.id,
        'from_chat_id': from_chat_id,
        'from_title': from_title,
        'to_chat_id': to_chat_id,
        'start_id': start_id,
        'end_id': end_id,
        'order': 'asc',
        'final_callback': final_callback_prefix,
        'original_message_id': message.id
    }
    await update_range_message(bot, session_id)

async def update_range_message(bot, session_id, message_to_edit=None):
    """Edits or sends the range selection message as a text message."""
    session = temp.RANGE_SESSIONS.get(session_id)
    if not session: return

    order_text = "Oldest ➔ Newest" if session['order'] == 'asc' else "Newest ➔ Oldest"
    text = Translation.RANGE_SELECTION_TXT.format(
        start=min(session['start_id'], session['end_id']), 
        end=max(session['start_id'], session['end_id'])
    )
    display_button_text = f"Range: {session['start_id']} ➔ {session['end_id']} ({order_text})"

    confirm_cb = f"range_confirm_{session['final_callback']}_{session_id}"

    buttons = [
        [InlineKeyboardButton(display_button_text, callback_data=f"range_info_{session_id}")],
        [InlineKeyboardButton("✎ Edit Start", callback_data=f"range_edit_start_{session_id}"),
         InlineKeyboardButton("✎ Edit End", callback_data=f"range_edit_end_{session_id}")],
        [InlineKeyboardButton("⇄ Swap Order", callback_data=f"range_swap_{session_id}")],
        [InlineKeyboardButton("✓ Confirm Range", callback_data=confirm_cb)],
        [InlineKeyboardButton("« Cancel", callback_data=f"range_cancel_{session_id}")]
    ]
    
    reply_markup = InlineKeyboardMarkup(buttons)
    
    try:
        if message_to_edit:
            new_message = await message_to_edit.edit_text(text=text, reply_markup=reply_markup)
        else:
            new_message = await bot.send_message(
                chat_id=session['chat_id'],
                text=text,
                reply_markup=reply_markup,
                reply_to_message_id=session['original_message_id']
            )
        session['message_id'] = new_message.id
    except Exception as e:
        logger.error(f"Error sending/editing range message: {e}", exc_info=True)
        try:
            await bot.send_message(session['chat_id'], "An error occurred while displaying the menu. Please try again.")
        except Exception as ie:
            logger.error(f"Failed to send error message to user: {ie}")
