# iangrind/forwardworker/forwardworker-1ff680b8c32922eb74e103a193e108a8d299c7bc/plugins/forward_engine.py

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
from .utils import update_configs, start_range_selection, update_range_message, STS
from .test import CLIENT, start_clone_bot
from pyrogram import Client, filters, enums
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, CallbackQuery
from pyrogram.errors import FloodWait

SYD = ["https://files.catbox.moe/3lwlbm.png"]
logger = logging.getLogger(__name__)
BATCH_SIZE = 100 # Telegram API's limit for forward_messages

def generate_short_id(length=8):
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=length))

#+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+
# CORE FORWARDING ENGINE (BATCH PROCESSING & WORKER MANAGEMENT)
#+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+

class WorkerManager:
    """ Manages a pool of operators for high-speed, ordered batch forwarding. """
    def __init__(self, operator_clients, job_queue, sts, delay):
        self.clients = deque(operator_clients)
        self.job_queue = job_queue
        self.sts = sts
        self.delay_between_batches = delay
        self.cooldown_workers = {}
        self.is_cancelled = False

    async def start(self):
        logger.info(f"WorkerManager started with {len(self.clients)} workers for batch forwarding.")
        # CORRECTED: Use truthiness to check if deque is empty. ".empty()" was the bug.
        while self.job_queue and not self.is_cancelled:
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
                if self.delay_between_batches > 0 and self.job_queue:
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
    m = await cb.message.edit("`Initializing...`")
    
    operator_clients = []
    sts = None
    
    try:
        operator_configs = await db.get_bots(user_id)
        if not operator_configs:
            raise ValueError("No Operator Bots/Userbots found in your settings.")
        
        user_settings = await db.get_configs(user_id)
        delay = user_settings.get('forward_delay', 0)
        
        await m.edit(f"`Step 1/4: Starting {len(operator_configs)} operator(s)...`")
        
        successful_clients = []
        for config in operator_configs:
            try:
                client = await start_clone_bot(CLIENT.client(config), config)
                successful_clients.append(client)
                logger.info(f"Successfully started operator: {client.me.first_name}")
            except Exception as e:
                logger.error(f"Failed to start operator with name {config.get('name', 'N/A')}: {e}")
        
        operator_clients = successful_clients
        if not operator_clients:
            raise ValueError("All operators failed to start. Check your tokens/sessions.")

        await m.edit(f"`Step 2/4: Verifying channel access with {operator_clients[0].me.first_name}...`")
        try:
            await operator_clients[0].get_chat(session['from_chat_id'])
            await operator_clients[0].get_chat(session['to_chat_id'])
        except Exception as e:
            raise ValueError(f"Operator {operator_clients[0].me.first_name} could not access a required chat.\n\nError: {e}")

        sts = STS(frwd_id).store(From=session['from_chat_id'], to=session['to_chat_id'], start_id=session['start_id'], end_id=session['end_id'])
        
        await m.edit("`Step 3/4: Populating ordered batch queue...`")
        start_id = min(sts.start_id, sts.end_id)
        end_id = max(sts.start_id, sts.end_id)
        
        job_queue = deque()
        for i in range(start_id, end_id + 1, BATCH_SIZE):
            job_queue.append(list(range(i, min(i + BATCH_SIZE, end_id + 1))))
        
        temp.ACTIVE_TASKS[user_id] = {frwd_id: {"process": m}}
        temp.lock[user_id] = True

        await m.edit(f"`Step 4/4: Deploying workers...`\n\n`{sts.total}` msgs in `{len(job_queue)}` batches.\n`{len(operator_clients)}` workers active.\nForwarding from **{session['from_title']}**...")
        
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

        # CORRECTED: This part now only runs if the loop completes without errors.
        if not cancel_task.done():
            cancel_task.cancel()
        if not reporter_task.done():
            reporter_task.cancel()
        await asyncio.sleep(0.1) 
        await edit_progress(m, sts, sts.get('start'), done=True)


    except Exception as e:
        # CORRECTED: The error message will now persist.
        logger.error(f"A critical error occurred, halting task: {e}", exc_info=True)
        await m.edit(f"**TASK FAILED**\n\n**Reason:** `{e}`")
    finally:
        # CORRECTED: The finally block is now ONLY for cleanup.
        logger.info("Cleaning up forwarding task resources...")
        stop_tasks = [client.stop() for client in operator_clients if client.is_connected]
        await asyncio.gather(*stop_tasks, return_exceptions=True)
        
        temp.FORWARD_SESSIONS.pop(frwd_id, None)
        temp.ACTIVE_TASKS.pop(user_id, None)
        temp.CANCEL.pop(frwd_id, None)
        temp.lock.pop(user_id, None)
        logger.info("Forwarding task fully cleaned up.")

#+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+
# USER COMMANDS & INTERFACE HANDLERS
#+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+

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
    if not message or (not message.text and not message.forward_date): return None, None, "Invalid input: Send a message link or forward a message."
    if message.text and not message.forward_date:
        match = re.match(r"(https://)?t\.me/(c/)?(\d+|[a-zA-Z_0-9]+)/(\d+)", message.text.replace("?single", ""))
        if not match: return None, None, 'Invalid Link Format.'
        chat_id = f"-100{match.group(3)}" if match.group(3).isdigit() else match.group(3)
        return chat_id, int(match.group(4)), None
    elif message.forward_from_chat: return message.forward_from_chat.id, message.forward_from_message_id, None
    return None, None, "Could not identify the source."

@Client.on_message(filters.private & filters.command(["fwd", "forward"]))
async def forward_command_handler(bot, message):
    user_id = message.from_user.id
    if temp.lock.get(user_id): return await message.reply("A task is in progress.")
    
    bots = await db.get_bots(user_id)
    if not bots: return await message.reply("You haven't added any bots or userbots. Please add at least one in `/settings`.")

    channels = await db.get_user_channels(user_id)
    if not channels: return await message.reply("You haven't added any target channels. Please add one in `/settings`.")

    temp.USER_STATES[user_id] = {'command_message': message, 'is_settings': False}
    
    buttons = [[InlineKeyboardButton(c['title'], callback_data=f"fwd_target_{c['chat_id']}")] for c in channels]
    buttons.append([InlineKeyboardButton("« Cancel", callback_data="close_btn")])
    await message.reply("<b>Step 1: Select the Target Channel</b>", reply_markup=InlineKeyboardMarkup(buttons))

@Client.on_callback_query(filters.regex(r'^fwd_target_'))
async def cb_select_target(bot, query):
    user_id = query.from_user.id
    state = temp.USER_STATES.get(user_id)
    if not state or state.get("is_settings"): return await query.answer("This is not for you, or your session has expired.", show_alert=True)
    
    state['to_chat_id'] = int(query.data.split('_')[-1])
    
    prompt = await query.message.edit_text(Translation.FROM_MSG)
    state['prompt_message_id'] = prompt.id
    state['state'] = 'awaiting_source'

@Client.on_callback_query(filters.regex(r"^(range_|noop)"))
async def range_menu_handler(bot: Client, query: CallbackQuery):
    user_id = query.from_user.id
    if query.data == "noop": return await query.answer()

    try:
        parts = query.data.split('_')
        action = parts[1]
        session_id = parts[-1]

        session = temp.RANGE_SESSIONS.get(session_id)
        if not session or session.get('user_id') != user_id:
            return await query.answer("This menu is not for you, or the session has expired.", show_alert=True)

        if action == "confirm":
            await query.message.delete()
            await show_final_confirmation(bot, query, session_id)

        elif action == "all":
            await query.answer("Checking for the last message...", show_alert=False)
            bots = await db.get_bots(user_id)
            if not bots:
                return await query.answer("No operator bots found to perform this action.", show_alert=True)
            
            try:
                async with CLIENT.client(bots[0]) as temp_client:
                    async for last_message in temp_client.get_chat_history(session['from_chat_id'], limit=1):
                        session['start_id'] = 1
                        session['end_id'] = last_message.id
                        await update_range_message(bot, session_id, message_to_edit=query.message)
                        return await query.answer(f"Range set to: 1 -> {last_message.id}", show_alert=False)
                await query.answer("Could not find any messages in the source chat.", show_alert=True)
            except Exception as e:
                logger.error(f"Could not get last message for 'Forward All': {e}")
                await query.answer(f"Error: Could not access the source chat. Is the operator bot a member?\n\n({e})", show_alert=True)

        elif action == "cancel":
            temp.RANGE_SESSIONS.pop(session_id, None)
            await query.message.delete()
            await bot.send_message(user_id, "Operation cancelled.")

        elif action == "edit":
            part_to_edit = "start" if parts[2] == "start" else "end"
            prompt_text = f"OK, send the new **{part_to_edit}** message ID."
            prompt_msg = await query.message.edit_text(prompt_text)
            temp.USER_STATES[user_id] = { "state": "awaiting_range_edit", "session_id": session_id, "part_to_edit": part_to_edit, "prompt_message_id": prompt_msg.id, "is_settings": False }
        
        elif action == "swap":
            start, end = session['start_id'], session['end_id']
            session['start_id'] = end
            session['end_id'] = start
            await update_range_message(bot, session_id, message_to_edit=query.message)
            await query.answer("Order swapped")
            
    except Exception as e:
        logger.error(f"Error in range_menu_handler: {e}", exc_info=True)
        await query.answer("An error occurred.", show_alert=True)

async def show_final_confirmation(bot, query, session_id):
    user_id = query.from_user.id
    session = temp.RANGE_SESSIONS.get(session_id)
    if not session: return await bot.send_message(user_id, "⚠️ Your session has expired.")
    
    operators = await db.get_bots(user_id)
    to_title = (await db.get_channel_details(user_id, session['to_chat_id']))['title']
    
    forward_id = generate_short_id()
    temp.FORWARD_SESSIONS[forward_id] = temp.RANGE_SESSIONS.pop(session_id)
    
    STS(forward_id).store(From=session['from_chat_id'], to=session['to_chat_id'], start_id=session['start_id'], end_id=session['end_id'])
    
    await bot.send_message(user_id, f"<b>Final Check</b>\n\n"
        f"● <b>Source:</b> `{session['from_title']}`\n"
        f"● <b>Target:</b> `{to_title}`\n"
        f"● <b>Range:</b> `{min(session['start_id'], session['end_id'])}` to `{max(session['start_id'], session['end_id'])}`\n"
        f"● <b>Operators:</b> `{len(operators)}` bots/userbots will be used.\n\n"
        f"<i>Ensure all Operators are members of the source channel and admins in the target channel.</i>",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton('✓ Yes, Start Forwarding', callback_data=f"start_public_{forward_id}")],
            [InlineKeyboardButton('« No, Cancel', callback_data="close_btn")]
        ]))

@Client.on_message(filters.private & filters.command(['settings']))
async def settings_entry(client, message):
    if temp.lock.get(message.from_user.id):
        return await message.reply("A task is in progress.")
    await message.reply_photo(
        photo=random.choice(SYD),
        caption="<b>֎ Settings ֎</b>",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton('Bots & Userbots', callback_data='settings#bots'), InlineKeyboardButton('Channels', callback_data='settings#channels')]
        ])
    )

@Client.on_callback_query(filters.regex(r'^settings'))
async def settings_query_handler(bot, query):
    await query.answer()
    user_id = query.from_user.id
    if temp.lock.get(user_id): return await query.answer("A task is in progress.", show_alert=True)

    try:
        parts = query.data.split("#")
        menu_type = parts[1]
        action_parts = menu_type.split('_', 1)
        action = action_parts[0]
        value = action_parts[1] if len(action_parts) > 1 else None

        if menu_type == "main":
            await query.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton('Bots & Userbots', callback_data='settings#bots'), InlineKeyboardButton('Channels', callback_data='settings#channels')]
            ]))
        elif menu_type == "bots": await list_bots(bot, user_id, message=query.message)
        elif menu_type == "channels": await list_channels(bot, user_id, message=query.message)
        elif menu_type == "addbot": await prompt_for_input(bot, query, user_id, "awaiting_bot_token", "Send the bot token from @BotFather.")
        elif menu_type == "adduserbot": await prompt_for_input(bot, query, user_id, "awaiting_user_session", "Send the Pyrogram (v2) session string.")
        elif menu_type == "addbots_bulk": await prompt_for_input(bot, query, user_id, "awaiting_bots_bulk", "Send a list of bot tokens, separated by spaces.")
        elif menu_type == "addusers_bulk": await prompt_for_input(bot, query, user_id, "awaiting_users_bulk", "Send a list of session strings, separated by spaces.")
        elif menu_type == "addchannel": await prompt_for_input(bot, query, user_id, "awaiting_channel_forward", "Forward a message from the target chat.")
        elif action == "editbot": await show_bot_details(query.message, user_id, int(value))
        elif action == "removebot": await remove_and_go_back(bot, query, user_id, db.remove_bot, int(value), "Bot/Userbot removed.", 'bots')
        elif action == "editchannels": await show_channel_details(query.message, user_id, int(value))
        elif action == "removechannel": await remove_and_go_back(bot, query, user_id, db.remove_channel, int(value), "Channel removed.", 'channels')

    except Exception as e:
        logger.error(f"Error in settings_query: {e}", exc_info=True)

@Client.on_message(filters.private & filters.command(["forwardelay", "fd"]))
async def forward_delay(client: Client, message: Message):
    user_id = message.from_user.id
    
    ban_status = await db.get_ban_status(user_id)
    if ban_status["is_banned"]: return await message.reply_text(f"Access denied.")

    user_configs = await db.get_configs(user_id)
    current_delay = user_configs.get('forward_delay', 0)

    if len(message.command) < 2: 
        await message.reply_text(
            f"<b>֎ Batch Delay ֎</b>\n\n"
            f"Set a custom delay between batches of 100 messages.\n\n"
            f"<b>Current Delay:</b> <code>{current_delay} seconds</code>\n\n"
            f"<b>Usage:</b> `/forwardelay [seconds]` (e.g., `/forwardelay 0.5`)\n\n"
            f"Set to `0` for maximum speed."
        )
        return
    
    try:
        delay = float(message.command[1])
        if delay < 0: return await message.reply_text("The delay must be a positive number.")
        await update_configs(user_id, 'forward_delay', delay)
        await message.reply_text(f"✅ Delay between batches has been updated to **{delay} seconds**.")
    except ValueError: await message.reply_text("Invalid input. Please provide a number.")
    except Exception as e: await message.reply_text(f"An error occurred: {e}")

@Client.on_message(filters.private & filters.incoming & ~filters.command([
    "start", "restart", "r", "fwd", "forward", "settings", "forwardelay", "fd"
]))
async def universal_message_handler(bot: Client, message: Message):
    user_id = message.from_user.id
    state = temp.USER_STATES.get(user_id)
    if not state: return

    if state.get("is_settings"):
        if state.get("prompt_message_id"):
            try: await bot.delete_messages(user_id, state["prompt_message_id"])
            except: pass
        
        state_type = state.get("state")
        temp.USER_STATES.pop(user_id, None)

        if state_type == "awaiting_bot_token":
            if await CLIENT.add_bot(message): await list_bots(bot, user_id, as_new=True, message=message)
        elif state_type == "awaiting_user_session":
            if await CLIENT.add_session(message): await list_bots(bot, user_id, as_new=True, message=message)
        elif state_type == "awaiting_bots_bulk":
            if await CLIENT.add_bots_bulk(message): await list_bots(bot, user_id, as_new=True, message=message)
        elif state_type == "awaiting_users_bulk":
            if await CLIENT.add_sessions_bulk(message): await list_bots(bot, user_id, as_new=True, message=message)
        elif state_type == "awaiting_channel_forward":
            if message.forward_from_chat:
                await db.add_channel(user_id, message.forward_from_chat.id, message.forward_from_chat.title, message.forward_from_chat.username)
                await message.reply("✅ Channel added.")
                await list_channels(bot, user_id, as_new=True, message=message)
            else:
                await message.reply("That was not a valid forwarded message.")
                await list_channels(bot, user_id, as_new=True, message=message)
    
    elif state.get('state') == 'awaiting_source':
        if state.get("prompt_message_id"):
            try: await bot.delete_messages(user_id, state["prompt_message_id"])
            except: pass
        
        from_chat, end_id, error = parse_message_input(message)
        if error:
            temp.USER_STATES.pop(user_id, None)
            return await message.reply(error)

        to_chat_id = state.get('to_chat_id')
        command_message = state.get('command_message')
        temp.USER_STATES.pop(user_id, None)

        bots = await db.get_bots(user_id)
        from_title = "Private Chat"
        try:
            async with CLIENT.client(bots[0]) as temp_client:
                from_title = (await temp_client.get_chat(from_chat)).title
        except Exception as e:
            logger.warning(f"Could not get chat title with first bot: {e}")
        
        await start_range_selection(bot, command_message, from_chat, from_title, to_chat_id, 1, end_id)
        
    elif state.get('state') == 'awaiting_range_edit':
        session_id = state.get("session_id")
        session = temp.RANGE_SESSIONS.get(session_id)
        if not session: 
            temp.USER_STATES.pop(user_id, None)
            return await message.reply_text("Your session has expired.")
        
        try:
            new_id = int(message.text)
            part_to_edit = state.get("part_to_edit")
            session[f'{part_to_edit}_id'] = new_id
            
            await message.delete()
            if state.get("prompt_message_id"):
                await bot.delete_messages(user_id, state["prompt_message_id"])

            temp.USER_STATES.pop(user_id, None)
            await update_range_message(bot, session_id)
        except ValueError:
            await message.reply_text("That's not a valid message ID.")
        except Exception as e:
            logger.error(f"Error processing range edit: {e}", exc_info=True)

async def prompt_for_input(bot, query, user_id, state, text):
    await query.message.delete()
    prompt = await bot.send_message(user_id, f"{text}\n\n/cancel - to abort.")
    temp.USER_STATES[user_id] = {"state": state, "prompt_message_id": prompt.id, "is_settings": True}

async def remove_and_go_back(bot, query, user_id, remove_func, item_id, success_text, back_menu):
    await remove_func(user_id, item_id)
    await query.message.edit_text(success_text)
    await asyncio.sleep(2)
    if back_menu == 'bots': await list_bots(bot, user_id, message=query.message)
    elif back_menu == 'channels': await list_channels(bot, user_id, message=query.message)

async def list_bots(bot, user_id, message=None, as_new=False):
    bots = await db.get_bots(user_id)
    buttons = [[InlineKeyboardButton(b['name'], callback_data=f"settings#editbot_{b['id']}")] for b in bots]
    buttons.extend([
        [InlineKeyboardButton('+ Add Bot', callback_data="settings#addbot"), InlineKeyboardButton('+ Add Userbot', callback_data="settings#adduserbot")],
        [InlineKeyboardButton('+ Add Multiple Bots', callback_data="settings#addbots_bulk"), InlineKeyboardButton('+ Add Multiple Userbots', callback_data="settings#addusers_bulk")],
        [InlineKeyboardButton('« Back', callback_data="settings#main")]
    ])
    text = f"<b>֎ Bots & Userbots ({len(bots)}) ֎</b>"
    
    if as_new and message: await bot.send_photo(chat_id=user_id, photo=random.choice(SYD), caption=text, reply_markup=InlineKeyboardMarkup(buttons))
    elif message: await message.edit_caption(caption=text, reply_markup=InlineKeyboardMarkup(buttons))

async def list_channels(bot, user_id, message=None, as_new=False):
    channels = await db.get_user_channels(user_id)
    buttons = [[InlineKeyboardButton(f"● {c['title']}", callback_data=f"settings#editchannels_{c['chat_id']}")] for c in channels]
    buttons += [[InlineKeyboardButton('+ Add Channel', callback_data="settings#addchannel")], [InlineKeyboardButton('« Back', callback_data="settings#main")]]
    text = f"<b>֎ Target Channels ({len(channels)}) ֎</b>"

    if as_new and message: await bot.send_photo(chat_id=user_id, photo=random.choice(SYD), caption=text, reply_markup=InlineKeyboardMarkup(buttons))
    elif message: await message.edit_caption(caption=text, reply_markup=InlineKeyboardMarkup(buttons))

async def show_bot_details(message, user_id, bot_id):
    _bot = await db.get_bot(user_id, bot_id)
    uname = f"@{_bot['username']}" if _bot.get('username') else "Not Set"
    buttons = [[InlineKeyboardButton('- Remove', callback_data=f"settings#removebot_{bot_id}")], [InlineKeyboardButton('« Back', callback_data="settings#bots")]]
    TEXT = Translation.BOT_DETAILS if _bot['is_bot'] else Translation.USER_DETAILS
    await message.edit_caption(caption=TEXT.format(_bot['name'], bot_id, uname), reply_markup=InlineKeyboardMarkup(buttons))

async def show_channel_details(message, user_id, chat_id):
    chat = await db.get_channel_details(user_id, int(chat_id))
    buttons = [[InlineKeyboardButton('- Remove', callback_data=f"settings#removechannel_{chat_id}")], [InlineKeyboardButton('« Back', callback_data="settings#channels")]]
    await message.edit_caption(caption=f"<b>֎ Channel Details ֎</b>\n\n<b>Title:</b> <code>{chat['title']}</code>\n<b>ID:</b> <code>{chat['chat_id']}</code>", reply_markup=InlineKeyboardMarkup(buttons))

@Client.on_callback_query(filters.regex(r'^close_btn$'))
async def close_callback(bot, query):
    temp.USER_STATES.pop(query.from_user.id, None)
    await query.message.delete()

@Client.on_callback_query(filters.regex(r'^back'))
async def back_to_start(bot, query):
    await query.message.edit_caption(
       caption=Translation.START_TXT.format(query.from_user.first_name),
       reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton('Help', callback_data='help'),
            InlineKeyboardButton('About', callback_data='about')
       ]])
    )
    
@Client.on_callback_query(filters.regex(r'^help'))
async def helpcb(bot, query):
    await query.message.edit_text(
        text=Translation.HELP_TXT,
        reply_markup=InlineKeyboardMarkup(
            [[
            InlineKeyboardButton('How to Use', callback_data='how_to_use')
            ],[
            InlineKeyboardButton('Settings', callback_data='settings#main'),
            InlineKeyboardButton('Stats', callback_data='status')
            ],[
            InlineKeyboardButton('Active Tasks', callback_data='active_tasks_cmd'),
            InlineKeyboardButton('« Back', callback_data='back')
            ]]
        ))

@Client.on_callback_query(filters.regex(r'^about'))
async def about(bot, query):
    await query.message.edit_caption(
        caption=Translation.ABOUT_TXT.format(bot.me.mention),
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('« Back', callback_data='back')]]),
    )

@Client.on_callback_query(filters.regex(r'^how_to_use'))
async def how_to_use(bot, query):
    await query.message.edit_caption(
        caption=Translation.HOW_USE_TXT,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('« Back', callback_data='help')]]),
    )

@Client.on_callback_query(filters.regex(r'^status'))
async def status(bot, query):
    await query.message.edit_caption(
        caption="Bot is online and operational.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('« Back', callback_data='help')]]),
    )
