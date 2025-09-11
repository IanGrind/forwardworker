import asyncio
import random
import logging
from database import db
from config import Config, temp
from translation import Translation
from pyrogram import Client, filters
from .test import CLIENT, update_configs
from .utils import parse_buttons
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

CLIENT = CLIENT()
SYD = ["https://files.catbox.moe/3lwlbm.png"]
logger = logging.getLogger(__name__)

@Client.on_message(filters.private & filters.command(['settings']))
async def settings(client, message):
    user_id = message.from_user.id
    if temp.lock.get(user_id):
        return await message.reply("A task is already in progress. Please wait.")
    ban_status = await db.get_ban_status(user_id)
    if ban_status["is_banned"]:
        return await message.reply_text(f"Access denied.\n\nReason: {ban_status['ban_reason']}")
    await message.reply_photo(photo=random.choice(SYD), caption="<b>֎ Settings ֎</b>\n\nManage personal configurations.", reply_markup=main_buttons())

@Client.on_callback_query(filters.regex(r'^settings'))
async def settings_query(bot, query):
    await query.answer()
    user_id = query.from_user.id
    if temp.lock.get(user_id):
        return await query.answer("A task is already in progress.", show_alert=True)

    try:
        parts = query.data.split("#")
        type = parts[1]
        action_parts = type.split('_', 1)
        action = action_parts[0]
        value = action_parts[1] if len(action_parts) > 1 else None

        if type == "main":
            await query.message.edit_reply_markup(reply_markup=main_buttons())
        elif type == "bots":
            await list_bots(query, user_id)
        elif type == "addbot":
            await query.message.delete()
            temp.USER_STATES[user_id] = {"state": "awaiting_bot_token"}
            await bot.send_message(user_id, "Forward the message from @BotFather containing the token.\n\n/cancel - to abort.")
        elif type == "adduserbot":
            await query.message.delete()
            temp.USER_STATES[user_id] = {"state": "awaiting_user_session"}
            await bot.send_message(user_id, "Send the Pyrogram (v2) session string.\n\n/cancel - to abort.")
        elif type == "channels":
            await list_channels(query, user_id)
        elif type == "addchannel":
            await query.message.delete()
            prompt = await bot.send_message(user_id, "<b>Set Target Chat</b>\n\nForward a message from the target chat.\n\n/cancel - to cancel.")
            temp.USER_STATES[user_id] = {"state": "awaiting_channel_forward", "prompt_message_id": prompt.id}
        elif type == "workers":
            await list_workers(query, user_id)
        elif type == "addworkerbot":
            await query.message.delete()
            temp.USER_STATES[user_id] = {"state": "awaiting_worker_bot_token"}
            await bot.send_message(user_id, "Forward the message from @BotFather for the worker bot.\n\n/cancel - to abort.")
        elif action == "editbot":
            await show_bot_details(query, user_id, int(value))
        elif action == "removebot":
            await db.remove_bot(user_id, int(value))
            await query.message.edit_text("Bot/Userbot removed. ✓", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('« Back', callback_data="settings#bots")]]))
        elif action == "setmanager":
            await db.set_manager_userbot(user_id, int(value))
            await query.message.edit_text("✅ This userbot is now the Manager.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('« Back', callback_data="settings#bots")]]))
        elif action == "editchannels":
            await show_channel_details(query, user_id, int(value))
        elif action == "removechannel":
            await db.remove_channel(user_id, int(value))
            await query.message.edit_text("Channel removed. ✓", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('« Back', callback_data="settings#channels")]]))
        elif action == "editworker":
            await show_worker_details(query, user_id, int(value))
        elif action == "removeworker":
            await db.remove_worker_bot(user_id, int(value))
            await query.message.edit_text("Worker bot removed. ✓", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('« Back', callback_data="settings#workers")]]))

    except Exception as e:
        logger.error(f"Error in settings_query: {e}", exc_info=True)
        await bot.send_message(user_id, "An unexpected error occurred.")

async def list_bots(query, user_id):
    manager = await db.get_manager_userbot(user_id)
    buttons = []
    for b in await db.get_bots(user_id):
        name = b['name']
        if manager and not b['is_bot'] and manager['id'] == b['id']:
            name = f"👑 {name} (Manager)"
        buttons.append([InlineKeyboardButton(name, callback_data=f"settings#editbot_{b['id']}")])
    buttons += [
        [InlineKeyboardButton('+ Add Bot', callback_data="settings#addbot")],
        [InlineKeyboardButton('+ Add Userbot', callback_data="settings#adduserbot")],
        [InlineKeyboardButton('« Back', callback_data="settings#main")]
    ]
    await query.message.edit_text("<b>֎ Bots & Userbots ֎</b>\n\nManage your accounts. Designate a userbot as Manager to auto-add workers.", reply_markup=InlineKeyboardMarkup(buttons))

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
    TEXT = Translation.BOT_DETAILS if _bot['is_bot'] else Translation.USER_DETAILS
    uname = f"@{_bot['username']}" if _bot.get('username') else "Not Set"
    buttons = [[InlineKeyboardButton('- Remove', callback_data=f"settings#removebot_{bot_id}")], [InlineKeyboardButton('« Back', callback_data="settings#bots")]]
    if not _bot['is_bot']:
        buttons.insert(0, [InlineKeyboardButton('👑 Set as Manager', callback_data=f"settings#setmanager_{bot_id}")])
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
