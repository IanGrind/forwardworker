# iangrind/forwardworker/forwardworker-1ff680b8c32922eb74e103a193e108a8d299c7bc/plugins/delay.py

import asyncio
from pyrogram import Client, filters
from pyrogram.types import Message
from database import db
from translation import Translation
from .utils import update_configs

@Client.on_message(filters.private & filters.command(["forwardelay", "fd"]))
async def forward_delay(client: Client, message: Message):
    user_id = message.from_user.id
    
    ban_status = await db.get_ban_status(user_id)
    if ban_status["is_banned"]:
        return await message.reply_text(f"Access denied.\n\nReason: {ban_status['ban_reason']}")

    user_configs = await db.get_configs(user_id)
    current_delay = user_configs.get('forward_delay', 0.5)

    if len(message.command) < 2:
        return await message.reply_text(Translation.FORWARDELAY_TXT.format(current_delay=current_delay))
    
    try:
        delay_value = float(message.command[1])
        if delay_value < 0:
            return await message.reply_text("The delay must be a positive number.")
        
        # Update the configuration
        await update_configs(user_id, 'forward_delay', delay_value)
        
        # Verify the update by reading the value back from the database
        new_configs = await db.get_configs(user_id)
        updated_delay = new_configs.get('forward_delay')

        if updated_delay == delay_value:
            await message.reply_text(f"✅ Forwarding delay has been successfully updated to **{updated_delay} seconds**.")
        else:
            await message.reply_text(f"⚠️ **Error:** Failed to update the delay. The current delay is still **{current_delay} seconds**.")

    except ValueError:
        await message.reply_text("Invalid input. Please provide a number (e.g., `0.5`, `1`, `2`).")
    except Exception as e:
        await message.reply_text(f"An error occurred: {e}")
