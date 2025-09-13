import asyncio
import logging
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.errors import Exception as PyrogramError
from config import temp
from database import db
from .test import CLIENT, start_clone_bot

logger = logging.getLogger(__name__)

# This is the core diagnosis logic for a single operator bot
async def run_single_bot_diagnosis(operator_config, source_chat_id, target_chat_id):
    """
    Starts a client, tests its permissions, and returns a result string.
    """
    bot_name = operator_config.get('name', 'Unknown Bot')
    bot_id = operator_config.get('id', 'N/A')
    report = [f"**Bot: `{bot_name}` (ID: `{bot_id}`)**"]
    
    # Start the client
    try:
        operator_client = await start_clone_bot(CLIENT.client(operator_config), operator_config)
    except Exception as e:
        report.append(f"❌ **Connection:** FAILED TO START")
        report.append(f"   **Error:** `{type(e).__name__}` - {e}")
        return "\n".join(report)

    # 1. Test fetching from the source chat
    try:
        # Check if the bot can see the chat
        await operator_client.get_chat(source_chat_id)
        # Check if the bot can read history
        async for _ in operator_client.get_chat_history(source_chat_id, limit=1):
            pass
        report.append("✅ **Source Access (Fetch):** OK")
    except PyrogramError as e:
        report.append("❌ **Source Access (Fetch):** FAILED")
        report.append(f"   **Error:** `{type(e).__name__}` - {e.MESSAGE}")
    except Exception as e:
        report.append(f"❌ **Source Access (Fetch):** FAILED (Unexpected Error)")
        report.append(f"   **Error:** `{type(e).__name__}` - {e}")


    # 2. Test posting to the target chat
    try:
        test_message = await operator_client.send_message(target_chat_id, "🔬 `Permission diagnosis in progress...`")
        await asyncio.sleep(1) # Give Telegram a moment
        await test_message.delete()
        report.append("✅ **Target Access (Post):** OK")
    except PyrogramError as e:
        report.append("❌ **Target Access (Post):** FAILED")
        report.append(f"   **Error:** `{type(e).__name__}` - {e.MESSAGE}")
    except Exception as e:
        report.append(f"❌ **Target Access (Post):** FAILED (Unexpected Error)")
        report.append(f"   **Error:** `{type(e).__name__}` - {e}")

    # Stop the client
    await operator_client.stop()
    return "\n".join(report)


# This function orchestrates the entire diagnosis process
async def start_diagnosis_process(bot, user_id, prompt_message, source_chat_id, target_chat_id):
    await prompt_message.edit("`🔬 Diagnosis in progress... This may take a moment.`")
    
    operator_configs = await db.get_bots(user_id)
    if not operator_configs:
        return await prompt_message.edit("You have no operator bots configured to diagnose.")

    tasks = [run_single_bot_diagnosis(cfg, source_chat_id, target_chat_id) for cfg in operator_configs]
    results = await asyncio.gather(*tasks)

    final_report = "**🔬 Diagnosis Report**\n\n" + "\n\n".join(results)
    final_report += "\n\n**--- Diagnosis Complete ---**"

    await prompt_message.edit(final_report)


# Handler for the /diagnose command
@Client.on_message(filters.private & filters.command("diagnose"))
async def diagnose_command(client, message):
    user_id = message.from_user.id
    if temp.lock.get(user_id):
        return await message.reply("A task is in progress. Please wait until it's finished to run a diagnosis.")
    
    prompt = await message.reply_text("**Step 1: Set Source Chat**\n\nForward a message from the source chat you want to test.")
    temp.USER_STATES[user_id] = {'state': 'diag_awaiting_source', 'prompt_message_id': prompt.id}


# Handler for when the user forwards the source message
@Client.on_message(filters.private & filters.forwarded)
async def diag_source_handler(bot: Client, message: Message):
    user_id = message.from_user.id
    state = temp.USER_STATES.get(user_id)
    if not state or state.get("state") != 'diag_awaiting_source':
        return

    source_chat_id = message.forward_from_chat.id
    state['source_chat_id'] = source_chat_id
    
    # Clean up previous message
    try:
        await bot.delete_messages(user_id, state['prompt_message_id'])
    except Exception:
        pass

    # Ask for target channel
    channels = await db.get_user_channels(user_id)
    if not channels:
        temp.USER_STATES.pop(user_id, None)
        return await message.reply("No target channels found in `/settings`. Please add one first.")

    buttons = [[InlineKeyboardButton(c['title'], callback_data=f"diag_target_{c['chat_id']}")] for c in channels]
    buttons.append([InlineKeyboardButton("« Cancel", callback_data="close_btn")])
    
    prompt = await message.reply(
        "**Step 2: Select Target Channel**",
        reply_markup=InlineKeyboardMarkup(buttons)
    )
    state.update({'state': 'diag_awaiting_target', 'prompt_message_id': prompt.id})


# Handler for the target channel selection button
@Client.on_callback_query(filters.regex(r'^diag_target_'))
async def cb_select_diag_target(bot, query: CallbackQuery):
    user_id = query.from_user.id
    state = temp.USER_STATES.get(user_id)
    if not state or state.get("state") != 'diag_awaiting_target':
        return await query.answer("Session expired or invalid state.", show_alert=True)

    target_chat_id = int(query.data.split('_')[-1])
    source_chat_id = state['source_chat_id']
    prompt_message = await bot.get_messages(user_id, state['prompt_message_id'])

    temp.USER_STATES.pop(user_id, None) # Clear state
    await query.answer("Starting diagnosis...")

    # Start the main process
    await start_diagnosis_process(bot, user_id, prompt_message, source_chat_id, target_chat_id)
