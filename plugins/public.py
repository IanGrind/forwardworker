import re
import asyncio
import logging
import random
import string
from .utils import STS, start_range_selection, update_range_message
from database import db
from config import temp
from translation import Translation
from .test import CLIENT
from pyrogram import Client, filters, enums
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery, Message

logger = logging.getLogger(__name__)

def generate_short_id(length=8):
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=length))

def parse_message_input(message):
    if not message or (not message.text and not message.forward_date): return None, None, "Invalid input."
    if message.text and not message.forward_date:
        match = re.match(r"(https://)?t\.me/(c/)?(\d+|[a-zA-Z_0-9]+)/(\d+)", message.text.replace("?single", ""))
        if not match: return None, None, 'Invalid Link.'
        chat_id = f"-100{match.group(3)}" if match.group(3).isdigit() else match.group(3)
        return chat_id, int(match.group(4)), None
    elif message.forward_from_chat: return message.forward_from_chat.id, message.forward_from_message_id, None
    return None, None, "Invalid input."

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
async def stateful_message_handler(bot, message):
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
    if state_type == "awaiting_source":
        try: await bot.delete_messages(user_id, state["prompt_message_id"])
        except: pass
        from_chat, end_id, error = parse_message_input(message)
        temp.USER_STATES.pop(user_id, None)
        if error: return await message.reply(error)
        try: from_title = (await bot.get_chat(from_chat)).title
        except: from_title = "Private Chat"
        await start_range_selection(bot, message, from_chat, from_title, state["to_chat_id"], 1, end_id)
    elif state_type == "awaiting_bot_token": await CLIENT().add_bot(bot, message)
    elif state_type == "awaiting_user_session": await CLIENT().add_session(bot, message)
    elif state_type == "awaiting_worker_bot_token": await CLIENT().add_worker_bot(bot, message)

@Client.on_callback_query(filters.regex(r"^range_confirm_fwd_final_"))
async def range_confirm_callback(bot, query):
    session_id = query.data.split('_')[-1]
    await query.message.delete()
    await ask_for_workers(bot, query, session_id)

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
    temp.RANGE_SESSIONS[forward_id] = session
    
    STS(forward_id).store(From=session['from_chat_id'], to=session['to_chat_id'], start_id=session['start_id'], end_id=session['end_id'])
    
    await bot.send_message(user_id, Translation.DOUBLE_CHECK.format(
        botname=_bot['name'], botuname=_bot['username'], from_chat=session['from_title'], to_chat=to_title,
        message_range=f"{min(session['start_id'], session['end_id'])} to {max(session['start_id'], session['end_id'])}") + 
        f"\n\n**Worker Bots:** `{num_workers}`",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton('✓ Yes, Start Forwarding', callback_data=f"start_public_{forward_id}")],
            [InlineKeyboardButton('« No, Cancel', callback_data="close_btn")]
        ]))

@Client.on_callback_query(filters.regex(r'^close_btn$'))
async def close_callback(bot, query):
    await query.message.delete()
