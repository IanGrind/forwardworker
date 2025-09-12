import asyncio
import logging
from pyrogram import Client, filters
from pyrogram.types import Message
from database import db
from translation import Translation
from .utils import update_configs

logger = logging.getLogger(__name__)

@Client.on_message(filters.private & filters.command("pingdelay"))
async def ping_delay_command(client: Client, message: Message):
    """A simple command to confirm this plugin file is loaded and responsive."""
    await message.reply_text("✅ Pong from delay plugin! The file is loaded correctly.")

@Client.on_message(filters.private & filters.command(["forwardelay", "fd"]))
async def forward_delay(client: Client, message: Message):
    user_id = message.from_user.id
    processing_msg = await message.reply_text("`Processing...`")

    try:
        logger.info(f"User {user_id} triggered /forwardelay.")
        
        ban_status = await db.get_ban_status(user_id)
        if ban_status and ban_status.get("is_banned"):
            return await processing_msg.edit(f"Access denied.\n\nReason: {ban_status['ban_reason']}")

        logger.info(f"User {user_id} is not banned. Fetching current configs.")
        user_configs = await db.get_configs(user_id)
        current_delay = user_configs.get('forward_delay', 0.5)

        if len(message.command) < 2:
            logger.info(f"Showing current delay of {current_delay}s to user {user_id}.")
            return await processing_msg.edit(Translation.FORWARDELAY_TXT.format(current_delay=current_delay))
        
        try:
            delay_value = float(message.command[1])
            if delay_value < 0:
                return await processing_msg.edit("The delay must be a positive number.")
        except ValueError:
            return await processing_msg.edit("Invalid input. Please provide a number (e.g., `0.5`, `1`, `2`).")

        logger.info(f"Attempting to set forward_delay to {delay_value} for user {user_id}.")
        await update_configs(user_id, 'forward_delay', delay_value)
        logger.info(f"Database update call finished for user {user_id}. Verifying change.")

        # Verify the update by reading the value back
        new_configs = await db.get_configs(user_id)
        updated_delay = new_configs.get('forward_delay')
        logger.info(f"Verification read: New delay is {updated_delay} for user {user_id}.")

        if updated_delay == delay_value:
            await processing_msg.edit(f"✅ Forwarding delay has been successfully updated to **{updated_delay} seconds**.")
        else:
            await processing_msg.edit(f"⚠️ **Error:** Failed to update the delay. The current delay is still **{current_delay} seconds**.")

    except Exception as e:
        logger.error(f"Error in /forwardelay for user {user_id}: {e}", exc_info=True)
        await processing_msg.edit(f"An unexpected error occurred: `{e}`")
