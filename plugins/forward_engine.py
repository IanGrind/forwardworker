import os
import sys
import asyncio
import random
import logging
import re
import string
from uuid import uuid4
from collections import deque
from database import db
from config import Config, temp
from translation import Translation
from .utils import start_range_selection, update_range_message, STS, edit_progress, get_size, progress_message_content
from .parser import parse_buttons
from .test import CLIENT, start_clone_bot
from pyrogram import Client, filters, enums
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, CallbackQuery
from pyrogram.errors import FloodWait, MessageNotModified

SYD = ["https://files.catbox.moe/3lwlbm.png"]
logger = logging.getLogger(__name__)
BATCH_SIZE = 100 
OPERATOR_START_TIMEOUT = 30

def generate_short_id(length=8):
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=length))

def should_skip(message, configs):
    f_config = configs.get('filters', {})
    if not message: return True
    if message.empty or message.service: return True
    
    if not f_config.get('text', True) and not message.media: return True
    if message.photo and not f_config.get('photo', True): return True
    if message.video and not f_config.get('video', True): return True
    if message.audio and not f_config.get('audio', True): return True
    if message.voice and not f_config.get('voice', True): return True
    if message.document and not f_config.get('document', True): return True
    if message.sticker and not f_config.get('sticker', True): return True
    if message.animation and not f_config.get('animation', True): return True
    if message.poll and not f_config.get('poll', True): return True
    return False

def get_custom_caption(msg, caption_template):
    if not caption_template or not msg:
        return msg.caption.html if msg and msg.caption else ""

    original_caption = ""
    if msg.caption:
        original_caption = msg.caption.html

    if msg.media:
        media = getattr(msg, msg.media.value, None)
        if media:
            file_name = getattr(media, 'file_name', '')
            file_size = getattr(media, 'file_size', 0)
            return caption_template.format(filename=file_name, size=get_size(file_size), caption=original_caption)
    
    return caption_template.format(filename="", size="", caption=original_caption)

class WorkerManager:
    def __init__(self, operator_clients, job_queue, sts, configs):
        self.clients = deque(operator_clients)
        self.job_queue = job_queue
        self.sts = sts
        self.configs = configs
        self.delay = configs.get('forward_delay', 0.5)
        self.cooldown_workers = {}
        self.is_cancelled = False

    async def start(self):
        logger.info(f"WorkerManager started with {len(self.clients)} workers.")
        while (self.job_queue or self.cooldown_workers) and not self.is_cancelled:
            self.check_cooldowns()

            if not self.clients:
                await asyncio.sleep(1)
                continue
            
            active_client = self.clients.popleft()
            
            if not self.job_queue:
                self.clients.append(active_client)
                await asyncio.sleep(1)
                continue

            message_id_batch = self.job_queue.popleft()

            try:
                await self.process_messages_one_by_one(active_client, message_id_batch)
                self.clients.append(active_client)
            except FloodWait as e:
                cooldown_duration = e.value + 5
                logger.warning(f"Worker {active_client.me.first_name} hit FloodWait. Cooldown for {cooldown_duration}s.")
                self.cooldown_workers[active_client] = asyncio.get_running_loop().time() + cooldown_duration
                self.job_queue.appendleft(message_id_batch)
            except Exception as e:
                logger.error(f"Worker {active_client.me.first_name} failed: {type(e).__name__}. Cooldown for 10s.")
                self.cooldown_workers[active_client] = asyncio.get_running_loop().time() + 10
                self.job_queue.appendleft(message_id_batch)

    async def process_messages_one_by_one(self, client, message_ids):
        messages = await client.get_messages(self.sts.FROM, message_ids)
        
        for i, message in enumerate(messages):
            if self.is_cancelled:
                remaining_ids = [msg.id for msg in messages[i:]]
                if remaining_ids: self.job_queue.appendleft(remaining_ids)
                return

            self.sts.add('fetched', 1)
            if should_skip(message, self.configs): continue
            
            try:
                if self.configs.get('forward_tag', False):
                    # CORRECTED: Pass message.id inside a list
                    await client.forward_messages(chat_id=self.sts.TO, from_chat_id=self.sts.FROM, message_ids=[message.id])
                else:
                    await client.copy_message(
                        chat_id=self.sts.TO,
                        from_chat_id=self.sts.FROM,
                        message_id=message.id,
                        caption=get_custom_caption(message, self.configs.get('caption')),
                        reply_markup=parse_buttons(self.configs.get('button'))
                    )
                
                self.sts.add('total_files', 1)
                if self.delay > 0: await asyncio.sleep(self.delay)

            except FloodWait as e:
                remaining_ids = [msg.id for msg in messages[i:]]
                self.job_queue.appendleft(remaining_ids)
                self.sts.add('fetched', -1)
                raise e
            except Exception as e:
                logger.error(f"Failed to process message {message.id}. Error: {e}")
                self.sts.add('failed', 1)

    def check_cooldowns(self):
        now = asyncio.get_running_loop().time()
        ready_workers = [w for w, end in self.cooldown_workers.items() if now >= end]
        for worker in ready_workers:
            logger.info(f"Worker {worker.me.first_name} cooldown finished.")
            self.clients.append(worker)
            del self.cooldown_workers[worker]

    def cancel(self):
        self.is_cancelled = True

async def resilient_start_clone(config):
    try:
        client = await asyncio.wait_for(start_clone_bot(CLIENT.client(config), config), timeout=OPERATOR_START_TIMEOUT)
        return client, None
    except asyncio.TimeoutError:
        return None, f"Timed out after {OPERATOR_START_TIMEOUT}s"
    except Exception as e:
        return None, str(e)

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
    m = await cb.message.edit("`Initializing...`")
    operator_clients = []
    
    try:
        user_configs = await db.get_configs(user_id)
        operator_configs = await db.get_bots(user_id)
        if not operator_configs: raise ValueError("No Operator Bots/Userbots found in your settings.")

        await m.edit(f"`Step 1/4: Starting {len(operator_configs)} operator(s)...`")
        results = await asyncio.gather(*[resilient_start_clone(c) for c in operator_configs])
        
        operator_clients = [client for client, error in results if client]
        if not operator_clients:
            errors = ". ".join(set(e for c, e in results if e))
            raise ValueError(f"All operators failed to start. Error: {errors or 'Unknown'}")

        await m.edit(f"`Step 2/4: Verifying channel access...`")
        try:
            await operator_clients[0].get_chat(session['from_chat_id'])
            await operator_clients[0].get_chat(session['to_chat_id'])
        except Exception as e: raise ValueError(f"Operator could not access a required chat.\nError: `{e}`")

        sts = STS(frwd_id).store(From=session['from_chat_id'], to=session['to_chat_id'], start_id=session['start_id'], end_id=session['end_id'])
        
        await m.edit("`Step 3/4: Populating job queue...`")
        start_id, end_id = min(sts.start_id, sts.end_id), max(sts.start_id, sts.end_id)
        job_queue = deque(
            list(range(i, min(i + BATCH_SIZE, end_id + 1)))
            for i in range(start_id, end_id + 1, BATCH_SIZE)
        )

        temp.ACTIVE_TASKS[user_id] = {frwd_id: {"process": m, "start_time": sts.get('start')}}
        temp.lock[user_id] = True

        text, buttons = progress_message_content(sts, sts.get('start'), frwd_id)
        await m.edit(text, reply_markup=buttons)

        reporter_task = asyncio.create_task(edit_progress(m, sts))
        manager = WorkerManager(operator_clients, job_queue, sts, user_configs)
        cancel_task = asyncio.create_task(cancel_checker(frwd_id, manager))
        
        await manager.start()

    except Exception as e:
        logger.error(f"Task failed: {e}", exc_info=True)
        await m.edit(f"**TASK FAILED**\n\n**Reason:** `{e}`")
    finally:
        if 'reporter_task' in locals(): reporter_task.cancel()
        if 'cancel_task' in locals(): cancel_task.cancel()
        if 'sts' in locals(): await edit_progress(m, sts, done=True)

        logger.info("Cleaning up resources...")
        await asyncio.gather(*[client.stop() for client in operator_clients if client.is_connected], return_exceptions=True)
        
        temp.FORWARD_SESSIONS.pop(frwd_id, None)
        temp.ACTIVE_TASKS.pop(user_id, None)
        temp.CANCEL.pop(frwd_id, None)
        temp.lock.pop(user_id, None)
        logger.info("Cleanup complete.")

async def cancel_checker(frwd_id, manager):
    while not manager.is_cancelled:
        if temp.CANCEL.get(frwd_id):
            manager.cancel()
            break
        await asyncio.sleep(1)

@Client.on_callback_query(filters.regex(r'^cancel_task_'))
async def cancel_task_cb(bot, cb):
    task_id = cb.data.split("_")[-1]
    temp.CANCEL[task_id] = True
    await cb.answer("Cancelling task... Please wait.", show_alert=True)
    try: await cb.message.edit_reply_markup(None)
    except MessageNotModified: pass

@Client.on_message(filters.private & filters.command(['start']))
async def start(client, message):
    user = message.from_user
    try:
        if not await db.is_user_exist(user.id):
            await db.add_user(user.id, user.first_name)
    except Exception as e:
        logger.error(f"Error in user registration: {e}", exc_info=True)
    await message.reply_photo(
        photo=random.choice(SYD),
        caption=Translation.START_TXT.format(user.mention),
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton('Help', callback_data='help'),
            InlineKeyboardButton('About', callback_data='about')
        ]])
    )

@Client.on_message(filters.private & filters.command(['restart', "r"]) & filters.user(Config.OWNER_ID))
async def restart(client, message):
    msg = await message.reply_text("<i>Restarting...</i>")
    await asyncio.sleep(2)
    await msg.edit("<i>Restarted.</i>")
    os.execl(sys.executable, sys.executable, *sys.argv)

def parse_message_input(message):
    if message.text and not message.forward_date:
        match = re.match(r"(https://)?t\.me/(c/)?(\d+|[a-zA-Z_0-9]+)/(\d+)", message.text.replace("?single", ""))
        if match:
            chat_id = f"-100{match.group(3)}" if match.group(3).isdigit() else match.group(3)
            return chat_id, int(match.group(4)), None
    elif message.forward_from_chat:
        return message.forward_from_chat.id, message.forward_from_message_id, None
    return None, None, "Invalid input. Send a message link or forward a message."

@Client.on_message(filters.private & filters.command(["fwd", "forward"]))
async def forward_command_handler(bot, message):
    user_id = message.from_user.id
    if temp.lock.get(user_id): return await message.reply("A task is in progress. Use /tasks to manage it.")
    if not await db.get_bots(user_id): return await message.reply("No bots found. Add one in `/settings`.")
    if not await db.get_user_channels(user_id): return await message.reply("No target channels found. Add one in `/settings`.")

    temp.USER_STATES[user_id] = {'command_message': message, 'is_settings': False}
    buttons = [[InlineKeyboardButton(c['title'], callback_data=f"fwd_target_{c['chat_id']}")] for c in await db.get_user_channels(user_id)]
    buttons.append([InlineKeyboardButton("« Cancel", callback_data="close_btn")])
    await message.reply("<b>Step 1: Select Target Channel</b>", reply_markup=InlineKeyboardMarkup(buttons))

@Client.on_callback_query(filters.regex(r'^fwd_target_'))
async def cb_select_target(bot, query):
    user_id = query.from_user.id
    state = temp.USER_STATES.get(user_id)
    if not state or state.get("is_settings"): return await query.answer("Session expired.", show_alert=True)
    state['to_chat_id'] = int(query.data.split('_')[-1])
    prompt = await query.message.edit_text(Translation.FROM_MSG)
    state.update({'prompt_message_id': prompt.id, 'state': 'awaiting_source'})

@Client.on_callback_query(filters.regex(r"^(range_|noop)"))
async def range_menu_handler(bot: Client, query: CallbackQuery):
    user_id = query.from_user.id
    if query.data == "noop": return await query.answer()
    try:
        parts = query.data.split('_')
        action, session_id = parts[1], parts[-1]
        session = temp.RANGE_SESSIONS.get(session_id)
        if not session or session.get('user_id') != user_id: return await query.answer("Session expired.", show_alert=True)
        
        message_to_edit = await bot.get_messages(user_id, session['range_message_id'])

        if action == "confirm":
            await message_to_edit.delete()
            await show_final_confirmation(bot, query, session_id)
        elif action == "cancel":
            temp.RANGE_SESSIONS.pop(session_id, None)
            await message_to_edit.delete()
            await bot.send_message(user_id, "Cancelled.")
        elif action == "edit":
            part = "start" if parts[2] == "start" else "end"
            prompt = await message_to_edit.edit_text(f"Send the new **{part}** message ID.")
            temp.USER_STATES[user_id] = {"state": "awaiting_range_edit", "session_id": session_id, "part_to_edit": part, "prompt_message_id": prompt.id}
        elif action == "swap":
            session['start_id'], session['end_id'] = session['end_id'], session['start_id']
            await update_range_message(bot, session_id)
            await query.answer("Swapped.")
    except Exception as e:
        logger.error(f"Range menu error: {e}")
        await query.answer(f"Error: {e}", show_alert=True)

async def show_final_confirmation(bot, query, session_id):
    user_id = query.from_user.id
    session = temp.RANGE_SESSIONS.get(session_id)
    if not session: return await bot.send_message(user_id, "Session expired.")
    
    operators = await db.get_bots(user_id)
    to_title = (await db.get_channel_details(user_id, session['to_chat_id']))['title']
    
    forward_id = generate_short_id()
    temp.FORWARD_SESSIONS[forward_id] = temp.RANGE_SESSIONS.pop(session_id)
    
    await bot.send_message(user_id, f"<b>Final Check</b>\n\n"
        f"● <b>Source:</b> <code>{session['from_title']}</code>\n"
        f"● <b>Target:</b> <code>{to_title}</code>\n"
        f"● <b>Range:</b> `{min(session['start_id'], session['end_id'])}` to `{max(session['start_id'], session['end_id'])}`\n"
        f"● <b>Operators:</b> `{len(operators)}` will be used.\n\n"
        f"<i>Ensure operators are in both channels!</i>",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton('✓ Start Forwarding', callback_data=f"start_public_{forward_id}")],
            [InlineKeyboardButton('« Cancel', callback_data="close_btn")]
        ]))

@Client.on_message(filters.private & filters.incoming & ~filters.command([
    "start", "restart", "r", "fwd", "forward", "settings", "forwardelay", "fd", "tasks"
]))
async def universal_message_handler(bot: Client, message: Message):
    user_id = message.from_user.id
    state = temp.USER_STATES.get(user_id)
    if not state: return

    prompt_id = state.get("prompt_message_id")
    if prompt_id:
        try: await bot.delete_messages(user_id, prompt_id)
        except: pass

    state_type = state.get("state")
    temp.USER_STATES.pop(user_id, None)

    if state_type == 'awaiting_source':
        from_chat, end_id, error = parse_message_input(message)
        if error: return await message.reply(error)
        to_chat_id = state['to_chat_id']
        bots = await db.get_bots(user_id)
        from_title = "Private Chat"
        try:
            async with CLIENT.client(bots[0]) as temp_client:
                chat_info = await temp_client.get_chat(from_chat)
                from_title = chat_info.title
        except Exception as e:
            logger.warning(f"Could not get chat title: {e}")
        await start_range_selection(bot, state['command_message'], from_chat, from_title, to_chat_id, 1, end_id)
    
    elif state_type == 'awaiting_range_edit':
        session_id = state["session_id"]
        session = temp.RANGE_SESSIONS.get(session_id)
        if not session: return await message.reply_text("Session expired.")
        try:
            new_id = int(message.text)
            session[f'{state["part_to_edit"]}_id'] = new_id
            await message.delete()
            await update_range_message(bot, session_id)
        except ValueError: await message.reply_text("Not a valid ID.")

@Client.on_callback_query(filters.regex(r'^close_btn$'))
async def close_callback(bot, query):
    await query.message.delete()
    temp.USER_STATES.pop(query.from_user.id, None)

@Client.on_callback_query(filters.regex(r'^back'))
async def back_to_start(bot, query):
    await query.message.edit_caption(
       caption=Translation.START_TXT.format(query.from_user.first_name),
       reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('Help', callback_data='help'), InlineKeyboardButton('About', callback_data='about')]])
    )
    
@Client.on_callback_query(filters.regex(r'^help'))
async def helpcb(bot, query):
    await query.message.edit_text(
        text=Translation.HELP_TXT,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('Settings', callback_data='settings#main'), InlineKeyboardButton('« Back', callback_data='back')]])
    )

@Client.on_callback_query(filters.regex(r'^about'))
async def about(bot, query):
    await query.message.edit_caption(caption=Translation.ABOUT_TXT.format(bot.me.mention), reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('« Back', callback_data='back')]]))
