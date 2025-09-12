import re
import asyncio
import logging
import random
import string
from uuid import uuid4
from .utils import STS, start_range_selection, update_range_message
from database import db
from config import temp
from translation import Translation
from .test import CLIENT
from .settings import show_settings_menu
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery, Message
from pyrogram.errors import PeerIdInvalid

logger = logging.getLogger(__name__)

def generate_short_id(length=8):
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=length))

def parse_message_input(message):
    if not message or (not message.text and not message.forward_date): return None, None, "Invalid input: Send a message link or forward a message."
    if message.text and not message.forward_date:
        match = re.match(r"(https://)?t\.me/(c/)?(\d+|[a-zA-Z_0-9]+)/(\d+)", message.text.replace("?single", ""))
        if not match: return None, None, 'Invalid Link Format.'
        chat_id = f"-100{match.group(3)}" if match.group(3).isdigit() else match.group(3)
        return chat_id, int(match.group(4)), None
    elif message.forward_from_chat: return message.forward_from_chat.id, message.forward_from_message_id, None
    return None, None, "Could not identify the source. Please send a valid message link or forward a message from the source chat."

@Client.on_message(filters.private & filters.command(["fwd", "forward"]))
async def run(bot, message):
    user_id = message.from_user.id
    if temp.lock.get(user_id): return await message.reply("A task is in progress.")
    temp.USER_STATES.pop(user_id, None)
    
    bots = await db.get_bots(user_id)
    if not bots: return await message.reply("You haven't added any bots or userbots. These will act as your **Operators**. Please add at least one in `/settings`.")

    await prompt_target_channel(bot, message)

async def prompt_target_channel(bot, message):
    channels = await db.get_user_channels(message.chat.id)
    if not channels: return await message.reply("You haven't added any target channels. Please add one in `/settings`.")
    
    buttons = [[InlineKeyboardButton(c['title'], callback_data=f"fwd_target_{c['chat_id']}")] for c in channels]
    buttons.append([InlineKeyboardButton("« Cancel", callback_data="close_btn")])
    await message.reply("<b>Step 1: Select the Target Channel</b>", reply_markup=InlineKeyboardMarkup(buttons))

@Client.on_callback_query(filters.regex(r'^fwd_target_'))
async def cb_select_target(bot, query):
    user_id = query.from_user.id
    prompt = await query.message.edit_text(Translation.FROM_MSG)
    temp.USER_STATES[user_id] = {
        "state": "awaiting_source",
        "to_chat_id": int(query.data.split('_')[-1]),
        "prompt_message_id": prompt.id
    }

@Client.on_message(filters.private & filters.incoming, group=-1)
async def stateful_message_handler(bot: Client, message: Message):
    user_id = message.from_user.id
    state = temp.USER_STATES.get(user_id)
    if not state: return

    if message.text and message.text.lower() == "/cancel":
        if state.get("prompt_message_id"):
            try: await bot.delete_messages(user_id, state["prompt_message_id"])
            except: pass
        temp.USER_STATES.pop(user_id, None)
        return await message.reply(Translation.CANCEL)

    state_type = state.get("state")
    if state.get("prompt_message_id"):
        try: await bot.delete_messages(user_id, state["prompt_message_id"])
        except: pass
    
    if state_type == "awaiting_source":
        from_chat, end_id, error = parse_message_input(message)
        to_chat_id = state.get("to_chat_id")
        temp.USER_STATES.pop(user_id, None)
        if error: return await message.reply(error)
        
        # We need a temporary client just to get the title
        bots = await db.get_bots(user_id)
        from_title = "Private Chat"
        try:
            async with CLIENT.client(bots[0]) as temp_client:
                from_title = (await temp_client.get_chat(from_chat)).title
        except Exception as e:
            logger.warning(f"Could not get chat title with first bot. This is non-critical. Error: {e}")
        
        await start_range_selection(bot, message, from_chat, from_title, to_chat_id, 1, end_id)

    elif state_type == "awaiting_range_edit":
        session_id = state.get("session_id")
        part_to_edit = state.get("part_to_edit")
        temp.USER_STATES.pop(user_id, None)
        try: await message.delete()
        except: pass
        session = temp.RANGE_SESSIONS.get(session_id)
        if not session: return
        if not message.text or not message.text.isdigit():
            await bot.send_message(user_id, "Invalid ID. Please send only numbers.")
            return await update_range_message(bot, session_id)
        session[f"{part_to_edit}_id"] = int(message.text)
        await update_range_message(bot, session_id)
        
    elif state_type == "awaiting_bot_token":
        temp.USER_STATES.pop(user_id, None)
        if await CLIENT.add_bot(message):
            await show_settings_menu(bot, message, 'bots')
            
    elif state_type == "awaiting_user_session":
        temp.USER_STATES.pop(user_id, None)
        if await CLIENT.add_session(message):
            await show_settings_menu(bot, message, 'bots')

@Client.on_callback_query(filters.regex(r"^(range_|noop)"))
async def range_menu_handler(bot: Client, query: CallbackQuery):
    user_id = query.from_user.id
    
    if query.data == "noop":
        return await query.answer()

    try:
        parts = query.data.split('_')
        action = parts[1]
        session_id = parts[-1]

        session = temp.RANGE_SESSIONS.get(session_id)
        if not session or session.get('user_id') != user_id:
            return await query.answer("This menu is not for you, or the session has expired.", show_alert=True)

        if action == "confirm":
            await query.message.delete()
            await show_final_confirmation(bot, query, session_id)

        elif action == "cancel":
            temp.RANGE_SESSIONS.pop(session_id, None)
            await query.message.delete()
            await bot.send_message(user_id, "Operation cancelled.")

        elif action == "edit":
            part_to_edit = parts[2]
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

    except Exception as e:
        logger.error(f"Error in range_menu_handler: {e}", exc_info=True)
        await query.answer("An error occurred.", show_alert=True)


async def show_final_confirmation(bot, query, session_id):
    user_id = query.from_user.id
    session = temp.RANGE_SESSIONS.get(session_id)
    if not session: return await bot.send_message(user_id, "⚠️ Your session has expired. Please start over.")
    
    operators = await db.get_bots(user_id)
    to_title = (await db.get_channel_details(user_id, session['to_chat_id']))['title']
    
    forward_id = generate_short_id()
    temp.FORWARD_SESSIONS[forward_id] = temp.RANGE_SESSIONS.pop(session_id)
    
    STS(forward_id).store(From=session['from_chat_id'], to=session['to_chat_id'], start_id=session['start_id'], end_id=session['end_id'])
    
    # We just use the first operator's name for the display message
    operator_name = operators[0]['name'] if operators else "N/A"
    
    await bot.send_message(user_id, f"<b>Final Check</b>\n\n"
        f"● <b>Source:</b> `{session['from_title']}`\n"
        f"● <b>Target:</b> `{to_title}`\n"
        f"● <b>Range:</b> `{min(session['start_id'], session['end_id'])}` to `{max(session['start_id'], session['end_id'])}`\n"
        f"● <b>Operators:</b> `{len(operators)}` bots/userbots will be used for this task.\n\n"
        f"<i>Ensure all Operators are members of the source channel and admins in the target channel.</i>",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton('✓ Yes, Start Forwarding', callback_data=f"start_public_{forward_id}")],
            [InlineKeyboardButton('« No, Cancel', callback_data="close_btn")]
        ]))

@Client.on_callback_query(filters.regex(r'^close_btn$'))
async def close_callback(bot, query):
    await query.message.delete()
