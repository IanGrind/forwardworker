import asyncio
import logging
import time
import math
from .utils import STS, edit_progress
from database import db
from .test import CLIENT, start_clone_bot
from config import Config, temp
from pyrogram import Client, filters, enums
from pyrogram.errors import FloodWait, UserNotParticipant, PeerIdInvalid, ChatAdminRequired
from pyrogram.types import CallbackQuery, ChatPrivileges

logger = logging.getLogger(__name__)

async def worker_task(worker_id, client, counter, lock, end_id, sts, frwd_id):
    """The core logic for each worker bot using a shared counter."""
    while True:
        if temp.CANCEL.get(frwd_id):
            sts.worker_statuses[worker_id] = "Cancelled"
            break

        async with lock:
            current_id = counter['value']
            if current_id > end_id:
                sts.worker_statuses[worker_id] = "Done"
                break
            counter['value'] += 1

        try:
            sts.worker_statuses[worker_id] = f"Forwarding {current_id}"
            await client.copy_message(
                chat_id=sts.TO,
                from_chat_id=sts.FROM,
                message_id=current_id
            )
            sts.add('total_files')
        except FloodWait as e:
            sts.worker_statuses[worker_id] = f"Resting ({e.value}s)"
            async with lock:
                counter['value'] -= 1
            await asyncio.sleep(e.value + 1)
        except Exception as e:
            logger.warning(f"Worker {worker_id} failed on message {current_id}: {e}")
            sts.add('failed')
        finally:
            sts.add('fetched')
            await asyncio.sleep(0.2)

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
    worker_clients = []
    reporter_task = None
    
    try:
        operator_config = await db.get_bot(user_id, session['bot_id'])
        worker_configs = await db.get_worker_bots(user_id)

        if not operator_config or not worker_configs:
            return await m.edit("Error: Operator or Worker Bot configurations were not found.")

        await m.edit("`Step 1/3: Waking up clients...`")
        operator_client = await start_clone_bot(CLIENT.client(operator_config), operator_config)
        for i, config in enumerate(worker_configs):
            await m.edit(f"`Step 1/3: Waking up worker {i+1}/{len(worker_configs)}...`")
            worker_clients.append(await start_clone_bot(CLIENT.client(config), config))
        
        await m.edit("`Step 2/3: Orienting clients and verifying access...`")
        await asyncio.sleep(2)
        
        target_chat_id = session['to_chat_id']
        source_chat_id = session['from_chat_id']

        try:
            await operator_client.get_chat(source_chat_id)
            for worker in worker_clients:
                await worker.get_chat(target_chat_id)
        except PeerIdInvalid as e:
             return await m.edit(f"**Access Error:** A bot is not a member of a required channel. Please check your setup.\nDetails: {e}")
        
        sts = STS(frwd_id).store(From=source_chat_id, to=target_chat_id, start_id=session['start_id'], end_id=session['end_id'])
        
        counter = {'value': sts.start_id}
        lock = asyncio.Lock()
        
        sts.worker_statuses = {i: "Idle" for i in range(len(worker_clients))}

        temp.ACTIVE_TASKS[user_id] = {frwd_id: {"process": m}}
        temp.lock[user_id] = True

        await m.edit(f"`Step 3/3: Setup Complete!`\n\nStarting forward from **{session['from_title']}**...")
        await asyncio.sleep(2)

        # Start the progress reporter as a background task
        reporter_task = asyncio.create_task(edit_progress(m, sts, sts.get('start')))
        
        worker_coroutines = [
            worker_task(i, client, counter, lock, sts.end_id, sts, frwd_id)
            for i, client in enumerate(worker_clients)
        ]
        
        await asyncio.gather(*worker_coroutines)
        
    except Exception as e:
        logger.error(f"A critical error occurred in the forwarding task: {e}", exc_info=True)
        await m.edit(f"**A critical error occurred:**\n\n`{type(e).__name__}`: `{e}`")
    finally:
        # Stop the reporter task gracefully and send a final update.
        if reporter_task:
            reporter_task.cancel()
            await edit_progress(m, sts, sts.get('start'), done=True)

        all_clients = [operator_client] + worker_clients
        for client in all_clients:
            if client and client.is_connected:
                try: await client.stop()
                except: pass
        
        temp.FORWARD_SESSIONS.pop(frwd_id, None)
        temp.ACTIVE_TASKS.pop(user_id, None)
        temp.CANCEL.pop(frwd_id, None)
        temp.lock.pop(user_id, None)
