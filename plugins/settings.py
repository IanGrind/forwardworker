import asyncio
import random
import logging
from database import db
from config import Config, temp
from translation import Translation
from pyrogram import Client, filters
from .test import CLIENT
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

logger = logging.getLogger(__name__)
SYD = ["https://files.catbox.moe/3lwlbm.png"]

# This is the new, dedicated handler for all replies within the settings menu.
@Client.on_message(filters.private & filters.incoming & ~filters.command("settings"))
async def settings_message_handler(bot: Client, message: Message):
    user_id = message.from_user.id
    state = temp.USER_STATES.get(user_id)
    if not state or not state.get("is_settings"):
        return # This handler only works for settings-related conversations

    # Always clean up the prompt message
    if state.get("prompt_message_id"):
        try: await bot.delete_messages(user_id, state["prompt_message_id"])
        except: pass
    
    state_type = state.get("state")
    temp.USER_STATES.pop(user_id, None) # Clear state after processing

    if state_type == "awaiting_bot_token":
        if await CLIENT.add_bot(message):
            await list_bots(bot, user_id, as_new=True)
            
    elif state_type == "awaiting_user_session":
        if await CLIENT.add_session(message):
            await list_bots(bot, user_id, as_new=True)

    elif state_type == "awaiting_channel_forward":
        if message.forward_from_chat:
            await db.add_channel(user_id, message.forward_from_chat.id, message.forward_from_chat.title, message.forward_from_chat.username)
            await message.reply("✅ Channel added.")
            await list_channels(bot, user_id, as_new=True)
        else:
            await message.reply("That was not a valid forwarded message. Please try again.")
            await list_channels(bot, user_id, as_new=True)

@Client.on_message(filters.private & filters.command(['settings']))
async def settings_entry(client, message):
    if temp.lock.get(message.from_user.id): return await message.reply("A task is in progress.")
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

async def prompt_for_input(bot, query, user_id, state, text):
    await query.message.delete()
    prompt = await bot.send_message(user_id, f"{text}\n\n/cancel - to abort.")
    temp.USER_STATES[user_id] = {"state": state, "prompt_message_id": prompt.id, "is_settings": True}

async def remove_and_go_back(bot, query, user_id, remove_func, item_id, success_text, back_menu):
    await remove_func(user_id, item_id)
    await query.message.edit_text(success_text)
    await asyncio.sleep(2)
    if back_menu == 'bots':
        await list_bots(bot, user_id, message=query.message)
    elif back_menu == 'channels':
        await list_channels(bot, user_id, message=query.message)

async def list_bots(bot, user_id, message=None, as_new=False):
    buttons = []
    for b in await db.get_bots(user_id):
        buttons.append([InlineKeyboardButton(b['name'], callback_data=f"settings#editbot_{b['id']}")])
    buttons += [[InlineKeyboardButton('+ Add Bot', callback_data="settings#addbot")], [InlineKeyboardButton('+ Add Userbot', callback_data="settings#adduserbot")], [InlineKeyboardButton('« Back', callback_data="settings#main")]]
    text = "<b>֎ Bots & Userbots ֎</b>\n\nThese are your Operators. All of them will be used to forward messages."
    
    if as_new and message:
        await bot.send_photo(chat_id=user_id, photo=random.choice(SYD), caption=text, reply_markup=InlineKeyboardMarkup(buttons))
    elif message:
        await message.edit_caption(caption=text, reply_markup=InlineKeyboardMarkup(buttons))

async def list_channels(bot, user_id, message=None, as_new=False):
    buttons = [[InlineKeyboardButton(f"● {c['title']}", callback_data=f"settings#editchannels_{c['chat_id']}")] for c in await db.get_user_channels(user_id)]
    buttons += [[InlineKeyboardButton('+ Add Channel', callback_data="settings#addchannel")], [InlineKeyboardButton('« Back', callback_data="settings#main")]]
    text = "<b>֎ Target Channels ֎</b>"
    if as_new and message:
        await bot.send_photo(chat_id=user_id, photo=random.choice(SYD), caption=text, reply_markup=InlineKeyboardMarkup(buttons))
    elif message:
        await message.edit_caption(caption=text, reply_markup=InlineKeyboardMarkup(buttons))


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

def main_buttons():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('Bots & Userbots', callback_data='settings#bots'), InlineKeyboardButton('Channels', callback_data='settings#channels')]
    ])
