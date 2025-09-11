import asyncio
import logging
from itertools import cycle
from .utils import STS
from database import db
from .test import CLIENT, start_clone_bot
from config import Config, temp
from pyrogram import Client, filters
from pyrogram.errors import FloodWait, UserAlreadyParticipant, PeerIdInvalid, ChatAdminRequired
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

        await m.edit("`Step 1/3: Starting Manager Userbot...`")
        manager_client = await start_clone_bot(CLIENT.client(manager_config), manager_config)
        
        target_chat_id = session['to_chat_id']
        try:
            await manager_client.get_chat(target_chat_id)
        except PeerIdInvalid:
            return await m.edit(f"**Setup Error:**\nManager Userbot (`{manager_config['name']}`) is not in the target channel. Please add it and try again.")
        except Exception as e:
            return await m.edit(f"**Setup Error:**\nCould not access target channel with Manager Userbot. Error: `{e}`")

        await m.edit("`Step 1/3: Starting Worker Bots...`")
        for i, config in enumerate(worker_configs):
            await m.edit(f"`Step 1/3: Starting Worker Bot {i+1}/{len(worker_configs)}...`")
            worker_clients.append(await start_clone_bot(CLIENT.client(config), config))

        await m.edit("`Step 2/3: Manager is promoting workers...`")
        
        # --- THIS IS THE CORE FIX ---
        # We now grant a standard set of safe, non-destructive admin privileges
        # to the worker bots. This satisfies the Telegram API's requirements.
        worker_privileges = ChatPrivileges(
            can_post_messages=True,
            can_edit_messages=True,
            can_delete_messages=True,
            can_invite_users=True
        )

        for i, worker in enumerate(worker_clients):
            await m.edit(f"`Step 2/3: Promoting worker {i+1}/{len(worker_clients)}...`")
            try:
                await manager_client.promote_chat_member(
                    chat_id=target_chat_id,
                    user_id=worker.me.id,
                    privileges=worker_privileges
                )
            except ChatAdminRequired as e:
                 return await m.edit(f"**Setup Error:**\nManager Userbot failed to promote worker `{worker.me.first_name}`.\nError: `{e}`\n\n**Troubleshooting:**\n1. Ensure the Manager Userbot is an admin in the target channel.\n2. Ensure the Manager Userbot has the 'Add New Admins' permission.\n3. Check if the channel owner has 'Remain Anonymous' enabled, as this can sometimes cause permission issues.")
            except Exception as e:
                 return await m.edit(f"**Setup Error:**\nAn unexpected error occurred while promoting worker `{worker.me.first_name}`.\nError: `{e}`")


        await m.edit("`Step 3/3: Starting Fetcher Client...`")
        fetcher_client = await start_clone_bot(CLIENT.client(fetcher_config), fetcher_config)

        sts = STS(frwd_id).store(From=session['from_chat_id'], to=target_chat_id, start_id=session['start_id'], end_id=session['end_id'])
        
        temp.ACTIVE_TASKS[user_id] = {frwd_id: {"process": m, "details": {"type": "Forwarding", "from": session['from_title'], "to": "N/A"}}}
        temp.lock[user_id] = True
        
        client_cycler = cycle(worker_clients)
        
        await m.edit(f"✅ **Setup Complete!**\n\nForwarding from **{session['from_title']}**...")
        
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
                await asyncio.sleep(0.1)
        
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
