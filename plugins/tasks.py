import time
import math
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from config import temp
from plugins.utils import STS, get_readable_time, progress_message_content

@Client.on_message(filters.private & filters.command("tasks"))
async def show_tasks(client, message):
    user_id = message.from_user.id
    active_task = temp.ACTIVE_TASKS.get(user_id)

    if not active_task:
        return await message.reply_text("You have no active tasks.")

    task_id = list(active_task.keys())[0]
    sts = STS(task_id)

    if not sts.verify():
        # Clean up dangling task entry if status is gone
        del temp.ACTIVE_TASKS[user_id]
        return await message.reply_text("Task found, but its status has expired. It might have finished or been cancelled.")

    start_time = sts.get('start')
    text, buttons = progress_message_content(sts, start_time, task_id)
    
    await message.reply_text(
        text,
        reply_markup=buttons
    )
