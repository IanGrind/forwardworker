import re
import random
import time
import math
import logging
import asyncio
from uuid import uuid4
from database import db
from config import temp
from translation import Translation
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from pyrogram.errors import MessageNotModified

STATUS = {}
SYD = ["https://files.catbox.moe/3lwlbm.png"]
logger = logging.getLogger(__name__)

def get_size(size):
    """Converts bytes to a human-readable format."""
    if not size: return ""
    units = ["Bytes", "KB", "MB", "GB", "TB"]
    i = 0
    while size >= 1024 and i < len(units) - 1:
        size /= 1024; i += 1
    return f"{size:.2f} {units[i]}"

async def update_configs(user_id, key, value):
    configs = await db.get_configs(user_id)
    configs[key] = value
    await db.update_configs(user_id, configs)

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
            'start': time.time(), 'status': 'running'
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

async def start_range_selection(bot, message: Message, from_chat_id, from_title, to_chat_id, start_id, end_id):
    session_id = str(uuid4())
    range_message = await bot.send_message(
        chat_id=message.chat.id, text="Preparing range selection...", reply_to_message_id=message.id
    )
    temp.RANGE_SESSIONS[session_id] = {
        'user_id': message.chat.id,
        'from_chat_id': from_chat_id, 'from_title': from_title,
        'to_chat_id': to_chat_id, 'start_id': start_id, 'end_id': end_id,
        'original_message_id': message.id,
        'range_message_id': range_message.id
    }
    await update_range_message(bot, session_id)

async def update_range_message(bot, session_id):
    session = temp.RANGE_SESSIONS.get(session_id)
    if not session: return

    text = Translation.RANGE_SELECTION_TXT.format(
        start=min(session['start_id'], session['end_id']), 
        end=max(session['start_id'], session['end_id'])
    )
    
    buttons = [
        [InlineKeyboardButton(f"Range: {session['start_id']} ➔ {session['end_id']}", callback_data="noop")],
        [InlineKeyboardButton("✎ Edit Start", callback_data=f"range_edit_start_{session_id}"),
         InlineKeyboardButton("✎ Edit End", callback_data=f"range_edit_end_{session_id}")],
        [InlineKeyboardButton("⇄ Swap", callback_data=f"range_swap_{session_id}")],
        [InlineKeyboardButton("✓ Confirm", callback_data=f"range_confirm_{session_id}")],
        [InlineKeyboardButton("« Cancel", callback_data=f"range_cancel_{session_id}")]
    ]
    
    try:
        await bot.edit_message_text(
            chat_id=session['user_id'], message_id=session['range_message_id'],
            text=text, reply_markup=InlineKeyboardMarkup(buttons)
        )
    except Exception as e:
        logger.error(f"Error in update_range_message: {e}", exc_info=True)

async def edit_progress(message, sts, done=False):
    task_id = sts.get('id')
    
    async def editor_loop():
        while not temp.CANCEL.get(task_id):
            text, buttons = progress_message_content(sts, sts.get('start'), task_id)
            try: await message.edit_text(text, reply_markup=buttons)
            except MessageNotModified: pass
            except Exception as e:
                logger.warning(f"Progress update failed: {e}")
                break
            await asyncio.sleep(5)

    editor_task = asyncio.create_task(editor_loop())
    
    # This is a bit tricky; we just let the task run.
    # When the main forwarder finishes, it will call edit_progress with done=True
    if done:
        editor_task.cancel()
        final_text, _ = progress_message_content(sts, sts.get('start'), task_id, done=True)
        try: await message.edit_text(final_text, reply_markup=None)
        except: pass

def progress_message_content(sts, start_time, task_id, done=False):
    total = sts.get('total', 1)
    fetched = sts.get('fetched', 0)
    forwarded = sts.get('total_files', 0)
    failed = sts.get('failed', 0)
    
    elapsed_time = time.time() - start_time
    if elapsed_time == 0: elapsed_time = 1
    
    is_cancelled = temp.CANCEL.get(task_id)

    if done or is_cancelled:
        status = "Cancelled" if is_cancelled else "Completed"
        text = (
            f"✅ **Task {status}!**\n\n"
            f"**Total Forwarded:** `{forwarded}`\n"
            f"**Total Failed:** `{failed}`\n"
            f"**Time Taken:** `{get_readable_time(int(elapsed_time))}`"
        )
        return text, None

    speed = fetched / elapsed_time
    percentage = (fetched * 100) / total if total > 0 else 0
    eta = get_readable_time(int(((total - fetched) / speed) if speed > 0 else 0))
    progress_bar = "▰" * math.floor(percentage / 10) + "▱" * (10 - math.floor(percentage / 10))
    
    text = (
        f"<b>Status:</b> `Running...`\n"
        f"<b>Progress:</b> `{fetched} / {total}`\n"
        f"{progress_bar} `({percentage:.2f}%)`\n\n"
        f"<b>Forwarded:</b> `{forwarded}`\n"
        f"<b>Failed:</b> `{failed}`\n"
        f"<b>ETA:</b> `{eta}`"
    )
    buttons = InlineKeyboardMarkup(
        [[InlineKeyboardButton("✖️ Cancel Task ✖️", callback_data=f"cancel_task_{task_id}")]]
    )
    return text, buttons
