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
from .unequify import process_unequify_target
from pyrogram import Client, filters, enums
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
    elif message.forward_from:
         return None, None, "Cannot forward from this source. It may be a restricted channel or a user who has hidden their account."
    return None, None, "Could not identify the source. Please send a valid message link or forward a message from the source chat."

@Client.on_message(filters.private & filters.command(["fwd", "forward"]))
async def run(bot, message):
    user_id = message.from_user.id
    if temp.lock.get(user_id): return await message.reply("A task is in progress.")
    temp.USER_STATES.pop(user_id, None)
    bots = await db.get_bots(user_id)
    if not bots: return await message.reply("Add a bot or userbot in /settings.")
    if len(bots) == 1:
        temp.FORWARD_BOT_ID[user_id] = bots[0]['id']
        await prompt_target_channel(bot, message)
    else:
        buttons = [[InlineKeyboardButton(b['name'], callback_data=f"fwd_select_bot_{b['id']}")] for b in bots]
        buttons.append([InlineKeyboardButton("« Cancel", callback_data="close_btn")])
        await message.reply("<b>Select a Bot or Userbot to act as the Fetcher:</b>", reply_markup=InlineKeyboardMarkup(buttons))

@Client.on_callback_query(filters.regex(r'^fwd_select_bot_'))
async def cb_select_bot(bot, query):
    temp.FORWARD_BOT_ID[query.from_user.id] = int(query.data.split('_')[-1])
    await query.message.delete()
    await prompt_target_channel(bot, query.message)

async def prompt_target_channel(bot, message):
    channels = await db.get_user_channels(message.chat.id)
    if not channels: return await message.reply("Add a target channel in /settings.")
    buttons = [[InlineKeyboardButton(c['title'], callback_data=f"fwd_target_{c['chat_id']}")] for c in channels]
    buttons.append([InlineKeyboardButton("« Cancel", callback_data="close_btn")])
    await message.reply(Translation.TO_MSG, reply_markup=InlineKeyboardMarkup(buttons))

@Client.on_callback_query(filters.regex(r'^fwd_target_'))
async def cb_select_target(bot, query):
    prompt = await query.message.edit_text(Translation.FROM_MSG)
    temp.USER_STATES[query.from_user.id] = {"state": "awaiting_source", "to_chat_id": int(query.data.split('_')[-1]), "prompt_message_id": prompt.id}

@Client.on_message(filters.private & filters.incoming, group=-1)
async def stateful_message_handler(bot: Client, message: Message):
    user_id = message.from_user.id
    state = temp.USER_STATES.get(user_id)
    if not state:
        return

    if message.text and message.text.lower() == "/cancel":
        prompt_id = state.get("prompt_message_id")
        if prompt_id:
            try: await bot.delete_messages(user_id, prompt_id)
            except Exception: pass
        temp.USER_STATES.pop(user_id, None)
        return await message.reply(Translation.CANCEL)

    state_type = state.get("state")
    prompt_message_id = state.get("prompt_message_id")
    if prompt_message_id:
        try: await bot.delete_messages(user_id, prompt_message_id)
        except Exception: pass
    
    if state_type == "awaiting_source":
        from_chat, end_id, error = parse_message_input(message)
        temp.USER_STATES.pop(user_id, None)
        if error: return await message.reply(error)
        
        from_title = "Private Chat"
        try:
            bot_id = temp.FORWARD_BOT_ID.get(user_id)
            if not bot_id: return await message.reply("⚠️ Error: Bot selection lost. Please start over.")
            bot_config = await db.get_bot(user_id, bot_id)
            if not bot_config: return await message.reply("⚠️ Error: Selected bot not found.")
            
            async with CLIENT().client(bot_config) as temp_client:
                from_title = (await temp_client.get_chat(from_chat)).title
        except PeerIdInvalid:
             return await message.reply("⚠️ **Error:** The selected bot/userbot is not in the source channel.")
        except Exception as e:
            logger.error(f"Could not get chat title for {from_chat}. Error: {e}", exc_info=True)
            await message.reply(f"Could not verify source channel. Using default name.\n`{e}`")
        
        await start_range_selection(bot, message, from_chat, from_title, state["to_chat_id"], 1, end_id)

    elif state_type == "awaiting_range_edit":
        session_id = state.get("session_id")
        part_to_edit = state.get("part_to_edit")
        
        temp.USER_STATES.pop(user_id, None)
        try: await message.delete()
        except Exception: pass

        session = temp.RANGE_SESSIONS.get(session_id)
        if not session: return

        if not message.text or not message.text.isdigit():
            await bot.send_message(user_id, "Invalid ID. Please provide only numbers.")
            await update_range_message(bot, session_id) # Resend the menu
            return

        session[f"{part_to_edit}_id"] = int(message.text)
        await update_range_message(bot, session_id)

    elif state_type in ["awaiting_unequify_manual_target", "awaiting_unequify_chat_selection"]:
        userbot_id = temp.UNEQUIFY_USERBOT_ID.get(user_id)
        if not userbot_id:
            temp.USER_STATES.pop(user_id, None)
            return await message.reply("⚠️ Error: Userbot selection lost. Start over with /unequify.")
        
        if state_type == "awaiting_unequify_manual_target":
            target_input = message.text
        else: # awaiting_unequify_chat_selection
            selection = message.text
            chats = state.get("chats", {})
            selected_chat = chats.get(selection) or chats.get(f"-100{selection}")
            if not selected_chat:
                return await message.reply("Invalid selection. Please reply with the number or ID from the list.")
            target_input = selected_chat.id
        
        await process_unequify_target(bot, message, user_id, userbot_id, target_input)
        temp.USER_STATES.pop(user_id, None)
        
    elif state_type == "awaiting_bot_token":
        await CLIENT().add_bot(bot, message)
        temp.USER_STATES.pop(user_id, None)
    elif state_type == "awaiting_user_session":
        await CLIENT().add_session(bot, message)
        temp.USER_STATES.pop(user_id, None)
    elif state_type == "awaiting_worker_bot_token":
        await CLIENT().add_worker_bot(bot, message)
        temp.USER_STATES.pop(user_id, None)
    elif state_type == "awaiting_channel_forward":
        if message.forward_from_chat:
            try:
                await db.add_channel(user_id, message.forward_from_chat.id, message.forward_from_chat.title, message.forward_from_chat.username)
                await message.reply("✅ Channel added successfully.")
            except Exception as e:
                await message.reply(f"⚠️ Error adding channel: {e}")
        else:
            await message.reply("Please forward a message from the channel.")
        temp.USER_STATES.pop(user_id, None)

async def ask_for_workers(bot, query, session_id):
    session = temp.RANGE_SESSIONS.get(session_id)
    if not session: return
    workers = await db.get_worker_bots(query.from_user.id)
    if not workers: return await show_final_confirmation(bot, query, session_id, 0)
    
    buttons = [[InlineKeyboardButton(str(i), callback_data=f"fwd_workers:{session_id}:{i}")] for i in range(1, len(workers) + 1)]
    grid = [buttons[i:i + 5] for i in range(0, len(buttons), 5)]
    grid.append([InlineKeyboardButton("✨ Use All", callback_data=f"fwd_workers:{session_id}:{len(workers)}")])
    grid.append([InlineKeyboardButton("Skip", callback_data=f"fwd_workers:{session_id}:0"), InlineKeyboardButton("« Cancel", callback_data="close_btn")])
    await bot.send_message(query.from_user.id, "How many worker bots to use?", reply_markup=InlineKeyboardMarkup(grid))

@Client.on_callback_query(filters.regex(r"^fwd_workers:"))
async def cb_select_workers(bot, query):
    _, session_id, num_workers = query.data.split(":")
    await query.message.delete()
    await show_final_confirmation(bot, query, session_id, int(num_workers))

async def show_final_confirmation(bot, query, session_id, num_workers):
    user_id = query.from_user.id
    session = temp.RANGE_SESSIONS.get(session_id)
    if not session: return await bot.send_message(user_id, "⚠️ Your session has expired.")
    
    bot_id = temp.FORWARD_BOT_ID.get(user_id)
    _bot = await db.get_bot(user_id, bot_id)
    to_title = (await db.get_channel_details(user_id, session['to_chat_id']))['title']
    
    forward_id = generate_short_id()
    session['num_workers'] = num_workers
    # We move the session data to a new key to avoid conflicts if the user starts another task
    temp.FORWARD_SESSIONS[forward_id] = temp.RANGE_SESSIONS.pop(session_id)
    
    STS(forward_id).store(From=session['from_chat_id'], to=session['to_chat_id'], start_id=session['start_id'], end_id=session['end_id'])
    
    await bot.send_message(user_id, Translation.DOUBLE_CHECK.format(
        botname=_bot['name'], botuname=_bot.get('username', 'N/A'), from_chat=session['from_title'], to_chat=to_title,
        message_range=f"{min(session['start_id'], session['end_id'])} to {max(session['start_id'], session['end_id'])}") + 
        f"\n\n**Worker Bots:** `{num_workers}`",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton('✓ Yes, Start Forwarding', callback_data=f"start_public_{forward_id}")],
            [InlineKeyboardButton('« No, Cancel', callback_data="close_btn")]
        ]))

@Client.on_callback_query(filters.regex(r'^close_btn$'))
async def close_callback(bot, query):
    await query.message.delete()
