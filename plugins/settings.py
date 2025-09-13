import asyncio
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from database import db
from plugins.parser import parse_buttons
from plugins.test import CLIENT
from translation import Translation


async def get_configs(user_id):
    return await db.get_configs(user_id)


async def update_configs(user_id, key, value):
    configs = await get_configs(user_id)
    if key in ['caption', 'duplicate', 'db_uri', 'forward_tag', 'protect', 'file_size', 'size_limit', 'extension',
               'keywords', 'button', 'forward_delay']:
        configs[key] = value
    else:
        # This handles filter updates
        configs.setdefault('filters', {})[key] = value
    await db.update_configs(user_id, configs)


@Client.on_message(filters.private & filters.command(['settings']))
async def settings(client, message):
    """
    Entry point for the settings menu.
    """
    await message.reply_text(
        text="<b>Change your settings as per your needs! ❄️</b>",
        reply_markup=main_buttons(),
        quote=True
    )


@Client.on_callback_query(filters.regex(r'^settings'))
async def settings_query(bot, query):
    user_id = query.from_user.id
    parts = query.data.split("#")
    type = parts[1] if len(parts) > 1 else "main"
    
    back_button_main = [[InlineKeyboardButton('⇇ Back', callback_data="settings#main")]]

    if type == "main":
        await query.message.edit_text(
            "<b>Change your settings as per your needs! ❄️</b>",
            reply_markup=main_buttons())
        return

    configs = await get_configs(user_id)

    if type == "filters":
        await query.message.edit_text(
            "<b><u>Custom Filters & Style</u></b>\n\nConfigure the type of messages you want to forward and how they appear.",
            reply_markup=await filters_buttons(configs))
    elif type.startswith("updatefilter"):
        _, key, value = query.data.split('-')
        new_value = (value == "False") # Toggle boolean
        await update_configs(user_id, key, new_value)
        await query.edit_message_reply_markup(
            reply_markup=await filters_buttons(await get_configs(user_id)))
    elif type == "forward_tag":
        current_value = configs.get('forward_tag', False)
        await update_configs(user_id, 'forward_tag', not current_value)
        await query.edit_message_reply_markup(reply_markup=await filters_buttons(await get_configs(user_id)))

    elif type == "caption":
        caption_buttons = []
        if configs.get('caption') is None:
            caption_buttons.append([InlineKeyboardButton('➕ Add Caption', callback_data="settings#addcaption")])
        else:
            caption_buttons.append([
                InlineKeyboardButton('👁️ See Caption', callback_data="settings#seecaption"),
                InlineKeyboardButton('🗑️ Delete Caption', callback_data="settings#deletecaption")
            ])
        caption_buttons.extend(back_button_main)
        await query.message.edit_text(
            "<b><u>Custom Caption</u></b>\n\nYou can set a custom caption.\n\n"
            "<b>Available Fillings:</b>\n"
            "<code>{filename}</code>: Filename\n"
            "<code>{size}</code>: File Size\n"
            "<code>{caption}</code>: Original Caption",
            reply_markup=InlineKeyboardMarkup(caption_buttons))

    elif type == "addcaption":
        await query.message.delete()
        try:
            ask_msg = await bot.ask(user_id, "Send your custom caption.\n\nUse placeholders like `{filename}` and `{caption}`.\n\n/cancel to abort.", timeout=300)
            if ask_msg.text == "/cancel":
                return await ask_msg.reply("Cancelled.")
            await update_configs(user_id, 'caption', ask_msg.text)
            await ask_msg.reply("Caption updated successfully.", reply_markup=InlineKeyboardMarkup(back_button_main))
        except asyncio.TimeoutError:
            await bot.send_message(user_id, "Timed out.")

    elif type == "seecaption":
        await query.answer(configs.get('caption', 'No caption set.'), show_alert=True)
    
    elif type == "deletecaption":
        await update_configs(user_id, 'caption', None)
        await query.answer("Caption deleted.")
        query.data = "settings#caption" # Refresh menu
        await settings_query(bot, query)

    elif type == "button":
        button_buttons = []
        if configs.get('button') is None:
            button_buttons.append([InlineKeyboardButton('➕ Add Button', callback_data="settings#addbutton")])
        else:
            button_buttons.append([
                InlineKeyboardButton('👁️ See Button', callback_data="settings#seebutton"),
                InlineKeyboardButton('🗑️ Delete Button', callback_data="settings#deletebutton")
            ])
        button_buttons.extend(back_button_main)
        await query.message.edit_text(
            "<b><u>Custom Button</u></b>\n\nYou can set an inline button.\n\n"
            "<b>Format:</b>\n`[Button Text](buttonurl:https://example.com)`\n`[Button 1](url) | [Button 2](url)`",
            reply_markup=InlineKeyboardMarkup(button_buttons))

    elif type == "addbutton":
        await query.message.delete()
        try:
            ask_msg = await bot.ask(user_id, "Send your custom button text in the correct format.\n/cancel to abort.", timeout=300)
            if ask_msg.text == "/cancel":
                return await ask_msg.reply("Cancelled.")
            
            if not parse_buttons(ask_msg.text):
                return await ask_msg.reply("Invalid button format.", reply_markup=InlineKeyboardMarkup(back_button_main))

            await update_configs(user_id, 'button', ask_msg.text)
            await ask_msg.reply("Button updated successfully.", reply_markup=InlineKeyboardMarkup(back_button_main))
        except asyncio.TimeoutError:
            await bot.send_message(user_id, "Timed out.")

    elif type == "seebutton":
         button_text = configs.get('button')
         if button_text:
             await query.message.reply_text("Here is your button layout:", reply_markup=parse_buttons(button_text))
         else:
             await query.answer("No button set.", show_alert=True)

    elif type == "deletebutton":
        await update_configs(user_id, 'button', None)
        await query.answer("Button deleted.")
        query.data = "settings#button" # Refresh menu
        await settings_query(bot, query)


def main_buttons():
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
