# iangrind/forwardworker/forwardworker-1ff680b8c32922eb74e103a193e108a8d299c7bc/plugins/regix.py

import asyncio
import logging
from collections import deque
from .utils import STS, edit_progress
from database import db
from .test import CLIENT, start_clone_bot
from config import Config, temp
from pyrogram import Client, filters
from pyrogram.errors import FloodWait
from pyrogram.types import CallbackQuery

logger = logging.getLogger(__name__)
BATCH_SIZE = 100 # Telegram API's limit for forward_messages

class WorkerManager:
    """
    Manages a pool of operator clients for high-speed, ordered batch forwarding.
    Only one worker is active at a time to preserve order. If it hits a FloodWait,
    it goes into cooldown and another worker is activated instantly.
    """
    def __init__(self, operator_clients, job_queue, sts, delay):
        self.clients = deque(operator_clients)
        self.job_queue = job_queue
        self.sts = sts
        self.delay_between_batches = delay
        self.cooldown_workers = {}
        self.is_cancelled = False

    async def start(self):
        logger.info(f"WorkerManager started with {len(self.clients)} workers for batch forwarding.")
        while not self.job_queue.empty() and not self.is_cancelled:
            if not self.clients:
                if self.cooldown_workers:
                    first_worker_cooldown_end = min(self.cooldown_workers.values())
                    wait_time = max(0, first_worker_cooldown_end - asyncio.get_running_loop().time())
                    logger.warning(f"All workers in cooldown. Waiting for {wait_time:.2f}s...")
                    await asyncio.sleep(wait_time)
                    self.check_cooldowns()
                else:
                    logger.error("No available workers. Halting.")
                    break
                continue

            active_client = self.clients.popleft()
            
            try:
                await self.process_batch(active_client)
                self.clients.append(active_client)
                if self.delay_between_batches > 0:
                    await asyncio.sleep(self.delay_between_batches)
            except FloodWait as e:
                cooldown_duration = e.value + 5
                logger.warning(f"Worker {active_client.me.first_name} hit FloodWait. Cooldown for {cooldown_duration}s.")
                self.cooldown_workers[active_client] = asyncio.get_running_loop().time() + cooldown_duration
            except Exception:
                logger.error(f"Worker {active_client.me.first_name} encountered a critical error. Cycling worker.", exc_info=True)
                self.clients.append(active_client)
            
            self.check_cooldowns()
    
    async def process_batch(self, client):
        message_batch = self.job_queue.popleft()
        
        try:
            await client.forward_messages(
                chat_id=self.sts.TO,
                from_chat_id=self.sts.FROM,
                message_ids=message_batch
            )
            self.sts.add('total_files', len(message_batch))
            self.sts.add('fetched', len(message_batch))
        except FloodWait as e:
            self.job_queue.appendleft(message_batch)
            raise e
        except Exception as e:
            self.sts.add('failed', len(message_batch))
            self.sts.add('fetched', len(message_batch))
            self.job_queue.appendleft(message_batch)
            logger.error(f"Failed to process batch starting with {message_batch[0]}: {e}")

    def check_cooldowns(self):
        now = asyncio.get_running_loop().time()
        ready_workers = [worker for worker, end_time in self.cooldown_workers.items() if now >= end_time]
        
        for worker in ready_workers:
            logger.info(f"Worker {worker.me.first_name} cooldown finished. Returning to pool.")
            self.clients.append(worker)
            del self.cooldown_workers[worker]
            
    def cancel(self):
        self.is_cancelled = True

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
    m = await cb.message.edit("`Initializing high-speed batch forwarding...`")
    
    operator_clients = []
    
    try:
        operator_configs = await db.get_bots(user_id)
        if not operator_configs:
            return await m.edit("Error: No Operator Bots/Userbots were found.")
        
        user_settings = await db.get_configs(user_id)
        delay = user_settings.get('forward_delay', 0)
        
        await m.edit(f"`Step 1/2: Starting {len(operator_configs)} operator(s)...`")
        start_tasks = [start_clone_bot(CLIENT.client(op_config), op_config) for op_config in operator_configs]
        operator_clients = await asyncio.gather(*start_tasks)

        sts = STS(frwd_id).store(From=session['from_chat_id'], to=session['to_chat_id'], start_id=session['start_id'], end_id=session['end_id'])
        
        await m.edit("`Step 2/2: Populating ordered batch queue...`")
        start_id = min(sts.start_id, sts.end_id)
        end_id = max(sts.start_id, sts.end_id)
        
        job_queue = deque()
        for i in range(start_id, end_id + 1, BATCH_SIZE):
            job_queue.append(list(range(i, min(i + BATCH_SIZE, end_id + 1))))
        
        temp.ACTIVE_TASKS[user_id] = {frwd_id: {"process": m}}
        temp.lock[user_id] = True

        await m.edit(f"✅ **Setup Complete!**\n\n`{sts.total}` messages in `{len(job_queue)}` batches.\n`{len(operator_clients)}` workers in failover pool.\nForwarding from **{session['from_title']}**...")
        
        reporter_task = asyncio.create_task(edit_progress(m, sts, sts.get('start')))
        
        manager = WorkerManager(operator_clients, job_queue, sts, delay)
        
        async def cancel_checker():
            while not manager.is_cancelled:
                if temp.CANCEL.get(frwd_id):
                    manager.cancel()
                    break
                await asyncio.sleep(1)

        cancel_task = asyncio.create_task(cancel_checker())
        await manager.start()

    except Exception as e:
        logger.error(f"A critical error occurred: {e}", exc_info=True)
        await m.edit(f"**A critical error occurred:**\n\n`{type(e).__name__}`: `{e}`")
    finally:
        if 'cancel_task' in locals() and not cancel_task.done(): cancel_task.cancel()
        if 'reporter_task' in locals() and not reporter_task.done(): reporter_task.cancel()
        await edit_progress(m, sts, sts.get('start'), done=True)

        logger.info("Stopping all operator clients...")
        stop_tasks = [client.stop() for client in operator_clients if client.is_connected]
        await asyncio.gather(*stop_tasks, return_exceptions=True)
        
        temp.FORWARD_SESSIONS.pop(frwd_id, None)
        temp.ACTIVE_TASKS.pop(user_id, None)
        temp.CANCEL.pop(frwd_id, None)
        temp.lock.pop(user_id, None)
        logger.info("Forwarding task fully cleaned up.")
