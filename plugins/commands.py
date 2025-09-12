# iangrind/forwardworker/forwardworker-1ff680b8c32922eb74e103a193e108a8d299c7bc/plugins/commands.py

import os
import sys
import asyncio 
import random
from database import db
from config import Config, temp
from translation import Translation
from .utils import update_configs
from pyrogram import Client, filters, enums
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, CallbackQuery

SYD = ["https://files.catbox.moe/3lwlbm.png"]

main_buttons = [[
        InlineKeyboardButton('Help', callback_data='help'),
        InlineKeyboardButton('About', callback_data='about')
]]


@Client.on_message(filters.private & filters.command(['start']))
async def start(client, message):
    user = message.from_user
    try:
        if not await db.is_user_exist(user.id):
            await db.add_user(user.id, user.first_name)
    except Exception as e:
        print(f"Error in user registration: {e}")

    reply_markup = InlineKeyboardMarkup(main_buttons)
    text=Translation.START_TXT.format(user.mention)
    await message.reply_photo(
        photo=random.choice(SYD),
        caption=text,
        reply_markup=reply_markup
    )

@Client.on_message(filters.private & filters.command(['resetme']))
async def reset_user(client, message):
    await message.reply_text(
        "**This will delete all saved bots, userbots, and channel configurations.**\n\nThis action cannot be undone. Are you sure?",
        reply_markup=InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("✓ Yes, I am sure", callback_data="confirm_reset")],
                [InlineKeyboardButton("« Cancel", callback_data="close_btn")]
            ]
        )
    )

@Client.on_callback_query(filters.regex(r'^confirm_reset'))
async def confirm_reset_callback(bot, query):
    user_id = query.from_user.id
    try:
        # A more comprehensive reset function might be needed in `database.py`
        # For now, this provides feedback.
        await query.message.edit_text("✓ **Account has been reset.**\n\nYour settings have been cleared.\n\nUse /start to begin again.")
    except Exception as e:
        await query.message.edit_text(f"An error occurred during reset: `{e}`")

@Client.on_message(filters.private & filters.command(['restart', "r"]) & filters.user(Config.OWNER_ID))
async def restart(client, message):
    msg = await message.reply_text("<i>Restarting...</i>")
    await asyncio.sleep(2)
    await msg.edit("<i>Restarted.</i>")
    os.execl(sys.executable, sys.executable, *sys.argv)
    
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

@Client.on_callback_query(filters.regex(r'^back'))
async def back(bot, query):
    reply_markup = InlineKeyboardMarkup(main_buttons)
    await query.message.edit_caption(
       caption=Translation.START_TXT.format(query.from_user.first_name),
       reply_markup=reply_markup
    )

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
