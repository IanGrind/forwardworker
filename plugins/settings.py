import asyncio
import random
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from config import temp
from database import db
from plugins.parser import parse_buttons
from plugins.test import CLIENT
from translation import Translation

SYD = ["https://files.catbox.moe/3lwlbm.png"]


# +~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+
# Main Settings Logic & Callback Handlers
# +~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+

async def get_configs(user_id):
    """Fetches user configurations from the database."""
    return await db.get_configs(user_id)


async def update_configs(user_id, key, value):
    """Updates a specific configuration key for a user."""
    configs = await get_configs(user_id)
    if key in ['caption', 'duplicate', 'db_uri', 'forward_tag', 'protect', 'file_size', 'size_limit', 'extension',
               'keywords', 'button', 'forward_delay']:
        configs[key] = value
    else:
        configs.setdefault('filters', {})[key] = value
    await db.update_configs(user_id, configs)


@Client.on_message(filters.private & filters.command(['settings']))
async def settings_entry(client, message):
    """Entry point for the /settings command."""
    if temp.lock.get(message.from_user.id):
        return await message.reply("A task is in progress. Please wait until it's finished to change settings.")
    await message.reply_photo(
        photo=random.choice(SYD),
        caption="<b>֎ Settings ֎</b>\n\nSelect a category to configure.",
        reply_markup=main_buttons()
    )


@Client.on_callback_query(filters.regex(r'^settings'))
async def settings_query_handler(bot, query):
    """Handles all interactions within the settings menu."""
    await query.answer()
    user_id = query.from_user.id

    if temp.lock.get(user_id):
        return await query.answer("A task is in progress. Settings are locked.", show_alert=True)

    try:
        parts = query.data.split("#")
        menu = parts[1]
        
        # Sub-menu navigation
        value = parts[2] if len(parts) > 2 else None

        if menu == "main":
            await query.message.edit_caption(
                caption="<b>֎ Settings ֎</b>\n\nSelect a category to configure.",
                reply_markup=main_buttons()
            )

        # Bots & Userbots Menu
        elif menu == "bots":
            await list_bots(query.message, user_id)
        elif menu == "addbot":
            await prompt_for_input(query, user_id, "awaiting_bot_token", "Send the bot token.")
        elif menu == "adduserbot":
            await prompt_for_input(query, user_id, "awaiting_user_session", "Send the Pyrogram v2 session string.")
        elif menu == "addbots_bulk":
            await prompt_for_input(query, user_id, "awaiting_bots_bulk", "Send a list of bot tokens, separated by spaces or newlines.")
        elif menu == "addusers_bulk":
            await prompt_for_input(query, user_id, "awaiting_users_bulk", "Send a list of session strings, separated by spaces or newlines.")
        elif menu == "editbot":
            await show_bot_details(query.message, user_id, int(value))
        elif menu == "removebot":
            await remove_and_go_back(query, user_id, db.remove_bot, int(value), "Bot removed.", "bots")

        # Channels Menu
        elif menu == "channels":
            await list_channels(query.message, user_id)
        elif menu == "addchannel":
            await prompt_for_input(query, user_id, "awaiting_channel_forward", "Forward a message from the target chat.")
        elif menu == "editchannel":
            await show_channel_details(query.message, user_id, int(value))
        elif menu == "removechannel":
            await remove_and_go_back(query, user_id, db.remove_channel, int(value), "Channel removed.", "channels")

        # Filters & Style Menu
        elif menu == "filters":
            configs = await get_configs(user_id)
            await query.message.edit_caption(
                caption="<b><u>Custom Filters & Style</u></b>\n\nConfigure the type of messages to forward and how they appear.",
                reply_markup=await filters_buttons(configs)
            )
        elif menu.startswith("updatefilter"):
            _, key, current_val_str = menu.split('-')
            new_value = not (current_val_str == 'True')
            await update_configs(user_id, key, new_value)
            await query.edit_message_reply_markup(reply_markup=await filters_buttons(await get_configs(user_id)))

        # Caption Menu
        elif menu == "caption":
            await display_caption_menu(query.message, user_id)
        elif menu == "addcaption":
            await prompt_for_input(query, user_id, "awaiting_caption", Translation.CAPTION_PROMPT)
        elif menu == "seecaption":
            configs = await get_configs(user_id)
            await query.answer(configs.get('caption', 'No caption set.'), show_alert=True)
        elif menu == "deletecaption":
            await update_configs(user_id, 'caption', None)
            await query.answer("Caption deleted.")
            await display_caption_menu(query.message, user_id)

        # Button Menu
        elif menu == "button":
            await display_button_menu(query.message, user_id)
        elif menu == "addbutton":
            await prompt_for_input(query, user_id, "awaiting_button", Translation.BUTTON_PROMPT)
        elif menu == "seebutton":
            configs = await get_configs(user_id)
            button_text = configs.get('button')
            if button_text:
                await query.message.reply_text("Here is your button layout:", reply_markup=parse_buttons(button_text))
            else:
                await query.answer("No button set.", show_alert=True)
        elif menu == "deletebutton":
            await update_configs(user_id, 'button', None)
            await query.answer("Button deleted.")
            await display_button_menu(query.message, user_id)
            
    except Exception as e:
        print(f"Error in settings query handler: {e}")

# +~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+
# Message Handler for Settings Input
# +~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+
@Client.on_message(filters.private & ~filters.command LACK OF &filters.incoming)
async def settings_message_handler(bot: Client, message: Message):
    """Handles text/forwarded inputs for the settings menu."""
    user_id = message.from_user.id
    state = temp.USER_STATES.get(user_id)
    if not state or not state.get("is_settings"):
        return

    prompt_id = state.get("prompt_message_id")
    if prompt_id:
        try: await bot.delete_messages(user_id, prompt_id)
        except Exception: pass
    
    state_type = state.get("state")
    temp.USER_STATES.pop(user_id, None) 
    
    # Create a dummy message object to pass to list_bots/list_channels
    # This avoids errors when trying to edit a message that doesn't exist.
    sent_message = await message.reply_text("`Processing...`")

    if state_type == "awaiting_bot_token":
        if await CLIENT.add_bot(message): await list_bots(sent_message, user_id, as_new=True)
    elif state_type == "awaiting_user_session":
        if await CLIENT.add_session(message): await list_bots(sent_message, user_id, as_new=True)
    elif state_type == "awaiting_bots_bulk":
        if await CLIENT.add_bots_bulk(message): await list_bots(sent_message, user_id, as_new=True)
    elif state_type == "awaiting_users_bulk":
        if await CLIENT.add_sessions_bulk(message): await list_bots(sent_message, user_id, as_new=True)
    elif state_type == "awaiting_channel_forward":
        if message.forward_from_chat:
            await db.add_channel(user_id, message.forward_from_chat.id, message.forward_from_chat.title, message.forward_from_chat.username)
            await message.reply("✅ Channel added.")
            await list_channels(sent_message, user_id, as_new=True)
        else:
            await message.reply("Not a valid forwarded message.")
    elif state_type == "awaiting_caption":
        await update_configs(user_id, 'caption', message.text)
        await message.reply("Caption updated successfully.")
    elif state_type == "awaiting_button":
        if parse_buttons(message.text):
            await update_configs(user_id, 'button', message.text)
            await message.reply("Button layout updated successfully.")
        else:
            await message.reply("Invalid button format.")

    # Clean up the "Processing..." message
    await sent_message.delete()


# +~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+
# UI & Helper Functions
# +~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+
async def prompt_for_input(query, user_id, state, text):
    """Edits a message to prompt user for input and sets their state."""
    prompt = await query.message.edit_caption(f"{text}\n\n/cancel - to abort.")
    temp.USER_STATES[user_id] = {"state": state, "prompt_message_id": prompt.id, "is_settings": True}


async def remove_and_go_back(query, user_id, remove_func, item_id, success_text, back_menu):
    """Generic function to remove an item and refresh the list."""
    await remove_func(user_id, item_id)
    await query.answer(success_text, show_alert=True)
    if back_menu == 'bots':
        await list_bots(query.message, user_id)
    elif back_menu == 'channels':
        await list_channels(query.message, user_id)


async def list_bots(message, user_id, as_new=False):
    """Displays the list of user's bots."""
    bots = await db.get_bots(user_id)
    buttons = [[InlineKeyboardButton(f"🤖 {b['name']}", callback_data=f"settings#editbot#{b['id']}")] for b in bots]
    buttons.extend([
        [InlineKeyboardButton('➕ Add Bot', callback_data="settings#addbot"), InlineKeyboardButton('👤 Add User', callback_data="settings#adduserbot")],
        [InlineKeyboardButton('➕ Add Many Bots', callback_data="settings#addbots_bulk"), InlineKeyboardButton('👥 Add Many Users', callback_data="settings#addusers_bulk")],
        [InlineKeyboardButton('« Back', callback_data="settings#main")]
    ])
    text = f"<b>֎ Bots & Userbots ({len(bots)}) ֎</b>\n\nManage your operator bots and userbots here."
    
    # If called after adding a bot, send a new photo message instead of editing.
    if as_new:
        await message.reply_photo(photo=random.choice(SYD), caption=text, reply_markup=InlineKeyboardMarkup(buttons))
    else:
        await message.edit_caption(caption=text, reply_markup=InlineKeyboardMarkup(buttons))


async def list_channels(message, user_id, as_new=False):
    """Displays the list of user's target channels."""
    channels = await db.get_user_channels(user_id)
    buttons = [[InlineKeyboardButton(f"📢 {c['title']}", callback_data=f"settings#editchannel#{c['chat_id']}")] for c in channels]
    buttons.extend([
        [InlineKeyboardButton('➕ Add Channel', callback_data="settings#addchannel")],
        [InlineKeyboardButton('« Back', callback_data="settings#main")]
    ])
    text = f"<b>֎ Target Channels ({len(channels)}) ֎</b>\n\nManage your destination channels here."
    if as_new:
        await message.reply_photo(photo=random.choice(SYD), caption=text, reply_markup=InlineKeyboardMarkup(buttons))
    else:
        await message.edit_caption(caption=text, reply_markup=InlineKeyboardMarkup(buttons))


async def show_bot_details(message, user_id, bot_id):
    """Shows details for a specific bot."""
    _bot = await db.get_bot(user_id, bot_id)
    uname = f"@{_bot['username']}" if _bot.get('username') else "N/A"
    buttons = [[InlineKeyboardButton('🗑️ Remove', callback_data=f"settings#removebot#{bot_id}")], [InlineKeyboardButton('« Back', callback_data="settings#bots")]]
    TEXT = Translation.BOT_DETAILS if _bot['is_bot'] else Translation.USER_DETAILS
    await message.edit_caption(caption=TEXT.format(_bot['name'], bot_id, uname), reply_markup=InlineKeyboardMarkup(buttons))


async def show_channel_details(message, user_id, chat_id):
    """Shows details for a specific channel."""
    chat = await db.get_channel_details(user_id, int(chat_id))
    buttons = [[InlineKeyboardButton('🗑️ Remove', callback_data=f"settings#removechannel#{chat_id}")], [InlineKeyboardButton('« Back', callback_data="settings#channels")]]
    await message.edit_caption(caption=f"<b>Channel:</b> <code>{chat['title']}</code>\n<b>ID:</b> <code>{chat['chat_id']}</code>", reply_markup=InlineKeyboardMarkup(buttons))


async def display_caption_menu(message, user_id):
    """Displays the caption settings menu."""
    configs = await get_configs(user_id)
    caption_buttons = []
    if configs.get('caption') is None:
        caption_buttons.append([InlineKeyboardButton('➕ Add Caption', callback_data="settings#addcaption")])
    else:
        caption_buttons.append([
            InlineKeyboardButton('👁️ See Caption', callback_data="settings#seecaption"),
            InlineKeyboardButton('🗑️ Delete Caption', callback_data="settings#deletecaption")
        ])
    caption_buttons.append([InlineKeyboardButton('« Back', callback_data="settings#main")])
    await message.edit_caption(
        "<b><u>Custom Caption</u></b>\n\nYou can set a custom caption for forwarded media.",
        reply_markup=InlineKeyboardMarkup(caption_buttons))


async def display_button_menu(message, user_id):
    """Displays the button settings menu."""
    configs = await get_configs(user_id)
    button_buttons = []
    if configs.get('button') is None:
        button_buttons.append([InlineKeyboardButton('➕ Add Button', callback_data="settings#addbutton")])
    else:
        button_buttons.append([
            InlineKeyboardButton('👁️ See Button', callback_data="settings#seebutton"),
            InlineKeyboardButton('🗑️ Delete Button', callback_data="settings#deletebutton")
        ])
    button_buttons.append([InlineKeyboardButton('« Back', callback_data="settings#main")])
    await message.edit_caption(
        "<b><u>Custom Button</u></b>\n\nYou can add an inline button to forwarded messages.",
        reply_markup=InlineKeyboardMarkup(button_buttons))


def main_buttons():
    """Returns the main settings menu keyboard."""
    buttons = [[
        InlineKeyboardButton('🤖 Bots/Userbots', callback_data='settings#bots'),
        InlineKeyboardButton('📢 Channels', callback_data='settings#channels')
    ], [
        InlineKeyboardButton('📝 Caption', callback_data='settings#caption'),
        InlineKeyboardButton('🔘 Button', callback_data='settings#button')
    ], [
        InlineKeyboardButton('🔎 Filters & Style', callback_data='settings#filters')
    ], [
        InlineKeyboardButton('⇇ Back to Start', callback_data='back')
    ]]
    return InlineKeyboardMarkup(buttons)


async def filters_buttons(configs):
    """Returns the filters and style settings keyboard."""
    filters = configs.get('filters', {})
    
    def btn(text, key, val):
        return [
            InlineKeyboardButton(text, callback_data=f'settings#updatefilter-{key}-{val}'),
            InlineKeyboardButton('✅' if val else '❌', callback_data=f'settings#updatefilter-{key}-{val}')
        ]

    buttons = [
        btn('Forward Tag', 'forward_tag', configs.get('forward_tag', False)),
        btn('Text', 'text', filters.get('text', True)),
        btn('Document', 'document', filters.get('document', True)),
        btn('Video', 'video', filters.get('video', True)),
        btn('Photo', 'photo', filters.get('photo', True)),
        btn('Audio', 'audio', filters.get('audio', True)),
        btn('Voice', 'voice', filters.get('voice', True)),
        btn('Animation', 'animation', filters.get('animation', True)),
        btn('Sticker', 'sticker', filters.get('sticker', True)),
        [InlineKeyboardButton('⇇ Back', callback_data="settings#main")]
    ]
    return InlineKeyboardMarkup(buttons)
