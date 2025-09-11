import asyncio
import random
import logging
from database import db
from config import Config, temp
from translation import Translation
from pyrogram import Client, filters
from .test import CLIENT, update_configs
from .parser import parse_buttons # <-- FIXED IMPORT
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

CLIENT = CLIENT()
SYD = ["https://files.catbox.moe/3lwlbm.png"]
logger = logging.getLogger(__name__)

@Client.on_message(filters.private & filters.command(['settings']))
async def settings(client, message):
    user_id = message.from_user.id
    if temp.lock.get(user_id): return await message.reply("A task is in progress.")
    ban_status = await db.get_ban_status(user_id)
    if ban_status["is_banned"]: return await message.reply_text(f"Access denied: {ban_status['ban_reason']}")
    await message.reply_photo(photo=random.choice(SYD), caption="<b>֎ Settings ֎</b>", reply_markup=main_buttons())

@Client.on_callback_query(filters.regex(r'^settings'))
async def settings_query(bot, query):
    await query.answer()
    user_id = query.from_user.id
    if temp.lock.get(user_id): return await query.answer("A task is in progress.", show_alert=True)

    try:
        parts = query.data.split("#")
        type = parts[1]
        action_parts = type.split('_', 1)
        action = action_parts[0]
        value = action_parts[1] if len(action_parts) > 1 else None

        if type == "main": await query.message.edit_reply_markup(reply_markup=main_buttons())
        elif type == "bots": await list_bots(query, user_id)
        elif type == "channels": await list_channels(query, user_id)
        elif type == "workers": await list_workers(query, user_id)
        elif type == "addbot": await prompt_for_input(bot, query, user_id, "awaiting_bot_token", "Forward the message from @BotFather.")
        elif type == "adduserbot": await prompt_for_input(bot, query, user_id, "awaiting_user_session", "Send the Pyrogram (v2) session string.")
        elif type == "addchannel": await prompt_for_input(bot, query, user_id, "awaiting_channel_forward", "Forward a message from the target chat.")
        elif type == "addworkerbot": await prompt_for_input(bot, query, user_id, "awaiting_worker_bot_token", "Forward the message from @BotFather for the worker bot.")
        elif action == "editbot": await show_bot_details(query, user_id, int(value))
        elif action == "removebot": await remove_and_go_back(query, user_id, db.remove_bot, int(value), "Bot/Userbot removed.", "settings#bots")
        elif action == "setmanager": await set_manager_and_go_back(query, user_id, int(value))
        elif action == "editchannels": await show_channel_details(query, user_id, int(value))
        elif action == "removechannel": await remove_and_go_back(query, user_id, db.remove_channel, int(value), "Channel removed.", "settings#channels")
        elif action == "editworker": await show_worker_details(query, user_id, int(value))
        elif action == "removeworker": await remove_and_go_back(query, user_id, db.remove_worker_bot, int(value), "Worker bot removed.", "settings#workers")

    except Exception as e:
        logger.error(f"Error in settings_query: {e}", exc_info=True)
        await bot.send_message(user_id, "An unexpected error occurred.")

async def prompt_for_input(bot, query, user_id, state, text):
    await query.message.delete()
    prompt = await bot.send_message(user_id, f"{text}\n\n/cancel - to abort.")
    temp.USER_STATES[user_id] = {"state": state, "prompt_message_id": prompt.id}

async def remove_and_go_back(query, user_id, remove_func, item_id, success_text, back_cb):
    await remove_func(user_id, item_id)
    await query.message.edit_text(success_text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('« Back', callback_data=back_cb)]]))

async def set_manager_and_go_back(query, user_id, bot_id):
    await db.set_manager_userbot(user_id, bot_id)
    await query.message.edit_text("✅ This userbot is now the Manager.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('« Back', callback_data="settings#bots")]]))

async def list_bots(query, user_id):
    manager = await db.get_manager_userbot(user_id)
    buttons = []
    for b in await db.get_bots(user_id):
        name = b['name']
        if manager and not b['is_bot'] and manager['id'] == b['id']: name = f"👑 {name} (Manager)"
        buttons.append([InlineKeyboardButton(name, callback_data=f"settings#editbot_{b['id']}")])
    buttons += [[InlineKeyboardButton('+ Add Bot', callback_data="settings#addbot")], [InlineKeyboardButton('+ Add Userbot', callback_data="settings#adduserbot")], [InlineKeyboardButton('« Back', callback_data="settings#main")]]
    await query.message.edit_text("<b>֎ Bots & Userbots ֎</b>", reply_markup=InlineKeyboardMarkup(buttons))

async def list_channels(query, user_id):
    buttons = [[InlineKeyboardButton(f"● {c['title']}", callback_data=f"settings#editchannels_{c['chat_id']}")] for c in await db.get_user_channels(user_id)]
    buttons += [[InlineKeyboardButton('+ Add Channel', callback_data="settings#addchannel")], [InlineKeyboardButton('« Back', callback_data="settings#main")]]
    await query.message.edit_text("<b>֎ Target Channels ֎</b>", reply_markup=InlineKeyboardMarkup(buttons))

async def list_workers(query, user_id):
    buttons = [[InlineKeyboardButton(w['name'], callback_data=f"settings#editworker_{w['id']}")] for w in await db.get_worker_bots(user_id)]
    buttons += [[InlineKeyboardButton('+ Add Worker Bot', callback_data="settings#addworkerbot")], [InlineKeyboardButton('« Back', callback_data="settings#main")]]
    await query.message.edit_text("<b>֎ Worker Bots ֎</b>", reply_markup=InlineKeyboardMarkup(buttons))

async def show_bot_details(query, user_id, bot_id):
    _bot = await db.get_bot(user_id, bot_id)
    uname = f"@{_bot['username']}" if _bot.get('username') else "Not Set"
    buttons = [[InlineKeyboardButton('- Remove', callback_data=f"settings#removebot_{bot_id}")], [InlineKeyboardButton('« Back', callback_data="settings#bots")]]
    if not _bot['is_bot']:
        buttons.insert(0, [InlineKeyboardButton('👑 Set as Manager', callback_data=f"settings#setmanager_{bot_id}")])
    TEXT = Translation.BOT_DETAILS if _bot['is_bot'] else Translation.USER_DETAILS
    await query.message.edit_text(TEXT.format(_bot['name'], bot_id, uname), reply_markup=InlineKeyboardMarkup(buttons))

async def show_channel_details(query, user_id, chat_id):
    chat = await db.get_channel_details(user_id, chat_id)
    buttons = [[InlineKeyboardButton('- Remove', callback_data=f"settings#removechannel_{chat_id}")], [InlineKeyboardButton('« Back', callback_data="settings#channels")]]
    await query.message.edit_text(f"<b>֎ Channel Details ֎</b>\n\n<b>Title:</b> <code>{chat['title']}</code>\n<b>ID:</b> <code>{chat_id}</code>", reply_markup=InlineKeyboardMarkup(buttons))

async def show_worker_details(query, user_id, bot_id):
    worker = await db.get_worker_bot(user_id, bot_id)
    uname = f"@{worker['username']}" if worker.get('username') else "Not Set"
    buttons = [[InlineKeyboardButton('- Remove', callback_data=f"settings#removeworker_{bot_id}")], [InlineKeyboardButton('« Back', callback_data="settings#workers")]]
    await query.message.edit_text(Translation.BOT_DETAILS.format(worker['name'], bot_id, uname), reply_markup=InlineKeyboardMarkup(buttons))

def main_buttons():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('Bots & Userbots', callback_data='settings#bots'), InlineKeyboardButton('Channels', callback_data='settings#channels')],
        [InlineKeyboardButton('Worker Bots', callback_data='settings#workers')],
        [InlineKeyboardButton('« Back', callback_data='back')]
    ])
