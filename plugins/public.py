import re
import asyncio
import logging
import random
from uuid import uuid4
from .utils import STS, start_range_selection, update_range_message
from database import db
from config import temp
from translation import Translation
from .test import CLIENT
# --- Import the necessary function from unequify ---
from .unequify import process_unequify_target
from pyrogram import Client, filters, enums
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery, Message

logger = logging.getLogger(__name__)

# --- Helper Functions ---
def parse_message_input(message):
    """Parses a forwarded message or a message link."""
    if not message or (not message.text and not message.forward_date):
        return None, None, "Invalid input. A message link or forwarded message is required."

    if message.text and not message.forward_date:
        regex = re.compile(r"(https://)?(t\.me/|telegram\.me/|telegram\.dog/)(c/)?(\d+|[a-zA-Z_0-9]+)/(\d+)$")
        match = regex.match(message.text.replace("?single", ""))
        if not match:
            return None, None, 'Invalid Link.'
        chat_id_str, msg_id = match.group(4), int(match.group(5))
        chat_id = int(("-100" + chat_id_str)) if chat_id_str.isnumeric() else chat_id_str
        return chat_id, msg_id, None
    elif message.forward_from_chat and message.forward_from_chat.type == enums.ChatType.CHANNEL:
        msg_id, chat_id = message.forward_from_message_id, message.forward_from_chat.username or message.forward_from_chat.id
        return chat_id, msg_id, None
    else:
        return None, None, "Invalid input. Please forward from a channel or provide a valid message link."

# --- Main /forward Command Flow ---

@Client.on_message(filters.private & filters.command(["fwd", "forward"]))
async def run(bot, message):
    user_id = message.from_user.id
    if temp.lock.get(user_id):
        return await message.reply("A task is already in progress. Please wait for it to complete before starting a new one.")
    
    temp.USER_STATES.pop(user_id, None)

    bots = await db.get_bots(user_id)
    if not bots:
        return await message.reply("Add a bot or userbot to proceed. ( >⁠.⁠< ) --> /settings")

    if len(bots) == 1:
        temp.FORWARD_BOT_ID[user_id] = bots[0]['id']
        await prompt_target_channel(bot, message)
    else:
        buttons = [[InlineKeyboardButton(b.get('name') or f"ID: {b['id']}", callback_data=f"fwd_select_bot_{b['id']}")] for b in bots]
        buttons.append([InlineKeyboardButton("« Cancel", callback_data="close_btn")])
        await message.reply("<b>Select a Bot or Userbot</b>", reply_markup=InlineKeyboardMarkup(buttons))

@Client.on_callback_query(filters.regex(r'^fwd_select_bot_'))
async def cb_select_bot(bot, query):
    bot_id = int(query.data.split('_')[-1])
    temp.FORWARD_BOT_ID[query.from_user.id] = bot_id
    await query.message.delete()
    await prompt_target_channel(bot, query.message)

async def prompt_target_channel(bot, message):
    user_id = message.chat.id
    channels = await db.get_user_channels(user_id)
    if not channels:
       return await message.reply("Add a target channel first. ( >⁠.⁠< ) --> /settings")

    buttons = [[InlineKeyboardButton(c['title'], callback_data=f"fwd_target_{c['chat_id']}")] for c in channels]
    buttons.append([InlineKeyboardButton("« Cancel", callback_data="close_btn")])
    await message.reply(Translation.TO_MSG, reply_markup=InlineKeyboardMarkup(buttons))

@Client.on_callback_query(filters.regex(r'^fwd_target_'))
async def cb_select_target(bot, query):
    user_id = query.from_user.id
    to_chat_id = int(query.data.split('_')[-1])
    
    prompt_message = await query.message.edit_text(Translation.FROM_MSG)
    temp.USER_STATES[user_id] = {
        "state": "awaiting_source",
        "to_chat_id": to_chat_id,
        "prompt_message_id": prompt_message.id
    }

# --- High-Priority Stateful Message Handler ---
@Client.on_message(filters.private & filters.incoming, group=-1)
async def stateful_message_handler(bot: Client, message: Message):
    if message.edit_date: return

    user_id = message.from_user.id
    state_info = temp.USER_STATES.get(user_id)
    if not state_info: return

    if message.text and message.text.lower() == "/cancel":
        prompt_id = state_info.get("prompt_message_id")
        if prompt_id:
            try: await bot.delete_messages(user_id, prompt_id)
            except Exception: pass
        temp.USER_STATES.pop(user_id, None)
        return await message.reply(Translation.CANCEL)

    current_state = state_info.get("state")

    if current_state == "awaiting_source":
        try: await bot.delete_messages(user_id, state_info["prompt_message_id"])
        except Exception: pass
        
        to_chat_id = state_info["to_chat_id"]
        from_chat_id, end_id, error = parse_message_input(message)
        
        temp.USER_STATES.pop(user_id, None)
        if error: return await message.reply(error)

        try: from_title = (await bot.get_chat(from_chat_id)).title
        except Exception: from_title = "Private/Unknown Chat"
        
        await start_range_selection(bot, message, from_chat_id, from_title, to_chat_id, 1, end_id)

    elif current_state == "awaiting_range_edit":
        prompt_id = state_info.get("prompt_message_id")
        session_id = state_info.get("session_id")
        value_type = state_info.get("value_type")
        session = temp.RANGE_SESSIONS.get(session_id)

        try: await bot.delete_messages(user_id, prompt_id)
        except Exception: pass
        
        temp.USER_STATES.pop(user_id, None)

        if not session: return await message.reply("Your session has expired. Please start over.")

        if message.text and message.text.isdigit():
            session[f'{value_type}_id'] = int(message.text)
            await update_range_message(bot, session_id, message_to_edit=message)
        else:
            await message.reply("Invalid ID provided. The process has been cancelled.")
            temp.RANGE_SESSIONS.pop(session_id, None)

    elif current_state == "awaiting_channel_forward":
        try: await bot.delete_messages(user_id, state_info["prompt_message_id"])
        except Exception: pass

        temp.USER_STATES.pop(user_id, None)
        
        if not message.forward_date:
            return await message.reply("Not a forwarded message. (・_・;)\nProcess cancelled.")

        chat_id, title = message.forward_from_chat.id, message.forward_from_chat.title
        username = f"@{message.forward_from_chat.username}" if message.forward_from_chat.username else "private"

        if await db.in_channel(user_id, chat_id):
            await message.reply("This channel has already been added.")
        else:
            await db.add_channel(user_id, chat_id, title, username)
            await message.reply("Channel added. ✓")
    
    elif current_state == "awaiting_bot_token":
        temp.USER_STATES.pop(user_id, None)
        await CLIENT().add_bot(bot, message)

    elif current_state == "awaiting_user_session":
        temp.USER_STATES.pop(user_id, None)
        await CLIENT().add_session(bot, message)
    
    elif current_state == "awaiting_worker_bot_token":
        temp.USER_STATES.pop(user_id, None)
        await CLIENT().add_worker_bot(bot, message) # You'll need to create this method in test.py


    # --- FIXED UNEQIFY LOGIC ---
    elif current_state == "awaiting_unequify_manual_target":
        prompt_id = state_info.get("prompt_message_id")
        if prompt_id:
            try: await bot.delete_messages(user_id, prompt_id)
            except: pass
        
        userbot_id = temp.UNEQUIFY_USERBOT_ID.get(user_id)
        if not userbot_id: return await message.reply("Error: Userbot selection lost. Please start over.")
        
        temp.USER_STATES.pop(user_id, None)
        await process_unequify_target(bot, message, user_id, userbot_id, message.text)

    elif current_state == "awaiting_unequify_chat_selection":
        prompt_message = state_info.get("prompt_message")
        if prompt_message:
            try: await prompt_message.delete()
            except: pass
        
        chats = state_info.get("chats", {})
        user_input = message.text.strip()
        selected_chat = chats.get(user_input)

        if not selected_chat:
            temp.USER_STATES.pop(user_id, None)
            return await message.reply("Invalid selection. Please start the /unequify process again.")
        
        userbot_id = temp.UNEQUIFY_USERBOT_ID.get(user_id)
        if not userbot_id: return await message.reply("Error: Userbot selection lost. Please start over.")

        temp.USER_STATES.pop(user_id, None)
        await process_unequify_target(bot, message, user_id, userbot_id, selected_chat.id)


# --- Callbacks for Interactive Range Selection ---
@Client.on_callback_query(filters.regex(r"^range_"))
async def range_selection_callbacks(bot, query):
    user_id = query.from_user.id
    parts = query.data.split("_")
    action, session_id = parts[1], parts[-1]

    session = temp.RANGE_SESSIONS.get(session_id)
    if not session or session['user_id'] != user_id:
        return await query.answer("This is not for you, or the session has expired.", show_alert=True)

    if action == "info":
        await query.answer("Displays the current range and order selection.", show_alert=False)
    
    elif action == "cancel":
        temp.RANGE_SESSIONS.pop(session_id, None)
        await query.message.delete()
        await bot.send_message(user_id, "Operation cancelled.")
    
    elif action == "swap":
        session['order'] = 'desc' if session['order'] == 'asc' else 'asc'
        session['start_id'], session['end_id'] = session['end_id'], session['start_id']
        await update_range_message(bot, session_id, message_to_edit=query.message)
        await query.answer(f"Order swapped!")
    
    elif action == "edit":
        value_type = parts[2]
        await query.message.delete()
        prompt = await bot.send_message(user_id, f"Send the new **{value_type.upper()} ID**.")
        # SET THE STATE to wait for the user's next message
        temp.USER_STATES[user_id] = {
            "state": "awaiting_range_edit",
            "session_id": session_id,
            "value_type": value_type,
            "prompt_message_id": prompt.id
        }
    
    elif action == "confirm":
        await query.message.delete()
        final_callback = session.get('final_callback')
        if final_callback == 'fwd_final':
            await ask_for_workers(bot, session_id)
        elif final_callback == 'uneq_final':
            from plugins.unequify import prompt_type_selection
            await prompt_type_selection(bot, query, session_id)


async def ask_for_workers(bot, session_id):
    session = temp.RANGE_SESSIONS.get(session_id)
    if not session: return

    user_id = session['user_id']
    worker_bots = await db.get_worker_bots(user_id)

    if not worker_bots:
        return await show_final_confirmation(bot, session_id, 0)

    buttons = []
    for i in range(1, len(worker_bots) + 1):
        buttons.append(InlineKeyboardButton(str(i), callback_data=f"select_workers_{session_id}_{i}"))

    # create a grid of buttons
    buttons = [buttons[i:i + 5] for i in range(0, len(buttons), 5)]
    buttons.append([InlineKeyboardButton("Skip", callback_data=f"select_workers_{session_id}_0")])
    buttons.append([InlineKeyboardButton("« Cancel", callback_data="close_btn")])

    await bot.send_message(
        user_id,
        "How many worker bots do you want to use for this forward? Using more workers can significantly reduce floodwaits.",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

@Client.on_callback_query(filters.regex(r"^select_workers_"))
async def select_workers_callback(bot, query):
    session_id, num_workers = query.data.split("_")[2:]
    num_workers = int(num_workers)
    
    await query.message.delete()
    await show_final_confirmation(bot, session_id, num_workers)


async def show_final_confirmation(bot, session_id, num_workers):
    session = temp.RANGE_SESSIONS.get(session_id)
    if not session: return

    user_id, bot_id = session['user_id'], temp.FORWARD_BOT_ID.get(session['user_id'])
    if not bot_id: return await bot.send_message(user_id, "Error: Bot selection lost.")

    _bot, channels = await db.get_bot(user_id, bot_id), await db.get_user_channels(user_id)
    to_title = next((c['title'] for c in channels if c['chat_id'] == session['to_chat_id']), 'Unknown')
    
    message_range_text = f"{min(session['start_id'], session['end_id'])} to {max(session['start_id'], session['end_id'])}"
    forward_id = str(uuid4())

    STS(forward_id).store(From=session['from_chat_id'], to=session['to_chat_id'], start_id=session['start_id'], end_id=session['end_id'])
    
    temp.RANGE_SESSIONS[session_id]['num_workers'] = num_workers

    await bot.send_message(user_id, Translation.DOUBLE_CHECK.format(
            botname=_bot.get('name', 'N/A'), botuname=_bot.get('username', ''),
            from_chat=session['from_title'], to_chat=to_title, message_range=message_range_text) + f"\n\n**Worker Bots:** `{num_workers}`",
        disable_web_page_preview=True,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton('✓ Yes, Start Forwarding', callback_data=f"start_public_{forward_id}_{session_id}")],
            [InlineKeyboardButton('« No, Cancel', callback_data="close_btn")]
        ]))
    # temp.RANGE_SESSIONS.pop(session_id, None) # Don't pop here, we need it in regix.py

@Client.on_callback_query(filters.regex(r'^close_btn$'))
async def close_callback(bot, query):
    user_id = query.from_user.id
    if not temp.lock.get(user_id):
        temp.USER_STATES.pop(user_id, None)
    await query.message.delete()
