import logging
from pyrogram import Client, filters
from pyrogram.types import CallbackQuery
from config import temp
from .utils import update_range_message
from .public import ask_for_workers
from .unequify import prompt_type_selection

logger = logging.getLogger(__name__)

@Client.on_callback_query(filters.regex(r"^range_"))
async def range_menu_handler(bot: Client, query: CallbackQuery):
    """Handles all button presses for the interactive range selection menu."""
    user_id = query.from_user.id
    
    try:
        parts = query.data.split('_')
        action = parts[1]
        session_id = parts[-1]

        session = temp.RANGE_SESSIONS.get(session_id)
        if not session or session.get('user_id') != user_id:
            return await query.answer("This menu is not for you, or the session has expired.", show_alert=True)

        if action == "confirm":
            workflow = parts[2]  # e.g., 'fwd' or 'uneq'
            await query.message.delete()
            if workflow == "fwd":
                await ask_for_workers(bot, query, session_id)
            elif workflow == "uneq":
                await prompt_type_selection(bot, query, session_id)

        elif action == "cancel":
            temp.RANGE_SESSIONS.pop(session_id, None)
            await query.message.delete()
            await bot.send_message(user_id, "Operation cancelled.")

        elif action == "edit":
            part_to_edit = parts[2]  # 'start' or 'end'
            prompt_text = f"OK, send the new **{part_to_edit}** message ID.\n\n/cancel to abort."
            prompt_msg = await query.message.edit_text(prompt_text)
            temp.USER_STATES[user_id] = {
                "state": "awaiting_range_edit",
                "session_id": session_id,
                "part_to_edit": part_to_edit,
                "prompt_message_id": prompt_msg.id
            }

        elif action == "swap":
            start, end = session['start_id'], session['end_id']
            session['start_id'] = end
            session['end_id'] = start
            session['order'] = 'desc' if session['order'] == 'asc' else 'asc'
            await update_range_message(bot, session_id, message_to_edit=query.message)
            await query.answer("Order swapped")

        elif action == "info":
            await query.answer("This shows the current range. Use other buttons to edit.", show_alert=True)

    except Exception as e:
        logger.error(f"Error in range_menu_handler: {e}", exc_info=True)
        await query.answer("An error occurred.", show_alert=True)
