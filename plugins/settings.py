import asyncio
import random
import logging
from database import db
from config import Config, temp
from translation import Translation
from pyrogram import Client, filters
from .test import CLIENT, update_configs
from .parser import parse_buttons
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

SYD = ["https://files.catbox.moe/3lwlbm.png"]
logger = logging.getLogger(__name__)

async def show_settings_menu(bot, message, menu_type):
    user_id = message.chat.id
    if menu_type == 'bots':
        await list_bots(message, user_id, as_new=True)
    elif menu_type == 'channels':
        await list_channels(message, user_id, as_new=True)

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
        menu_type = parts[1]
        action_parts = menu_type.split('_', 1)
        action = action_parts[0]
        value = action_parts[1] if len(action_parts) > 1 else None

        if menu_type == "main": await query.message.edit_reply_markup(reply_markup=main_buttons())
        elif menu_type == "bots": await list_bots(query.message, user_id)
        elif menu_type == "channels": await list_channels(query.message, user_id)
        elif menu_type == "addbot": await prompt_for_input(bot, query, user_id, "awaiting_bot_token", "Send the bot token from @BotFather.")
        elif menu_type == "adduserbot": await prompt_for_input(bot, query, user_id, "awaiting_user_session", "Send the Pyrogram (v2) session string.")
        elif menu_type == "addchannel": await prompt_for_input(bot, query, user_id, "awaiting_channel_forward", "Forward a message from the target chat.")
        elif action == "editbot": await show_bot_details(query.message, user_id, int(value))
        elif action == "removebot": await remove_and_go_back(query.message, user_id, db.remove_bot, int(value), "Bot/Userbot removed.", 'bots')
        elif action == "editchannels": await show_channel_details(query.message, user_id, int(value))
        elif action == "removechannel": await remove_and_go_back(query.message, user_id, db.remove_channel, int(value), "Channel removed.", 'channels')

    except Exception as e:
        logger.error(f"Error in settings_query: {e}", exc_info=True)
        await bot.send_message(user_id, "An unexpected error occurred.")

async def prompt_for_input(bot, query, user_id, state, text):
    await query.message.delete()
    prompt = await bot.send_message(user_id, f"{text}\n\n/cancel - to abort.")
    temp.USER_STATES[user_id] = {"state": state, "prompt_message_id": prompt.id}

async def remove_and_go_back(message, user_id, remove_func, item_id, success_text, back_menu):
    await remove_func(user_id, item_id)
    await message.edit_text(success_text)
    await asyncio.sleep(2)
    await show_settings_menu(message.bot, message, back_menu)
    await message.delete()

async def list_bots(message, user_id, as_new=False):
    buttons = []
    for b in await db.get_bots(user_id):
        buttons.append([InlineKeyboardButton(b['name'], callback_data=f"settings#editbot_{b['id']}")])
    buttons += [[InlineKeyboardButton('+ Add Bot', callback_data="settings#addbot")], [InlineKeyboardButton('+ Add Userbot', callback_data="settings#adduserbot")], [InlineKeyboardButton('« Back', callback_data="settings#main")]]
    text = "<b>֎ Bots & Userbots ֎</b>\n\nThese are your Operators. All of them will be used to forward messages."
    
    if as_new:
        await message.reply_photo(photo=random.choice(SYD), caption=text, reply_markup=InlineKeyboardMarkup(buttons))
    else:
        await message.edit_text(text, reply_markup=InlineKeyboardMarkup(buttons))

async def list_channels(message, user_id, as_new=False):
    buttons = [[InlineKeyboardButton(f"● {c['title']}", callback_data=f"settings#editchannels_{c['chat_id']}")] for c in await db.get_user_channels(user_id)]
    buttons += [[InlineKeyboardButton('+ Add Channel', callback_data="settings#addchannel")], [InlineKeyboardButton('« Back', callback_data="settings#main")]]
    text = "<b>֎ Target Channels ֎</b>"
    if as_new:
        await message.reply_photo(photo=random.choice(SYD), caption=text, reply_markup=InlineKeyboardMarkup(buttons))
    else:
        await message.edit_text(text, reply_markup=InlineKeyboardMarkup(buttons))

async def show_bot_details(message, user_id, bot_id):
    _bot = await db.get_bot(user_id, bot_id)
    uname = f"@{_bot['username']}" if _bot.get('username') else "Not Set"
    buttons = [[InlineKeyboardButton('- Remove', callback_data=f"settings#removebot_{bot_id}")], [InlineKeyboardButton('« Back', callback_data="settings#bots")]]
    TEXT = Translation.BOT_DETAILS if _bot['is_bot'] else Translation.USER_DETAILS
    await message.edit_text(TEXT.format(_bot['name'], bot_id, uname), reply_markup=InlineKeyboardMarkup(buttons))

async def show_channel_details(message, user_id, chat_id):
    chat = await db.get_channel_details(user_id, chat_id)
    buttons = [[InlineKeyboardButton('- Remove', callback_data=f"settings#removechannel_{chat_id}")], [InlineKeyboardButton('« Back', callback_data="settings#channels")]]
    await message.edit_text(f"<b>֎ Channel Details ֎</b>\n\n<b>Title:</b> <code>{chat['title']}</code>\n<b>ID:</b> <code>{chat_id}</code>", reply_markup=InlineKeyboardMarkup(buttons))

def main_buttons():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('Bots & Userbots', callback_data='settings#bots'), InlineKeyboardButton('Channels', callback_data='settings#channels')]
    ])
