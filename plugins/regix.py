# iangrind/forwardworker/forwardworker-1ff680b8c32922eb74e103a193e108a8d299c7bc/plugins/regix.py

import asyncio
import logging
from .utils import STS, edit_progress
from database import db
from .test import CLIENT, start_clone_bot
from config import Config, temp
from pyrogram import Client, filters
from pyrogram.errors import FloodWait
from pyrogram.types import CallbackQuery

logger = logging.getLogger(__name__)

# The size of message chunks to process. 200 is Pyrogram's max for get_messages.
CHUNK_SIZE = 200

async def forward_worker(worker_id, client, job_queue, sts, delay):
    """
    A worker that processes message chunks in order.
    It pulls a 'chunk' (a range of message IDs) from the queue and forwards them sequentially.
    """
    logger.info(f"Worker {worker_id} ({client.me.first_name}) has started.")
    while True:
        try:
            # Get a chunk of message IDs to process
            chunk = await job_queue.get()
            
            # Fetch all messages in the chunk in a single API call for efficiency
            messages = await client.get_messages(sts.FROM, chunk)
            
            # Process each message within the chunk sequentially to maintain order
            for message in messages:
                if temp.CANCEL.get(sts.id):
                    logger.info(f"Worker {worker_id} received cancellation signal.")
                    job_queue.task_done()
                    return

                try:
                    await message.copy(sts.TO)
                    sts.add('total_files')
                    if delay > 0:
                        await asyncio.sleep(delay)
                except FloodWait as e:
                    logger.warning(f"Worker {worker_id} hit FloodWait for {e.value}s. Pausing this worker and retrying message {message.id}.")
                    await asyncio.sleep(e.value + 5)
                    # Retry the same message after the wait
                    await message.copy(sts.TO)
                    sts.add('total_files')
                except Exception as e:
                    sts.add('failed')
                    logger.error(f"Worker {worker_id} failed to copy message {message.id}: {e}")
                finally:
                    sts.add('fetched') # Mark as processed after attempting

            job_queue.task_done()

        except asyncio.CancelledError:
            logger.info(f"Worker {worker_id} is shutting down.")
            break
        except Exception as e:
            logger.error(f"A critical error occurred in worker {worker_id}: {e}", exc_info=True)
            job_queue.task_done() # Mark task as done to prevent deadlock


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
    m = await cb.message.edit("`Initializing ordered forwarding task...`")
    
    operator_clients = []
    
    try:
        operator_configs = await db.get_bots(user_id)
        if not operator_configs:
            return await m.edit("Error: No Operator Bots/Userbots were found in your settings.")
        
        user_settings = await db.get_configs(user_id)
        delay = user_settings.get('forward_delay', 0)
        
        await m.edit(f"`Step 1/3: Starting {len(operator_configs)} operator(s)...`")
        start_tasks = [start_clone_bot(CLIENT.client(op_config), op_config) for op_config in operator_configs]
        operator_clients = await asyncio.gather(*start_tasks)

        sts = STS(frwd_id).store(From=session['from_chat_id'], to=session['to_chat_id'], start_id=session['start_id'], end_id=session['end_id'])
        
        await m.edit("`Step 2/3: Creating memory-efficient work chunks...`")
        job_queue = asyncio.Queue()
        start_id = min(sts.start_id, sts.end_id)
        end_id = max(sts.start_id, sts.end_id)
        
        # Create a queue of chunks (range objects) instead of individual message IDs
        for i in range(start_id, end_id + 1, CHUNK_SIZE):
            chunk_end = min(i + CHUNK_SIZE - 1, end_id)
            await job_queue.put(range(i, chunk_end + 1))
        
        temp.ACTIVE_TASKS[user_id] = {frwd_id: {"process": m}}
        temp.lock[user_id] = True

        await m.edit(f"✅ **Setup Complete!**\n\n`{sts.total}` messages queued in `{job_queue.qsize()}` chunks.\n`{len(operator_clients)}` workers deployed.\nForwarding from **{session['from_title']}**...")
        
        reporter_task = asyncio.create_task(edit_progress(m, sts, sts.get('start')))
        
        worker_tasks = []
        for i, client in enumerate(operator_clients):
            task = asyncio.create_task(forward_worker(f"Worker-{i+1}", client, job_queue, sts, delay))
            worker_tasks.append(task)
        
        await job_queue.join()
        
        for task in worker_tasks:
            task.cancel()
        
        await asyncio.gather(*worker_tasks, return_exceptions=True)

    except Exception as e:
        logger.error(f"A critical error occurred in the main forwarder: {e}", exc_info=True)
        await m.edit(f"**A critical error occurred:**\n\n`{type(e).__name__}`: `{e}`")
    finally:
        if 'reporter_task' in locals() and not reporter_task.done():
            reporter_task.cancel()
            await edit_progress(m, sts, sts.get('start'), done=True)

        logger.info("Stopping all operator clients...")
        stop_tasks = [client.stop() for client in operator_clients if client.is_connected]
        await asyncio.gather(*stop_tasks, return_exceptions=True)
        
        temp.FORWARD_SESSIONS.pop(frwd_id, None)
        temp.ACTIVE_TASKS.pop(user_id, None)
        temp.CANCEL.pop(user_id, None)
        temp.lock.pop(user_id, None)
        logger.info("Forwarding task has been fully cleaned up.")
