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
    if not bots: return await message.reply("You haven't added any bots or userbots. Please add one in `/settings` to act as the **Fetcher**.")

    if len(bots) == 1:
        temp.USER_STATES[user_id] = {'bot_id': bots[0]['id']}
        await prompt_target_channel(bot, message)
    else:
        buttons = [[InlineKeyboardButton(b['name'], callback_data=f"fwd_select_fetcher_{b['id']}")] for b in bots]
        buttons.append([InlineKeyboardButton("« Cancel", callback_data="close_btn")])
        await message.reply("<b>Step 1: Select the Fetcher</b>\n\nThis bot/userbot will be used to read messages from the source channel.", reply_markup=InlineKeyboardMarkup(buttons))

@Client.on_callback_query(filters.regex(r'^fwd_select_fetcher_'))
async def cb_select_fetcher(bot, query):
    user_id = query.from_user.id
    bot_id = int(query.data.split('_')[-1])
    temp.USER_STATES[user_id] = {'bot_id': bot_id}
    await query.message.delete()
    await prompt_target_channel(bot, query.message)

async def prompt_target_channel(bot, message):
    channels = await db.get_user_channels(message.chat.id)
    if not channels: return await message.reply("You haven't added any target channels. Please add one in `/settings`.")
    
    buttons = [[InlineKeyboardButton(c['title'], callback_data=f"fwd_target_{c['chat_id']}")] for c in channels]
    buttons.append([InlineKeyboardButton("« Cancel", callback_data="close_btn")])
    await message.reply("<b>Step 2: Select the Target Channel</b>", reply_markup=InlineKeyboardMarkup(buttons))

@Client.on_callback_query(filters.regex(r'^fwd_target_'))
async def cb_select_target(bot, query):
    user_id = query.from_user.id
    state = temp.USER_STATES.get(user_id)
    if not state or 'bot_id' not in state:
        return await query.message.edit("Error: Fetcher Bot selection was lost. Please start over with /forward.")

    state['to_chat_id'] = int(query.data.split('_')[-1])
    prompt = await query.message.edit_text(Translation.FROM_MSG)
    state['state'] = "awaiting_source"
    state['prompt_message_id'] = prompt.id

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
        bot_id = state.get("bot_id")
        temp.USER_STATES.pop(user_id, None)
        if error: return await message.reply(error)
        
        from_title = "Private Chat"
        try:
            if not bot_id: return await message.reply("⚠️ Error: Fetcher Bot selection lost. Please start over.")
            bot_config = await db.get_bot(user_id, bot_id)
            if not bot_config: return await message.reply("⚠️ Error: Selected Fetcher Bot not found in database.")
            
            async with CLIENT().client(bot_config) as temp_client:
                from_title = (await temp_client.get_chat(from_chat)).title
        except PeerIdInvalid:
             return await message.reply("⚠️ **Access Error:** The selected Fetcher Bot/Userbot is not a member of the source channel. Please add it and try again.")
        except Exception as e:
            logger.error(f"Could not get chat title for {from_chat}. Error: {e}", exc_info=True)
        
        await start_range_selection(bot, message, from_chat, from_title, state["to_chat_id"], 1, end_id, bot_id=bot_id)

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
        if await CLIENT().add_bot(message):
            await show_settings_menu(bot, message, 'bots')
            
    elif state_type == "awaiting_user_session":
        temp.USER_STATES.pop(user_id, None)
        if await CLIENT().add_session(message):
            await show_settings_menu(bot, message, 'bots')

    elif state_type == "awaiting_worker_bot_token":
        temp.USER_STATES.pop(user_id, None)
        if await CLIENT().add_worker_bot(message):
            await show_settings_menu(bot, message, 'workers')

    elif state_type == "awaiting_channel_forward":
        temp.USER_STATES.pop(user_id, None)
        if message.forward_from_chat:
            await db.add_channel(user_id, message.forward_from_chat.id, message.forward_from_chat.title, message.forward_from_chat.username)
            await message.reply("✅ Channel added.")
            await show_settings_menu(bot, message, 'channels')
        else:
            await message.reply("That was not a valid forwarded message. Please try again.")
            await show_settings_menu(bot, message, 'channels')

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
            await pre_flight_check_and_ask_for_workers(bot, query, session_id)

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

async def pre_flight_check_and_ask_for_workers(bot, query, session_id):
    user_id = query.from_user.id
    try:
        session = temp.RANGE_SESSIONS.get(session_id)
        if not session:
            return await bot.send_message(user_id, "⚠️ Your session has expired. Please start over with /forward.")

        workers = await db.get_worker_bots(user_id)
        manager = await db.get_manager_userbot(user_id)
        
        manager_status = f"✅ `{manager['name']}`" if manager else "⚠️ **Not Set**"
        worker_status = f"✅ `{len(workers)}` Found" if workers else "⚠️ **None Found**"
        
        report = (
            "**Step 3: Pre-Flight Check**\n\n"
            "This is your current forwarding setup:\n\n"
            f"● **Manager Userbot:** {manager_status}\n"
            f"● **Worker Bots:** {worker_status}"
        )
        
        if manager and workers:
            report += "\n\n✅ **System Ready.** Please select the number of workers to use for this task."
            buttons = [[InlineKeyboardButton(str(i), callback_data=f"fwd_workers_{session_id}_{i}")] for i in range(1, len(workers) + 1)]
            grid = [buttons[i:i + 5] for i in range(0, len(buttons), 5)]
            grid.append([InlineKeyboardButton("✨ Use All Workers", callback_data=f"fwd_workers_{session_id}_{len(workers)}")])
            grid.append([InlineKeyboardButton("❌ Cancel", callback_data="close_btn")])
            await bot.send_message(user_id, report, reply_markup=InlineKeyboardMarkup(grid))
        else:
            report += "\n\n❌ **Setup Incomplete.** Please configure the missing items in /settings before proceeding."
            await bot.send_message(user_id, report)

    except Exception as e:
        logger.error(f"Error in pre_flight_check: {e}", exc_info=True)
        await bot.send_message(user_id, f"An unexpected error occurred while checking your setup: `{e}`")

@Client.on_callback_query(filters.regex(r"^fwd_workers_"))
async def cb_select_workers(bot, query):
    _, session_id, num_workers = query.data.split("_")
    await query.message.delete()
    await show_final_confirmation(bot, query, session_id, int(num_workers))

async def show_final_confirmation(bot, query, session_id, num_workers):
    user_id = query.from_user.id
    session = temp.RANGE_SESSIONS.get(session_id)
    if not session: return await bot.send_message(user_id, "⚠️ Your session has expired. Please start over.")
    
    bot_id = session.get('bot_id')
    if not bot_id: return await bot.send_message(user_id, "⚠️ Fetcher Bot selection lost. Please start over.")
    
    _bot = await db.get_bot(user_id, bot_id)
    to_title = (await db.get_channel_details(user_id, session['to_chat_id']))['title']
    
    forward_id = generate_short_id()
    session['num_workers'] = num_workers
    temp.FORWARD_SESSIONS[forward_id] = temp.RANGE_SESSIONS.pop(session_id)
    
    STS(forward_id).store(From=session['from_chat_id'], to=session['to_chat_id'], start_id=session['start_id'], end_id=session['end_id'])
    
    await bot.send_message(user_id, Translation.DOUBLE_CHECK.format(
        botname=_bot['name'], botuname=_bot.get('username', 'N/A'), from_chat=session['from_title'], to_chat=to_title,
        message_range=f"{min(session['start_id'], session['end_id'])} to {max(session['start_id'], session['end_id'])}") + 
        f"\n\n**Worker Bots to use:** `{num_workers}`",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton('✓ Yes, Start Forwarding', callback_data=f"start_public_{forward_id}")],
            [InlineKeyboardButton('« No, Cancel', callback_data="close_btn")]
        ]))

@Client.on_callback_query(filters.regex(r'^close_btn$'))
async def close_callback(bot, query):
    await query.message.delete()
