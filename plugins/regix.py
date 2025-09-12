import asyncio
import logging
import time
import math
from itertools import cycle
from .utils import STS, edit_progress
from database import db
from .test import CLIENT, start_clone_bot
from config import Config, temp
from pyrogram import Client, filters, enums
from pyrogram.errors import FloodWait, UserNotParticipant, PeerIdInvalid, ChatAdminRequired
from pyrogram.types import CallbackQuery, ChatPrivileges

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

    fetcher_client = None
    manager_client = None
    worker_clients = []
    
    try:
        fetcher_config = await db.get_bot(user_id, session['bot_id'])
        manager_config = await db.get_manager_userbot(user_id)
        worker_configs = (await db.get_worker_bots(user_id))[:session['num_workers']]

        if not fetcher_config or not manager_config or not worker_configs:
            return await m.edit("Error: A required bot/userbot configuration was not found.")

        # --- THIS IS THE CORE FIX ---
        # All clients now perform a "wake-up" routine to populate their internal chat cache.
        await m.edit("`Step 1/4: Waking up clients...`")
        manager_client = await start_clone_bot(CLIENT.client(manager_config), manager_config)
        async for _ in manager_client.get_dialogs(limit=1): pass # Wake-up call

        fetcher_client = await start_clone_bot(CLIENT.client(fetcher_config), fetcher_config)
        async for _ in fetcher_client.get_dialogs(limit=1): pass # Wake-up call

        for i, config in enumerate(worker_configs):
            await m.edit(f"`Step 1/4: Waking up worker {i+1}/{len(worker_configs)}...`")
            worker_clients.append(await start_clone_bot(CLIENT.client(config), config))
        # --- END OF WAKE-UP ROUTINE ---
        
        target_chat_id = session['to_chat_id']
        source_chat_id = session['from_chat_id']

        await m.edit("`Step 2/4: Verifying channel access...`")
        try:
            await fetcher_client.get_chat(source_chat_id)
        except PeerIdInvalid:
             return await m.edit(f"**Setup Error:**\nThe Fetcher Bot/Userbot (`{fetcher_config['name']}`) is not a member of the source channel. Please add it and try again.")
        except Exception as e:
            return await m.edit(f"**Setup Error:**\nCould not access source channel with Fetcher. Error: `{e}`")

        await m.edit("`Step 3/4: Promoting workers...`")
        worker_privileges = ChatPrivileges(can_post_messages=True, can_edit_messages=True, can_delete_messages=True)

        for i, worker in enumerate(worker_clients):
            worker_username = worker.me.username
            if not worker_username:
                return await m.edit(f"**Setup Error:**\nWorker Bot `{worker.me.first_name}` does not have a public @username. Please set one in @BotFather.")
            
            await m.edit(f"`Step 3/4: Checking status of @{worker_username}...`")
            try:
                member = await manager_client.get_chat_member(target_chat_id, worker.me.id)
                if member.status == enums.ChatMemberStatus.ADMINISTRATOR and member.privileges and member.privileges.can_post_messages:
                    continue
            except UserNotParticipant:
                pass 
            
            await m.edit(f"`Step 3/4: Promoting @{worker_username}...`")
            try:
                await manager_client.promote_chat_member(target_chat_id, f"@{worker_username}", privileges=worker_privileges)
            except Exception as e:
                 return await m.edit(f"**Setup Error:**\nManager failed to promote `@{worker_username}`.\nError: `{e}`")
        
        sts = STS(frwd_id).store(From=source_chat_id, to=target_chat_id, start_id=session['start_id'], end_id=session['end_id'])
        
        temp.ACTIVE_TASKS[user_id] = {frwd_id: {"process": m, "details": {"type": "Forwarding", "from": session['from_title'], "to": "N/A"}}}
        temp.lock[user_id] = True
        
        client_cycler = cycle(worker_clients)
        
        await m.edit(f"✅ **Step 4/4: Setup Complete!**\n\nForwarding from **{session['from_title']}**...")
        start_time = time.time()
        last_edit_time = start_time
        
        message_ids = range(sts.start_id, sts.end_id + 1)
        for chunk in [message_ids[i:i + 200] for i in range(0, len(message_ids), 200)]:
            if temp.CANCEL.get(frwd_id):
                await m.edit("Task cancelled by user.")
                break
            
            messages = await fetcher_client.get_messages(sts.FROM, chunk)
            for message in messages:
                if temp.CANCEL.get(frwd_id): break
                sts.add('fetched')
                try:
                    await next(client_cycler).copy_message(sts.TO, sts.FROM, message.id)
                    sts.add('total_files')
                except FloodWait as e:
                    await asyncio.sleep(e.value + 1)
                    await next(client_cycler).copy_message(sts.TO, sts.FROM, message.id)
                    sts.add('total_files')
                except Exception as e:
                    logger.warning(f"Failed to copy message {message.id}: {e}")
                    sts.add('failed')
                
                current_time = time.time()
                if current_time - last_edit_time > 5:
                    await edit_progress(m, sts, start_time)
                    last_edit_time = current_time
        
        if not temp.CANCEL.get(frwd_id):
            await m.edit("✅ **Forwarding Complete!**")

    except Exception as e:
        logger.error(f"A critical error occurred in the forwarding task: {e}", exc_info=True)
        await m.edit(f"**A critical error occurred:**\n\n`{type(e).__name__}`: `{e}`\n\nPlease check the logs.")
    finally:
        all_clients = [fetcher_client, manager_client] + worker_clients
        for client in all_clients:
            if client and client.is_connected:
                try: await client.stop()
                except: pass
        
        temp.FORWARD_SESSIONS.pop(frwd_id, None)
        temp.ACTIVE_TASKS.pop(user_id, None)
        temp.CANCEL.pop(frwd_id, None)
        temp.lock.pop(user_id, None)
