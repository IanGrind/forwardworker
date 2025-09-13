import time
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup
from config import temp
from database import db
from .utils import STS, get_readable_time, progress_message_content
from translation import Translation


@Client.on_message(filters.private & filters.command(['tasks']))
async def tasks_command(client, message):
    user_id = message.from_user.id
    active_task = temp.ACTIVE_TASKS.get(user_id)
    
    if not active_task:
        return await message.reply_text("You have no active tasks.")
        
    for task_id, task_data in active_task.items():
        sts = STS(task_id).get(full=True)
        if not sts: continue

        start_time = task_data.get("start_time", sts.get('start'))
        text, buttons = progress_message_content(sts, start_time, task_id, done=False)
        
        await message.reply_text(
            f"**Active Task Found:**\n\n{text}",
            reply_markup=buttons
        )

@Client.on_message(filters.private & filters.command(["forwardelay", "fd"]))
async def forward_delay(client: Client, message: Message):
    """Handler for the /forwardelay command."""
    user_id = message.from_user.id
    
    # Use the helper function to update configs
    from .settings import update_configs, get_configs

    user_configs = await get_configs(user_id)
    delay = user_configs.get('forward_delay', 0.5)

    if len(message.command) < 2:
        return await message.reply_text(
            Translation.FORWARDELAY_TXT.format(current_delay=delay)
        )
    
    try:
        new_delay = float(message.command[1])
        if new_delay < 0:
            return await message.reply_text("Delay must be a positive number (e.g., 0.5, 1, 2).")
        
        await update_configs(user_id, 'forward_delay', new_delay)
        
        await message.reply_text(f"✅ Forward delay updated to **{new_delay}** seconds.")

    except ValueError:
        await message.reply_text("Invalid input. Please provide a number for the delay.")
    except Exception as e:
        await message.reply_text(f"An error occurred: {e}")
