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

async def edit_progress(message, sts, start_time, done=False):
    """
    Handles both periodic progress updates and the final completion message.
    This function is now designed to be run as a cancellable asyncio task.
    """
    try:
        while not temp.CANCEL.get(sts.id):
            text = progress_text(sts, start_time)
            await message.edit_text(text)
            await asyncio.sleep(5) # Update interval
    except asyncio.CancelledError:
        # This is the expected way to stop the reporter task.
        pass
    except MessageNotModified:
        # It's okay if the message hasn't changed, just continue the loop.
        await asyncio.sleep(5)
    except Exception as e:
        logger.warning(f"Failed to edit progress message: {e}")

    # After the loop is broken or cancelled, send the final status.
    if temp.CANCEL.get(sts.id):
         await message.edit_text("✅ **Task Cancelled by user!**")
    else:
        text = progress_text(sts, start_time, done=True)
        try:
            await message.edit_text(text)
        except MessageNotModified:
            pass
        except Exception as e:
            logger.warning(f"Failed to edit final progress message: {e}")


def progress_text(sts, start_time, done=False):
    """Formats the progress text."""
    total = sts.get('total')
    fetched = sts.get('fetched')
    forwarded = sts.get('total_files')
    failed = sts.get('failed')
    
    elapsed_time = time.time() - start_time
    if elapsed_time == 0: elapsed_time = 1
    
    if done:
        return (
            f"✅ **Forwarding Complete!**\n\n"
            f"**Total Forwarded:** `{forwarded}`\n"
            f"**Total Failed:** `{failed}`\n"
            f"**Time Taken:** `{get_readable_time(int(elapsed_time))}`"
        )

    speed = fetched / elapsed_time
    percentage = (fetched * 100) / total if total > 0 else 0
    
    eta_seconds = ((total - fetched) / speed) if speed > 0 else 0
    eta = get_readable_time(int(eta_seconds))
    
    progress_bar = "▰" * math.floor(percentage / 10) + "▱" * (10 - math.floor(percentage / 10))
    
    # Worker statuses report
    worker_lines = []
    if hasattr(sts, 'worker_statuses'):
        for i, status in sts.worker_statuses.items():
            worker_lines.append(f"  `Worker {i+1}: {status}`")
    worker_report = "\n".join(worker_lines)

    return (
        f"<b>Status:</b> `Running`\n"
        f"<b>Progress:</b> `{fetched} / {total}`\n"
        f"{progress_bar} `({percentage:.2f}%)`\n\n"
        f"<b>Forwarded:</b> `{forwarded}`\n"
        f"<b>Failed:</b> `{failed}`\n"
        f"<b>ETA:</b> `{eta}`\n\n"
        f"<b>Worker Statuses:</b>\n{worker_report}"
    )
