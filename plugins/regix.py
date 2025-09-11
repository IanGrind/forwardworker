import re
import asyncio
import logging
import math
import time
from itertools import cycle
from .utils import STS
from database import db
from .test import CLIENT, start_clone_bot
from config import Config, temp
from translation import Translation
from pyrogram import Client, filters
from pyrogram.enums import ParseMode, ChatMemberStatus
from pyrogram.errors import FloodWait, MessageNotModified, UserNotParticipant, UserAlreadyParticipant
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery, Message, ChatPrivileges

CLIENT = CLIENT()
logger = logging.getLogger(__name__)

@Client.on_callback_query(filters.regex(r'^start_public_'))
async def pub_(bot, cb):
    user_id = cb.from_user.id
    if temp.lock.get(user_id):
        return await cb.answer("Task in progress.", show_alert=True)

    frwd_id = cb.data.split("_")[2]
    session = temp.RANGE_SESSIONS.pop(frwd_id, None)
    if not session:
        return await cb.message.edit("This task has expired.")

    num_workers = session.get('num_workers', 0)
    temp.CANCEL[frwd_id] = False
    sts = STS(frwd_id)
    if not sts.verify(): return await cb.answer("Task verification failed.", show_alert=True)

    i = sts.get(full=True)
    m = await cb.message.edit("Verifying...")

    _bot, _, _, data_params, _, _ = await sts.get_data(user_id)
    delay = data_params.get('forward_delay', 0.5)

    await m.edit("Starting clients...")
    main_client, worker_clients = None, []
    try:
        main_client = await start_clone_bot(CLIENT.client(_bot), _bot)
        if num_workers > 0:
            for config in (await db.get_worker_bots(user_id))[:num_workers]:
                worker_clients.append(await start_clone_bot(CLIENT.client(config), config))
    except Exception as e:
        return await m.edit(f"Failed to start clients: {e}")

    try:
        from_title = (await main_client.get_chat(i.FROM)).title
        to_title = (await main_client.get_chat(i.TO)).title
    except Exception as e:
        await m.edit(f"Error accessing chats: {e}")
        await stop_all([main_client] + worker_clients, user_id, frwd_id, m)
        return

    if num_workers > 0:
        manager_config = await db.get_manager_userbot(user_id)
        if not manager_config:
            await m.edit("❌ **Manager Userbot not set.** Please set one in /settings.")
            await stop_all([main_client] + worker_clients, user_id, frwd_id, m)
            return
        
        manager_client = None
        try:
            await m.edit("Manager Userbot is setting up workers...")
            manager_client = await start_clone_bot(CLIENT.client(manager_config), manager_config)
            
            try: await manager_client.join_chat((await main_client.export_chat_invite_link(i.TO)))
            except UserAlreadyParticipant: pass

            for worker in worker_clients:
                try: await manager_client.add_chat_members(i.TO, worker.me.id)
                except UserAlreadyParticipant: pass
                await manager_client.promote_chat_member(i.TO, worker.me.id, privileges=ChatPrivileges(can_post_messages=True))
        except Exception as e:
            await m.edit(f"Worker setup failed: {e}\n\nPlease ensure the Manager has admin rights in the target channel.")
            await stop_all([main_client] + worker_clients + ([manager_client] if manager_client else []), user_id, frwd_id, m)
            return
        finally:
            if manager_client: await manager_client.stop()

    temp.ACTIVE_TASKS[user_id] = {frwd_id: {"process": m, "details": {"type": "Forwarding", "from": from_title, "to": to_title}}}
    temp.lock[user_id] = True
    
    clients = worker_clients if worker_clients else [main_client]
    client_cycler = cycle(clients)
    
    try:
        message_ids = range(i.start_id, i.end_id + 1) if i.start_id < i.end_id else range(i.start_id, i.end_id - 1, -1)
        for chunk in [message_ids[x:x + 200] for x in range(0, len(message_ids), 200)]:
            if temp.CANCEL.get(frwd_id): break
            messages = await main_client.get_messages(i.FROM, chunk)
            for message in messages:
                if temp.CANCEL.get(frwd_id): break
                sts.add('fetched')
                try:
                    await next(client_cycler).copy_message(i.TO, i.FROM, message.id)
                    sts.add('total_files')
                except FloodWait as e:
                    await asyncio.sleep(e.value)
                    await next(client_cycler).copy_message(i.TO, i.FROM, message.id)
                    sts.add('total_files')
                except Exception: sts.add('failed')
                await asyncio.sleep(delay)
        final_status = "cancelled" if temp.CANCEL.get(frwd_id) else "completed"
    except Exception as e:
        logger.error(f"Forwarding loop error: {e}", exc_info=True)
        final_status = "error"
    finally:
        await m.edit(f"Forwarding {final_status}.")
        await stop_all([main_client] + worker_clients, user_id, frwd_id, m)

async def stop_all(clients, user_id, task_id, m):
    for client in clients:
        if client and client.is_connected:
            await client.stop()
    temp.ACTIVE_TASKS.pop(user_id, None)
    temp.CANCEL.pop(task_id, None)
    temp.lock.pop(user_id, None)
