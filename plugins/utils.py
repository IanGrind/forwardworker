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

def get_size(size):
    if not size: return ""
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    while size >= 1024 and i < len(units) - 1:
        size /= 1024
        i += 1
    return f"{size:.2f} {units[i]}"

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
    range_msg = await bot.send_message(
        chat_id=message.chat.id, text="`Calculating...`"
    )
    temp.RANGE_SESSIONS[session_id] = {
        'user_id': message.chat.id,
        'from_chat_id': from_chat_id, 'from_title': from_title,
        'to_chat_id': to_chat_id, 'start_id': start_id, 'end_id': end_id,
        'original_message_id': message.id,
        'range_message_id': range_msg.id
    }
    await update_range_message(bot, session_id)

async def update_range_message(bot, session_id):
    session = temp.RANGE_SESSIONS.get(session_id)
    if not session: return

    try:
        message_to_edit = await bot.get_messages(session['user_id'], session['range_message_id'])
    except Exception:
        logger.warning(f"Could not find message to edit for range session {session_id}")
        return

    text = Translation.RANGE_SELECTION_TXT.format(
        start=min(session['start_id'], session['end_id']),
        end=max(session['start_id'], session['end_id'])
    )

    buttons = [
        [InlineKeyboardButton(f"Range: {min(session['start_id'], session['end_id'])} ➔ {max(session['start_id'], session['end_id'])}", callback_data="noop")],
        [InlineKeyboardButton("✎ Edit Start", callback_data=f"range_edit_start_{session_id}"),
         InlineKeyboardButton("✎ Edit End", callback_data=f"range_edit_end_{session_id}")],
        [InlineKeyboardButton("⇄ Swap", callback_data=f"range_swap_{session_id}")],
        [InlineKeyboardButton("✓ Confirm", callback_data=f"range_confirm_{session_id}")],
        [InlineKeyboardButton("« Cancel", callback_data=f"range_cancel_{session_id}")]
    ]

    try:
        await message_to_edit.edit_text(text=text, reply_markup=InlineKeyboardMarkup(buttons))
    except Exception as e:
        logger.error(f"Error in update_range_message: {e}", exc_info=True)

async def edit_progress(message, sts, done=False):
    task_id = sts.get('id')
    start_time = sts.get('start')

    try:
        while not temp.CANCEL.get(task_id) and not done:
            if not sts.verify(): break
            text, buttons = progress_message_content(sts, start_time, task_id)
            try:
                await message.edit_text(text, reply_markup=buttons)
            except MessageNotModified:
                pass
            await asyncio.sleep(5)
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.warning(f"Progress update failed: {e}")
    finally:
        if sts.verify():
            text, buttons = progress_message_content(sts, start_time, task_id, done=True)
            try:
                await message.edit_text(text, reply_markup=buttons)
            except Exception: pass

def progress_message_content(sts, start_time, task_id, done=False):
    total = sts.get('total')
    fetched = sts.get('fetched')
    forwarded = sts.get('total_files')
    failed = sts.get('failed')
    elapsed_time = time.time() - start_time
    if elapsed_time == 0: elapsed_time = 1

    if done:
        status = "Completed" if not temp.CANCEL.get(task_id) else "Cancelled"
        text = (
            f"✅ **Task {status}!**\n\n"
            f"**Total Forwarded:** `{forwarded}`\n"
            f"**Total Failed:** `{failed}`\n"
            f"**Time Taken:** `{get_readable_time(int(elapsed_time))}`"
        )
        buttons = None
    else:
        speed = fetched / elapsed_time
        percentage = (fetched * 100) / total if total > 0 else 0
        percentage = min(100.00, percentage) # Visually cap percentage at 100%

        eta = get_readable_time(int(((total - fetched) / speed) if speed > 0 and fetched < total else 0))
        progress_bar = "▰" * math.floor(percentage / 10) + "▱" * (10 - math.floor(percentage / 10))

        status_text = "Running..."

        text = Translation.TEXT.format(
            status=status_text,
            fetched=fetched, total=total,
            forwarded=forwarded,
            skipped=fetched - forwarded - failed,
            failed=failed,
            progress_bar=progress_bar,
            percentage=f"{percentage:.2f}",
            eta=eta
        )

        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("📊 Status", callback_data=f"fwrdstatus_{task_id}")],
            [InlineKeyboardButton("✖️ Cancel Task ✖️", callback_data=f"cancel_task_{task_id}")]
        ])

    return text, buttons

def get_status_alert_text(sts, start_time):
    """Generates the text for the real-time status pop-up alert."""
    total = sts.get('total')
    fetched = sts.get('fetched')
    forwarded = sts.get('total_files')
    failed = sts.get('failed')
    elapsed_time = time.time() - start_time
    if elapsed_time == 0: elapsed_time = 1

    speed = fetched / elapsed_time
    percentage = (fetched * 100) / total if total > 0 else 0
    percentage = min(100.00, percentage)

    eta = get_readable_time(int(((total - fetched) / speed) if speed > 0 and fetched < total else 0))

    return Translation.STATUS_ALERT.format(
        fetched=fetched, total=total,
        percentage=f"{percentage:.2f}",
        forwarded=forwarded, failed=failed,
        skipped=fetched - forwarded - failed,
        status="Running",
        eta=eta
    )
