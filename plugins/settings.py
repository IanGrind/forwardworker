import asyncio
import random
from database import db
from config import Config, temp
from translation import Translation
from pyrogram import Client, filters
from .test import get_configs, update_configs, CLIENT
from .utils import parse_buttons
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

CLIENT = CLIENT()
SYD = ["https://files.catbox.moe/3lwlbm.png"]


@Client.on_message(filters.private & filters.command(['settings']))
async def settings(client, message):
    user_id = message.from_user.id
    if temp.lock.get(user_id):
        return await message.reply("A task is already in progress. Please wait for it to complete before changing settings.")

    ban_status = await db.get_ban_status(user_id)
    if ban_status["is_banned"]:
        return await message.reply_text(f"Access denied.\n\nReason: {ban_status['ban_reason']}")

    text="<b>֎ Settings ֎</b>\n\nManage personal configurations."
    await message.reply_photo(
        photo=random.choice(SYD),
        caption=text,
        reply_markup=main_buttons(),
        quote=True
    )

@Client.on_callback_query(filters.regex(r'^settings'))
async def settings_query(bot, query):
    await query.answer()
    user_id = query.from_user.id
  
    if temp.lock.get(user_id):
        return await query.answer("A task is already in progress. Please wait for it to complete before changing settings.", show_alert=True)

    try:
        i, type = query.data.split("#")
        buttons = [[InlineKeyboardButton('« Back', callback_data="settings#main")]]
        
        if type=="main":
           await query.message.edit_text(
             "<b>֎ Settings ֎</b>\n\nManage personal configurations.",
             reply_markup=main_buttons())

        elif type=="bots":
           buttons = []
           bots = await db.get_bots(user_id)
           for _bot in bots:
              if not _bot.get('id'): continue
              bot_name = _bot.get('name') or _bot.get('username', f"ID: {_bot['id']}")
              bot_id = _bot.get('id')
              buttons.append([InlineKeyboardButton(bot_name, callback_data=f"settings#editbot_{bot_id}")])

           buttons.append([InlineKeyboardButton('+ Add Bot', callback_data="settings#addbot")])
           buttons.append([InlineKeyboardButton('+ Add Userbot', callback_data="settings#adduserbot")])
           buttons.append([InlineKeyboardButton('« Back', callback_data="settings#main")])
           await query.message.edit_text(
             "<b>֎ Bots & Userbots ֎</b>\n\nManage connected bots and userbots.",
             reply_markup=InlineKeyboardMarkup(buttons))

        elif type=="addbot":
           await query.message.delete()
           temp.USER_STATES[user_id] = {"state": "awaiting_bot_token"}
           await bot.send_message(user_id, "Forward the message from @BotFather containing the token, or just send the token string.\n\n/cancel - to abort.")

        elif type=="adduserbot":
           await query.message.delete()
           temp.USER_STATES[user_id] = {"state": "awaiting_user_session"}
           await bot.send_message(user_id, "Send the Pyrogram (v2) session string.\n\nGet one from @mdsessiongenbot.\n\n/cancel - to cancel.")

        elif type=="channels":
           buttons = []
           channels = await db.get_user_channels(user_id)
           for channel in channels:
              buttons.append([InlineKeyboardButton(f"● {channel['title']}", callback_data=f"settings#editchannels_{channel['chat_id']}")])
           buttons.append([InlineKeyboardButton('+ Add Channel', callback_data="settings#addchannel")])
           buttons.append([InlineKeyboardButton('« Back', callback_data="settings#main")])
           await query.message.edit_text(
             "<b>֎ Target Channels ֎</b>\n\nManage target chats for forwarding.",
             reply_markup=InlineKeyboardMarkup(buttons))

        elif type=="addchannel":
           await query.message.delete()
           prompt_message = await bot.send_message(user_id, "<b>Set Target Chat</b>\n\nForward a message from the target chat.\n\n/cancel - to cancel.")
           temp.USER_STATES[user_id] = {
               "state": "awaiting_channel_forward",
               "prompt_message_id": prompt_message.id
           }

        elif type.startswith("editbot"):
           bot_id = int(type.split('_')[1])
           _bot = await db.get_bot(user_id, bot_id)
           if not _bot:
               return await query.message.edit_text("Bot configuration not found.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('« Back', callback_data="settings#bots")]]))

           bot_name = _bot.get('name', 'N/A')
           bot_uname = _bot.get('username')
           is_bot = _bot.get('is_bot', True)
           TEXT = Translation.BOT_DETAILS if is_bot else Translation.USER_DETAILS
           uname_display = f"@{bot_uname}" if bot_uname else "Not Set"
           buttons = [[InlineKeyboardButton('- Remove', callback_data=f"settings#removebot_{bot_id}")],
                      [InlineKeyboardButton('« Back', callback_data="settings#bots")]]
           await query.message.edit_text(TEXT.format(bot_name, bot_id, uname_display), reply_markup=InlineKeyboardMarkup(buttons))

        elif type.startswith("removebot"):
           bot_id = int(type.split('_')[1])
           await db.remove_bot(user_id, bot_id)
           await query.message.edit_text("Bot removed. ✓", reply_markup=InlineKeyboardMarkup(buttons))

        elif type.startswith("editchannels"):
           chat_id = type.split('_')[1]
           chat = await db.get_channel_details(user_id, chat_id)
           buttons = [[InlineKeyboardButton('- Remove', callback_data=f"settings#removechannel_{chat_id}")],
                      [InlineKeyboardButton('« Back', callback_data="settings#channels")]]
           await query.message.edit_text(f"<b>֎ Channel Details ֎</b>\n\n<b>Title:</b> <code>{chat['title']}</code>\n<b>ID:</b> <code>{chat['chat_id']}</code>\n<b>Username:</b> {chat['username']}", reply_markup=InlineKeyboardMarkup(buttons))

        elif type.startswith("removechannel"):
           chat_id = type.split('_')[1]
           await db.remove_channel(user_id, chat_id)
           await query.message.edit_text("Channel removed. ✓", reply_markup=InlineKeyboardMarkup(buttons))
        
        elif type == "workers":
            buttons = []
            workers = await db.get_worker_bots(user_id)
            main_worker = await db.get_main_worker(user_id)
            for worker in workers:
                if not worker.get('id'): continue
                worker_name = worker.get('name') or worker.get('username', f"ID: {worker['id']}")
                worker_id = worker.get('id')
                if main_worker and main_worker['id'] == worker_id:
                    worker_name = f"👑 {worker_name}"
                buttons.append([InlineKeyboardButton(worker_name, callback_data=f"settings#editworker_{worker_id}")])

            buttons.append([InlineKeyboardButton('+ Add Worker Bot', callback_data="settings#addworkerbot")])
            buttons.append([InlineKeyboardButton('+ Add Main Worker Bot', callback_data="settings#addmainworker")])
            buttons.append([InlineKeyboardButton('« Back', callback_data="settings#main")])
            await query.message.edit_text(
                "<b>֎ Worker Bots ֎</b>\n\nManage your worker bots for forwarding.",
                reply_markup=InlineKeyboardMarkup(buttons))

        elif type.startswith("editworker"):
            worker_id = int(type.split('_')[1])
            worker = await db.get_worker_bot(user_id, worker_id) 
            if not worker:
                return await query.message.edit_text("Worker bot not found.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('« Back', callback_data="settings#workers")]]))

            worker_name = worker.get('name', 'N/A')
            worker_uname = worker.get('username')
            is_bot = worker.get('is_bot', True)
            TEXT = Translation.BOT_DETAILS if is_bot else Translation.USER_DETAILS
            uname_display = f"@{worker_uname}" if worker_uname else "Not Set"
            buttons = [
                [InlineKeyboardButton('Set as Main Worker', callback_data=f"settings#setmainworker_{worker_id}")],
                [InlineKeyboardButton('- Remove', callback_data=f"settings#removeworker_{worker_id}")],
                [InlineKeyboardButton('« Back', callback_data="settings#workers")]
            ]
            await query.message.edit_text(TEXT.format(worker_name, worker_id, uname_display), reply_markup=InlineKeyboardMarkup(buttons))

        elif type.startswith("removeworker"):
            worker_id = int(type.split('_')[1])
            await db.remove_worker_bot(user_id, worker_id)
            await query.message.edit_text("Worker bot removed. ✓", reply_markup=InlineKeyboardMarkup(buttons))
        
        elif type == "addworkerbot":
           await query.message.delete()
           temp.USER_STATES[user_id] = {"state": "awaiting_worker_bot_token"}
           await bot.send_message(user_id, "Forward the message from @BotFather containing the worker bot's token, or just send the token string.\n\n/cancel - to abort.")

        elif type == "addmainworker":
            await query.message.delete()
            temp.USER_STATES[user_id] = {"state": "awaiting_main_worker_token"}
            await bot.send_message(user_id, "Forward the message from @BotFather containing the main worker bot's token, or just send the token string.\n\n/cancel - to abort.")

        elif type.startswith("setmainworker"):
            worker_id = int(type.split('_')[1])
            await db.set_main_worker(user_id, worker_id)
            await query.message.edit_text("Main worker bot set. ✓", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('« Back', callback_data="settings#workers")]]))

    except Exception as e:
        logger.error(f"Error in settings_query: {e}", exc_info=True)
        try:
            await query.message.reply_text("An unexpected error occurred. Please try again later.")
        except:
            await bot.send_message(user_id, "An unexpected error occurred. Please try again later.")


def main_buttons():
  buttons = [[
       InlineKeyboardButton('Bots & Userbots', callback_data=f'settings#bots'),
       InlineKeyboardButton('Channels', callback_data=f'settings#channels')
       ],[
       InlineKeyboardButton('Caption', callback_data=f'settings#caption'),
       InlineKeyboardButton('Database', callback_data=f'settings#database')
       ],[
       InlineKeyboardButton('Message Filters', callback_data=f'settings#filters'),
       InlineKeyboardButton('Button', callback_data=f'settings#button')
       ],[
       InlineKeyboardButton('Worker Bots', callback_data='settings#workers'),
       InlineKeyboardButton('Extra Settings', callback_data='settings#nextfilters')
       ],[
       InlineKeyboardButton('« Back', callback_data='back')
       ]]
  return InlineKeyboardMarkup(buttons)
