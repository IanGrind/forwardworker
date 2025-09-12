import asyncio
import logging
import time
import math
from .utils import STS, edit_progress
from database import db
from .test import CLIENT, start_clone_bot
from config import Config, temp
from pyrogram import Client, filters, enums
from pyrogram.errors import FloodWait, UserNotParticipant, PeerIdInvalid, MessageIdInvalid
from pyrogram.types import CallbackQuery

logger = logging.getLogger(__name__)

async def operator_task(worker_id, client, counter, lock, end_id, sts, frwd_id):
    """The core logic for each operator in the pool."""
    while True:
        if temp.CANCEL.get(frwd_id):
            sts.operator_statuses[worker_id] = "Cancelled"
            break

        async with lock:
            current_id = counter['value']
            if current_id > end_id:
                sts.operator_statuses[worker_id] = "Done"
                break
            counter['value'] += 1

        try:
            sts.operator_statuses[worker_id] = f"Copying {current_id}"
            
            # THE CRITICAL STEP: The operator gets the message object itself
            message_to_copy = await client.get_messages(sts.FROM, current_id)
            
            if message_to_copy:
                await message_to_copy.copy(sts.TO)
                sts.add('total_files')

        except MessageIdInvalid:
            # This message was deleted or never existed, which is fine.
            sts.add('failed')
        except FloodWait as e:
            sts.operator_statuses[worker_id] = f"Resting ({e.value}s)"
            async with lock:
                # Decrement the counter so this message will be retried
                counter['value'] -= 1
            await asyncio.sleep(e.value + 1)
        except Exception as e:
            logger.warning(f"Operator {worker_id} failed on message {current_id}: {e}")
            sts.add('failed')
        finally:
            sts.add('fetched')
            # A brief, non-blocking pause
            await asyncio.sleep(0.5)

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

    operator_clients = []
    reporter_task = None
    
    try:
        operator_configs = await db.get_bots(user_id)
        if not operator_configs:
            return await m.edit("Error: No Operator Bots/Userbots were found in your settings.")

        await m.edit("`Step 1/2: Waking up all operators...`")
        for i, config in enumerate(operator_configs):
            await m.edit(f"`Step 1/2: Waking up operator {i+1}/{len(operator_configs)}...`")
            operator_clients.append(await start_clone_bot(CLIENT.client(config), config))
        
        await m.edit("`Step 2/2: Verifying channel access for all operators...`")
        await asyncio.sleep(2) # Give clients a moment to orient
        
        target_chat_id = session['to_chat_id']
        source_chat_id = session['from_chat_id']

        for i, client in enumerate(operator_clients):
            config = operator_configs[i]
            await m.edit(f"`Step 2/2: Verifying {config['name']}...`")
            try:
                await client.get_chat(source_chat_id)
                await client.get_chat(target_chat_id)
            except PeerIdInvalid:
                 return await m.edit(f"**Access Error:**\nOperator `{config['name']}` is not a member of either the source or target channel. Please ensure all operators are in both channels.")
            except Exception as e:
                 return await m.edit(f"**Setup Error:**\nCould not verify channel access for `{config['name']}`. Error: `{e}`")
        
        sts = STS(frwd_id).store(From=source_chat_id, to=target_chat_id, start_id=session['start_id'], end_id=session['end_id'])
        
        counter = {'value': sts.start_id}
        lock = asyncio.Lock()
        
        sts.operator_statuses = {i: "Idle" for i in range(len(operator_clients))}

        temp.ACTIVE_TASKS[user_id] = {frwd_id: {"process": m}}
        temp.lock[user_id] = True

        await m.edit(f"✅ **Setup Complete!**\n\nStarting forward from **{session['from_title']}**...")
        
        reporter_task = asyncio.create_task(edit_progress(m, sts, sts.get('start')))
        
        operator_coroutines = [
            operator_task(i, client, counter, lock, sts.end_id, sts, frwd_id)
            for i, client in enumerate(operator_clients)
        ]
        
        await asyncio.gather(*operator_coroutines)
        
    except Exception as e:
        logger.error(f"A critical error occurred: {e}", exc_info=True)
        await m.edit(f"**A critical error occurred:**\n\n`{type(e).__name__}`: `{e}`")
    finally:
        if reporter_task:
            reporter_task.cancel()
            await edit_progress(m, sts, sts.get('start'), done=True)

        for client in operator_clients:
            if client and client.is_connected:
                try: await client.stop()
                except: pass
        
        temp.FORWARD_SESSIONS.pop(frwd_id, None)
        temp.ACTIVE_TASKS.pop(user_id, None)
        temp.CANCEL.pop(frwd_id, None)
        temp.lock.pop(user_id, None)
