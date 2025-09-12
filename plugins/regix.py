import asyncio
import logging
import time
import math
from .utils import STS, edit_progress
from database import db
from .test import CLIENT, start_clone_bot
from config import Config, temp
from pyrogram import Client, filters
from pyrogram.errors import FloodWait, MessageIdInvalid
from pyrogram.types import CallbackQuery

logger = logging.getLogger(__name__)

@Client.on_callback_query(filters.regex(r'^start_public_'))
async def pub_(bot, cb: CallbackQuery):
    user_id = cb.from_user.id
    if temp.lock.get(user_id):
        return await cb.answer("Another task is already in progress.", show_alert=True)

    frwd_id = cb.data.split("_")[2]
    session = temp.FORWARD_SESSIONS.get(frwd_id)
    if not session:
        return await cb.message.edit("This task has expired or is invalid.")

    await cb.answer()
    m = await cb.message.edit("`Initializing task...`")

    operator_client = None
    
    try:
        operator_configs = await db.get_bots(user_id)
        if not operator_configs:
            return await m.edit("Error: No Operator Bots/Userbots were found in your settings.")
        
        # --- THIS IS THE CORE FIX ---
        # We fetch the user's custom settings, including the forward delay.
        user_settings = await db.get_configs(user_id)
        delay = user_settings.get('forward_delay', 0.5) # Default to 0.5 if not set
        # --- END OF FIX ---
        
        operator_config = operator_configs[0]

        await m.edit("`Step 1/2: Starting Operator...`")
        operator_client = await start_clone_bot(CLIENT.client(operator_config), operator_config)
        
        target_chat_id = session['to_chat_id']
        source_chat_id = session['from_chat_id']

        await m.edit("`Step 2/2: Verifying channel access...`")
        try:
            await operator_client.get_chat(source_chat_id)
            await operator_client.get_chat(target_chat_id)
        except Exception as e:
             return await m.edit(f"**Access Error:**\nOperator `{operator_config['name']}` cannot access a required channel. Please ensure it is in both the source and target channels.\n\nError: `{e}`")
        
        sts = STS(frwd_id).store(From=source_chat_id, to=target_chat_id, start_id=session['start_id'], end_id=session['end_id'])
        
        temp.ACTIVE_TASKS[user_id] = {frwd_id: {"process": m}}
        temp.lock[user_id] = True

        await m.edit(f"✅ **Setup Complete!**\n\nForwarding from **{session['from_title']}**...")
        
        reporter_task = asyncio.create_task(edit_progress(m, sts, sts.get('start')))
        
        start_id = min(sts.start_id, sts.end_id)
        end_id = max(sts.start_id, sts.end_id)

        message_ids = range(start_id, end_id + 1)
        for chunk in [message_ids[i:i + 200] for i in range(0, len(message_ids), 200)]:
            if temp.CANCEL.get(frwd_id):
                break
            
            messages = await operator_client.get_messages(sts.FROM, chunk)
            for message in messages:
                if temp.CANCEL.get(frwd_id): break
                sts.add('fetched')
                try:
                    await message.copy(sts.TO)
                    sts.add('total_files')
                except FloodWait as e:
                    await asyncio.sleep(e.value + 1)
                    await message.copy(sts.TO)
                    sts.add('total_files')
                except Exception as e:
                    sts.add('failed')
                    logger.warning(f"Failed to copy message {message.id}: {e}")
                
                # Apply the user-defined delay
                await asyncio.sleep(delay)
        
    except Exception as e:
        logger.error(f"A critical error occurred: {e}", exc_info=True)
        await m.edit(f"**A critical error occurred:**\n\n`{type(e).__name__}`: `{e}`")
    finally:
        if 'reporter_task' in locals() and reporter_task:
            reporter_task.cancel()
            await edit_progress(m, sts, sts.get('start'), done=True)

        if operator_client and operator_client.is_connected:
            try: await operator_client.stop()
            except: pass
        
        temp.FORWARD_SESSIONS.pop(frwd_id, None)
        temp.ACTIVE_TASKS.pop(user_id, None)
        temp.CANCEL.pop(user_id, None)
        temp.lock.pop(user_id, None)
