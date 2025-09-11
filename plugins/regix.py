import re
import asyncio
import logging
import math
import time
from .utils import STS
from database import db
from .test import CLIENT, start_clone_bot
from config import Config, temp
from translation import Translation
from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.errors import FloodWait, MessageNotModified, RPCError, MediaEmpty
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery, Message
from itertools import cycle

CLIENT = CLIENT()
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# --- Main Task Starter ---
@Client.on_callback_query(filters.regex(r'^start_public'))
async def pub_(bot, cb):
    user_id = cb.from_user.id
    if temp.lock.get(user_id):
        return await cb.answer("Please wait for the previous task to complete!", show_alert=True)

    frwd_id, session_id = cb.data.split("_")[2:]
    
    session = temp.RANGE_SESSIONS.pop(session_id, None)
    if not session:
        return await cb.answer("This is an old button, please start over.", show_alert=True)

    num_workers = session.get('num_workers', 0)

    temp.CANCEL[frwd_id] = False
    sts = STS(frwd_id)
    if not sts.verify():
        return await cb.answer("This is an old button, please start over.", show_alert=True)

    i = sts.get(full=True)
    m = await msg_edit(cb.message, "Verifying...")

    _bot, caption, forward_tag, data_params, protect, button = await sts.get_data(user_id)
    if not _bot:
        return await msg_edit(m, "You haven't added a bot/userbot. Please do so in /settings.", wait=True)

    delay = data_params.get('forward_delay', 0.5)
    filters_to_apply = data_params.get('filters', []) # Get the filters

    await msg_edit(m, "Starting clients...")
    
    clients = []
    try:
        main_client = await start_clone_bot(CLIENT.client(_bot), _bot)
        clients.append(main_client)
        
        if num_workers > 0:
            worker_configs = await db.get_worker_bots(user_id)
            for worker_config in worker_configs[:num_workers]:
                worker_client = await start_clone_bot(CLIENT.client(worker_config), worker_config)
                clients.append(worker_client)
                
    except Exception as e:
        return await m.edit(f"Failed to start clients: {e}")

    await msg_edit(m, "Accessing channels...")
    try:
        from_chat_details, to_chat_details = await main_client.get_chat(i.FROM), await main_client.get_chat(i.TO)
        from_title, to_title = from_chat_details.title, to_chat_details.title
    except Exception as e:
        await msg_edit(m, f"Error accessing source/target chat: {e}\n\nMake sure your bot/userbot has access.", retry_btn(frwd_id, session_id), True)
        await stop_all(clients, user_id, frwd_id, m)
        return

    if user_id not in temp.ACTIVE_TASKS: temp.ACTIVE_TASKS[user_id] = {}
    temp.ACTIVE_TASKS[user_id][frwd_id] = { "process": m, "details": {"type": "Forwarding", "from": from_title, "to": to_title} }
    temp.lock[user_id] = True
    temp.forwardings += 1
    
    final_status = "error"
    forward_batch = []
    last_update_time = time.time()
    
    client_cycler = cycle(clients)

    try:
        await edit_progress(m, sts, "running")
        
        start, end = (i.start_id, i.end_id) if i.start_id < i.end_id else (i.end_id, i.start_id)
        message_ids = list(range(start, end + 1))

        for chunk_start in range(0, len(message_ids), 200):
            if temp.CANCEL.get(frwd_id):
                final_status = "cancelled"
                break
            
            chunk = message_ids[chunk_start:chunk_start+200]
            
            try:
                messages = await main_client.get_messages(i.FROM, chunk)
            except Exception as e_fetch:
                logger.error(f"Could not fetch message chunk {chunk}: {e_fetch}")
                sts.add('failed', len(chunk))
                sts.add('fetched', len(chunk))
                continue

            for message in messages:
                elapsed_time = time.time() - i.start
                update_interval = 5 if elapsed_time < 60 else 15
                if time.time() - last_update_time > update_interval:
                    await edit_progress(m, sts, "running")
                    last_update_time = time.time()
                
                sts.add('fetched')
                
                if not message or message.empty or message.service:
                    sts.add('deleted')
                    continue
                
                # --- APPLYING MESSAGE TYPE FILTERS ---
                if message.media and str(message.media.value) in filters_to_apply:
                    sts.add('filtered')
                    continue
                if not message.media and "text" in filters_to_apply:
                    sts.add('filtered')
                    continue
                # ------------------------------------

                try:
                    current_client = next(client_cycler)
                    if forward_tag:
                        forward_batch.append(message.id)
                        if len(forward_batch) >= 100:
                            await current_client.forward_messages(
                                chat_id=i.TO, from_chat_id=i.FROM,
                                message_ids=forward_batch, protect_content=protect
                            )
                            sts.add('total_files', len(forward_batch))
                            forward_batch.clear()
                            await asyncio.sleep(max(delay, 2))
                    else:
                        new_caption = custom_caption(message, caption)
                        await message.copy(
                            chat_id=i.TO, caption=new_caption,
                            reply_markup=button, protect_content=protect,
                            client=current_client # Pass the client to the copy method
                        )
                        sts.add('total_files')
                except FloodWait as e:
                    sts.set_status(f"floodwait ({e.value}s)")
                    await edit_progress(m, sts, sts.get('status'))
                    await asyncio.sleep(e.value + 2)
                    sts.set_status("running")
                    try:
                        current_client = next(client_cycler)
                        if forward_tag: 
                            sts.add('failed', len(forward_batch))
                            forward_batch.clear()
                        else: 
                            await message.copy(chat_id=i.TO, caption=new_caption, reply_markup=button, protect_content=protect, client=current_client)
                            sts.add('total_files')
                    except Exception as e_retry:
                        logger.error(f"Retry failed for message {message.id}: {e_retry}")
                        sts.add('failed')
                except Exception as e:
                    logger.error(f"Failed to process message {message.id}: {e}", exc_info=False)
                    sts.add('failed')

                if not forward_tag:
                    await asyncio.sleep(delay)
        
        if forward_tag and forward_batch and not temp.CANCEL.get(frwd_id):
            await main_client.forward_messages(
                chat_id=i.TO, from_chat_id=i.FROM,
                message_ids=forward_batch, protect_content=protect
            )
            sts.add('total_files', len(forward_batch))

        if not temp.CANCEL.get(frwd_id):
            final_status = "completed"

    except Exception as e:
        logger.error(f"Main forwarding loop error: {e}", exc_info=True)
    finally:
        await edit_progress(m, sts, final_status)
        await stop_all(clients, user_id, frwd_id, m)


# --- Callbacks ---
@Client.on_callback_query(filters.regex(r'^frwd_status_'))
async def get_frwd_status(bot, query):
    task_id = query.data.split("_", 2)[2]
    sts = STS(task_id)
    if not sts.verify(): return await query.answer("This task has completed or been cancelled.", show_alert=True)

    i = sts.get(full=True)
    diff = time.time() - i.start
    if diff == 0: diff = 1

    speed = i.fetched / diff
    eta = sts.get_readable_time(int((i.total - i.fetched) / speed if speed > 0 else 0))
    percentage = "{:.2f}".format(i.fetched * 100 / i.total if i.total > 0 else 0.00)

    await query.answer(
        Translation.STATUS_ALERT.format(
            status=i.status, fetched=i.fetched, total=i.total, forwarded=i.total_files,
            failed=i.failed, remaining=(i.total - i.fetched), skipped=i.deleted + i.duplicate + i.filtered, # Added filtered to skipped
            percentage=percentage, eta=eta
        ),
        show_alert=True
    )

# --- Helper functions ---
async def msg_edit(msg, text, button=None, wait=None):
    try:
        return await msg.edit(text, reply_markup=button, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    except MessageNotModified:
        return msg
    except FloodWait as e:
        if wait:
            await asyncio.sleep(e.value)
            return await msg_edit(msg, text, button, wait)
    except Exception as e:
        return msg

async def edit_progress(msg, sts, status):
    i = sts.get(full=True)
    sts.set_status(status)

    button = None
    if status not in ["cancelled", "completed", "error"]:
        diff = time.time() - i.start
        if diff == 0: diff = 1

        eta = sts.get_readable_time(int((i.total - i.fetched) / (i.fetched / diff) if (i.fetched / diff) > 0 else 0))
        percentage = "{:.2f}".format(i.fetched * 100 / i.total if i.total > 0 else 0.00)
        progress_bar = "▰{0}▱{1}".format('▰' * math.floor(float(percentage) / 10), '▱' * (10 - math.floor(float(percentage) / 10)))

        text = Translation.TEXT.format(
            status=status, fetched=i.fetched, total=i.total, forwarded=i.total_files,
            failed=i.failed, skipped=i.deleted + i.filtered, duplicates=i.duplicate, # Added filtered to skipped
            percentage=percentage, eta=eta, progress_bar=progress_bar
        )
        button = InlineKeyboardMarkup([[InlineKeyboardButton(f"📊 Status: {percentage}%", callback_data=f'frwd_status_{i.id}')], [InlineKeyboardButton('❌ Cancel ❌', f'cancel_task_{i.id}')]])
    else:
        # --- NEW CLASSY FORMATTING FOR FINAL MESSAGE ---
        end_time = time.time()
        time_taken = sts.get_readable_time(int(end_time - i.start))
        total_skipped = i.deleted + i.duplicate + i.filtered

        if status == "completed":
            title = "✅ <b>Forwarding Complete</b>"
            line = "━━━━━━━━━━━━━━━━━━━━"
        elif status == "cancelled":
            title = "❌ <b>Task Cancelled</b>"
            line = "━━━━━━━━━━━━━━━━━━━━"
        else:  # error
            title = "⚠️ <b>An Error Occurred</b>"
            line = "━━━━━━━━━━━━━━━━━━━━"

        text = (
            f"{title}\n"
            f"{line}\n"
            f"<b>Time Taken:</b> <code>{time_taken}</code>\n\n"
            f"<b><u>Statistics</u></b>:\n"
            f"  Processed: <code>{i.fetched}</code>\n"
            f"  Forwarded: <code>{i.total_files}</code>\n"
            f"  Skipped:   <code>{total_skipped}</code>\n"
            f"  Failed:    <code>{i.failed}</code>"
        )
        button = InlineKeyboardMarkup([[InlineKeyboardButton("Done!", callback_data="close_btn")]])
        # ---------------------------------------------

    await msg_edit(msg, text, button)

async def stop_all(clients, user_id, task_id, message_obj):
    for client in clients:
        try: await client.stop()
        except: pass
    if temp.ACTIVE_TASKS.get(user_id, {}).get(task_id): del temp.ACTIVE_TASKS[user_id][task_id]
    temp.CANCEL.pop(task_id, None)
    await db.rmve_frwd(user_id)
    if temp.forwardings > 0: temp.forwardings -= 1
    temp.lock.pop(user_id, None)

def custom_caption(msg, caption):
    if not msg: return ""
    
    fcaption_text = ""
    if msg.text:
        fcaption_text = msg.text.html
    elif msg.caption:
        fcaption_text = msg.caption.html
    
    if not caption: return fcaption_text
    
    file_name, file_size = "", "0 B"
    if msg.media:
        media = getattr(msg, msg.media.value, None)
        if media:
            file_name = getattr(media, 'file_name', '')
            file_size = get_size(getattr(media, 'file_size', 0))
    
    return caption.format(filename=file_name, size=file_size, caption=fcaption_text)

def get_size(size):
    try:
        if not size: return "0 B"
        units, size = ["B", "KB", "MB", "GB", "TB"], float(size)
        i = 0
        while size >= 1024.0 and i < len(units) - 1:
            i += 1
            size /= 1024.0
        return f"{size:.2f} {units[i]}"
    except: return "N/A"

def retry_btn(id, session_id):
    return InlineKeyboardMarkup([[InlineKeyboardButton('Retry', f"start_public_{id}_{session_id}")]])
