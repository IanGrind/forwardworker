# iangrind/forwardworker/forwardworker-1ff680b8c32922eb74e103a193e108a8d299c7bc/plugins/core_commands.py

import os
import sys
import asyncio 
import random
import logging
import re
import string
from uuid import uuid4
from database import db
from config import Config, temp
from translation import Translation
from .utils import update_configs, start_range_selection, update_range_message, STS
from .test import CLIENT
from pyrogram import Client, filters, enums
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, CallbackQuery

SYD = ["https://files.catbox.moe/3lwlbm.png"]
logger = logging.getLogger(__name__)

def generate_short_id(length=8):
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=length))

# --- Start & Main Menu Handlers ---

@Client.on_message(filters.private & filters.command(['start']))
async def start(client, message):
    user = message.from_user
    try:
        if not await db.is_user_exist(user.id):
            await db.add_user(user.id, user.first_name)
    except Exception as e:
        logger.error(f"Error in user registration: {e}", exc_info=True)

    await message.reply_photo(
        photo=random.choice(SYD),
        caption=Translation.START_TXT.format(user.mention),
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton('Help', callback_data='help'),
            InlineKeyboardButton('About', callback_data='about')
        ]])
    )

@Client.on_message(filters.private & filters.command(['restart', "r"]) & filters.user(Config.OWNER_ID))
async def restart(client, message):
    msg = await message.reply_text("<i>Restarting...</i>")
    await asyncio.sleep(2)
    await msg.edit("<i>Restarted.</i>")
    os.execl(sys.executable, sys.executable, *sys.argv)

# --- Forward Command Handlers ---

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
async def forward_command_handler(bot, message):
    user_id = message.from_user.id
    if temp.lock.get(user_id): return await message.reply("A task is in progress.")
    
    bots = await db.get_bots(user_id)
    if not bots: return await message.reply("You haven't added any bots or userbots. Please add at least one in `/settings`.")

    channels = await db.get_user_channels(user_id)
    if not channels: return await message.reply("You haven't added any target channels. Please add one in `/settings`.")

    temp.USER_STATES[user_id] = {'command_message': message, 'is_settings': False}
    
    buttons = [[InlineKeyboardButton(c['title'], callback_data=f"fwd_target_{c['chat_id']}")] for c in channels]
    buttons.append([InlineKeyboardButton("« Cancel", callback_data="close_btn")])
    await message.reply("<b>Step 1: Select the Target Channel</b>", reply_markup=InlineKeyboardMarkup(buttons))

@Client.on_callback_query(filters.regex(r'^fwd_target_'))
async def cb_select_target(bot, query):
    user_id = query.from_user.id
    state = temp.USER_STATES.get(user_id)
    if not state or state.get("is_settings"): return await query.answer("This is not for you, or your session has expired.", show_alert=True)
    
    state['to_chat_id'] = int(query.data.split('_')[-1])
    
    prompt = await query.message.edit_text(Translation.FROM_MSG)
    state['prompt_message_id'] = prompt.id
    state['state'] = 'awaiting_source'

@Client.on_callback_query(filters.regex(r"^(range_|noop)"))
async def range_menu_handler(bot: Client, query: CallbackQuery):
    user_id = query.from_user.id
    if query.data == "noop": return await query.answer()

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
            prompt_text = f"OK, send the new **{part_to_edit}** message ID."
            prompt_msg = await query.message.edit_text(prompt_text)
            temp.USER_STATES[user_id] = { "state": "awaiting_range_edit", "session_id": session_id, "part_to_edit": part_to_edit, "prompt_message_id": prompt_msg.id, "is_settings": False }
        elif action == "swap":
            start, end = session['start_id'], session['end_id']
            session['start_id'] = end
            session['end_id'] = start
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
    
    await bot.send_message(user_id, f"<b>Final Check</b>\n\n"
        f"● <b>Source:</b> `{session['from_title']}`\n"
        f"● <b>Target:</b> `{to_title}`\n"
        f"● <b>Range:</b> `{min(session['start_id'], session['end_id'])}` to `{max(session['start_id'], session['end_id'])}`\n"
        f"● <b>Operators:</b> `{len(operators)}` bots/userbots will be used.\n\n"
        f"<i>Ensure all Operators are members of the source channel and admins in the target channel.</i>",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton('✓ Yes, Start Forwarding', callback_data=f"start_public_{forward_id}")],
            [InlineKeyboardButton('« No, Cancel', callback_data="close_btn")]
        ]))

# --- Settings Command Handlers ---

@Client.on_message(filters.private & filters.command(['settings']))
async def settings_entry(client, message):
    if temp.lock.get(message.from_user.id):
        return await message.reply("A task is in progress.")
    await message.reply_photo(
        photo=random.choice(SYD),
        caption="<b>֎ Settings ֎</b>",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton('Bots & Userbots', callback_data='settings#bots'), InlineKeyboardButton('Channels', callback_data='settings#channels')]
        ])
    )

@Client.on_callback_query(filters.regex(r'^settings'))
async def settings_query_handler(bot, query):
    await query.answer()
    user_id = query.from_user.id
    if temp.lock.get(user_id): return await query.answer("A task is in progress.", show_alert=True)

    try:
        parts = query.data.split("#")
        menu_type = parts[1]
        action_parts = menu_type.split('_', 1)
        action = action_parts[0]
        value = action_parts[1] if len(action_parts) > 1 else None

        if menu_type == "main":
            await query.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton('Bots & Userbots', callback_data='settings#bots'), InlineKeyboardButton('Channels', callback_data='settings#channels')]
            ]))
        elif menu_type == "bots": await list_bots(bot, user_id, message=query.message)
        elif menu_type == "channels": await list_channels(bot, user_id, message=query.message)
        elif menu_type == "addbot": await prompt_for_input(bot, query, user_id, "awaiting_bot_token", "Send the bot token from @BotFather.")
        elif menu_type == "adduserbot": await prompt_for_input(bot, query, user_id, "awaiting_user_session", "Send the Pyrogram (v2) session string.")
        elif menu_type == "addchannel": await prompt_for_input(bot, query, user_id, "awaiting_channel_forward", "Forward a message from the target chat.")
        elif action == "editbot": await show_bot_details(query.message, user_id, int(value))
        elif action == "removebot": await remove_and_go_back(bot, query, user_id, db.remove_bot, int(value), "Bot/Userbot removed.", 'bots')
        elif action == "editchannels": await show_channel_details(query.message, user_id, int(value))
        elif action == "removechannel": await remove_and_go_back(bot, query, user_id, db.remove_channel, int(value), "Channel removed.", 'channels')

    except Exception as e:
        logger.error(f"Error in settings_query: {e}", exc_info=True)

# --- Forward Delay Command Handler ---

@Client.on_message(filters.private & filters.command(["forwardelay", "fd"]))
async def forward_delay(client: Client, message: Message):
    user_id = message.from_user.id
    
    ban_status = await db.get_ban_status(user_id)
    if ban_status["is_banned"]: return await message.reply_text(f"Access denied.\n\nReason: {ban_status['ban_reason']}")

    user_configs = await db.get_configs(user_id)
    current_delay = user_configs.get('forward_delay', 0.5)

    if len(message.command) < 2: return await message.reply_text(Translation.FORWARDELAY_TXT.format(current_delay=current_delay))
    
    try:
        delay = float(message.command[1])
        if delay < 0: return await message.reply_text("The delay must be a positive number.")
        await update_configs(user_id, 'forward_delay', delay)
        await message.reply_text(f"✅ Forwarding delay has been updated to **{delay} seconds**.")
    except ValueError: await message.reply_text("Invalid input. Please provide a number (e.g., `0.5`, `1`, `2`).")
    except Exception as e: await message.reply_text(f"An error occurred: {e}")

# --- Universal Message Handler for Interactive Sessions ---

@Client.on_message(filters.private & filters.incoming & ~filters.command())
async def universal_message_handler(bot: Client, message: Message):
    user_id = message.from_user.id
    state = temp.USER_STATES.get(user_id)
    if not state: return

    # Settings-related replies
    if state.get("is_settings"):
        if state.get("prompt_message_id"):
            try: await bot.delete_messages(user_id, state["prompt_message_id"])
            except: pass
        
        state_type = state.get("state")
        temp.USER_STATES.pop(user_id, None)

        if state_type == "awaiting_bot_token":
            if await CLIENT.add_bot(message): await list_bots(bot, user_id, as_new=True, message=message)
        elif state_type == "awaiting_user_session":
            if await CLIENT.add_session(message): await list_bots(bot, user_id, as_new=True, message=message)
        elif state_type == "awaiting_channel_forward":
            if message.forward_from_chat:
                await db.add_channel(user_id, message.forward_from_chat.id, message.forward_from_chat.title, message.forward_from_chat.username)
                await message.reply("✅ Channel added.")
                await list_channels(bot, user_id, as_new=True, message=message)
            else:
                await message.reply("That was not a valid forwarded message. Please try again.")
                await list_channels(bot, user_id, as_new=True, message=message)
    
    # Forward-related replies
    elif state.get('state') == 'awaiting_source':
        if state.get("prompt_message_id"):
            try: await bot.delete_messages(user_id, state["prompt_message_id"])
            except: pass
        
        from_chat, end_id, error = parse_message_input(message)
        if error:
            temp.USER_STATES.pop(user_id, None)
            return await message.reply(error)

        to_chat_id = state.get('to_chat_id')
        command_message = state.get('command_message')
        temp.USER_STATES.pop(user_id, None)

        bots = await db.get_bots(user_id)
        from_title = "Private Chat"
        try:
            async with CLIENT.client(bots[0]) as temp_client:
                from_title = (await temp_client.get_chat(from_chat)).title
        except Exception as e:
            logger.warning(f"Could not get chat title with first bot. Non-critical. Error: {e}")
        
        await start_range_selection(bot, command_message, from_chat, from_title, to_chat_id, 1, end_id)

# --- Helper functions for Settings & other commands ---

async def prompt_for_input(bot, query, user_id, state, text):
    await query.message.delete()
    prompt = await bot.send_message(user_id, f"{text}\n\n/cancel - to abort.")
    temp.USER_STATES[user_id] = {"state": state, "prompt_message_id": prompt.id, "is_settings": True}

async def remove_and_go_back(bot, query, user_id, remove_func, item_id, success_text, back_menu):
    await remove_func(user_id, item_id)
    await query.message.edit_text(success_text)
    await asyncio.sleep(2)
    if back_menu == 'bots': await list_bots(bot, user_id, message=query.message)
    elif back_menu == 'channels': await list_channels(bot, user_id, message=query.message)

async def list_bots(bot, user_id, message=None, as_new=False):
    buttons = [[InlineKeyboardButton(b['name'], callback_data=f"settings#editbot_{b['id']}")] for b in await db.get_bots(user_id)]
    buttons += [[InlineKeyboardButton('+ Add Bot', callback_data="settings#addbot")], [InlineKeyboardButton('+ Add Userbot', callback_data="settings#adduserbot")], [InlineKeyboardButton('« Back', callback_data="settings#main")]]
    text = "<b>֎ Bots & Userbots ֎</b>"
    
    if as_new and message: await bot.send_photo(chat_id=user_id, photo=random.choice(SYD), caption=text, reply_markup=InlineKeyboardMarkup(buttons))
    elif message: await message.edit_caption(caption=text, reply_markup=InlineKeyboardMarkup(buttons))

async def list_channels(bot, user_id, message=None, as_new=False):
    buttons = [[InlineKeyboardButton(f"● {c['title']}", callback_data=f"settings#editchannels_{c['chat_id']}")] for c in await db.get_user_channels(user_id)]
    buttons += [[InlineKeyboardButton('+ Add Channel', callback_data="settings#addchannel")], [InlineKeyboardButton('« Back', callback_data="settings#main")]]
    text = "<b>֎ Target Channels ֎</b>"

    if as_new and message: await bot.send_photo(chat_id=user_id, photo=random.choice(SYD), caption=text, reply_markup=InlineKeyboardMarkup(buttons))
    elif message: await message.edit_caption(caption=text, reply_markup=InlineKeyboardMarkup(buttons))

async def show_bot_details(message, user_id, bot_id):
    _bot = await db.get_bot(user_id, bot_id)
    uname = f"@{_bot['username']}" if _bot.get('username') else "Not Set"
    buttons = [[InlineKeyboardButton('- Remove', callback_data=f"settings#removebot_{bot_id}")], [InlineKeyboardButton('« Back', callback_data="settings#bots")]]
    TEXT = Translation.BOT_DETAILS if _bot['is_bot'] else Translation.USER_DETAILS
    await message.edit_caption(caption=TEXT.format(_bot['name'], bot_id, uname), reply_markup=InlineKeyboardMarkup(buttons))

async def show_channel_details(message, user_id, chat_id):
    chat = await db.get_channel_details(user_id, int(chat_id))
    buttons = [[InlineKeyboardButton('- Remove', callback_data=f"settings#removechannel_{chat_id}")], [InlineKeyboardButton('« Back', callback_data="settings#channels")]]
    await message.edit_caption(caption=f"<b>֎ Channel Details ֎</b>\n\n<b>Title:</b> <code>{chat['title']}</code>\n<b>ID:</b> <code>{chat['chat_id']}</code>", reply_markup=InlineKeyboardMarkup(buttons))

@Client.on_callback_query(filters.regex(r'^close_btn$'))
async def close_callback(bot, query):
    temp.USER_STATES.pop(query.from_user.id, None)
    await query.message.delete()

@Client.on_callback_query(filters.regex(r'^back'))
async def back_to_start(bot, query):
    await query.message.edit_caption(
       caption=Translation.START_TXT.format(query.from_user.first_name),
       reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton('Help', callback_data='help'),
            InlineKeyboardButton('About', callback_data='about')
       ]])
    )
    
@Client.on_callback_query(filters.regex(r'^help'))
async def helpcb(bot, query):
    await query.message.edit_text(
        text=Translation.HELP_TXT,
        reply_markup=InlineKeyboardMarkup(
            [[
            InlineKeyboardButton('How to Use', callback_data='how_to_use')
            ],[
            InlineKeyboardButton('Settings', callback_data='settings#main'),
            InlineKeyboardButton('Stats', callback_data='status')
            ],[
            InlineKeyboardButton('Active Tasks', callback_data='active_tasks_cmd'),
            InlineKeyboardButton('« Back', callback_data='back')
            ]]
        ))

@Client.on_callback_query(filters.regex(r'^about'))
async def about(bot, query):
    await query.message.edit_caption(
        caption=Translation.ABOUT_TXT.format(bot.me.mention),
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('« Back', callback_data='back')]]),
    )

@Client.on_callback_query(filters.regex(r'^how_to_use'))
async def how_to_use(bot, query):
    await query.message.edit_caption(
        caption=Translation.HOW_USE_TXT,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('« Back', callback_data='help')]]),
    )

@Client.on_callback_query(filters.regex(r'^status'))
async def status(bot, query):
    await query.message.edit_caption(
        caption="Bot is online and operational.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('« Back', callback_data='help')]]),
    )
