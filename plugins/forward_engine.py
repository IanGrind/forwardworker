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
from .utils import update_configs, start_range_selection, update_range_message, STS, edit_progress
from .test import CLIENT, start_clone_bot
from pyrogram import Client, filters, enums
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, CallbackQuery
from pyrogram.errors import FloodWait

SYD = ["https://files.catbox.moe/3lwlbm.png"]
logger = logging.getLogger(__name__)
BATCH_SIZE = 100
OPERATOR_START_TIMEOUT = 30

def generate_short_id(length=8):
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=length))

#+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+
# CORE FORWARDING ENGINE
#+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+~+

class WorkerManager:
    def __init__(self, operator_clients, job_queue, sts, delay):
        self.clients = deque(operator_clients)
        self.job_queue = job_queue
        self.sts = sts
        self.delay_between_batches = delay
        self.cooldown_workers = {}
        self.is_cancelled = False

    async def start(self):
        logger.info(f"WorkerManager started with {len(self.clients)} workers.")
        while self.job_queue and not self.is_cancelled:
            if not self.clients:
                if self.cooldown_workers:
                    first_cooldown_end = min(self.cooldown_workers.values())
                    wait_time = max(0, first_cooldown_end - asyncio.get_running_loop().time())
                    await asyncio.sleep(wait_time)
                    self.check_cooldowns()
                else:
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
            except Exception as e:
                logger.error(f"Worker {active_client.me.first_name} failed with {type(e).__name__}. Cooldown for 10s.")
                cooldown_duration = 10 # Short cooldown for generic errors
                self.cooldown_workers[active_client] = asyncio.get_running_loop().time() + cooldown_duration

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
            logger.error(f"Failed to process batch: {e}")
            self.job_queue.appendleft(message_batch) # Re-queue the failed batch
            raise e # Re-raise the exception to be handled by the start method

    def check_cooldowns(self):
        now = asyncio.get_running_loop().time()
        ready_workers = [w for w, end in self.cooldown_workers.items() if now >= end]
        for worker in ready_workers:
            self.clients.append(worker)
            del self.cooldown_workers[worker]

    def cancel(self):
        self.is_cancelled = True

async def resilient_start_clone(config):
    try:
        client = await asyncio.wait_for(
            start_clone_bot(CLIENT.client(config), config),
            timeout=OPERATOR_START_TIMEOUT
        )
        return client, None
    except asyncio.TimeoutError:
        return None, f"Timed out after {OPERATOR_START_TIMEOUT}s"
    except Exception as e:
        return None, str(e)

@Client.on_callback_query(filters.regex(r'^start_public_'))
async def pub_(bot, cb: CallbackQuery):
    user_id = cb.from_user.id
    if temp.lock.get(user_id):
        return await cb.answer("Another task is in progress.", show_alert=True)

    frwd_id = cb.data.split("_")[2]
    session = temp.FORWARD_SESSIONS.get(frwd_id)
    if not session:
        return await cb.message.edit("This task has expired.")

    await cb.answer()
    m = await cb.message.edit("`Initializing...`")

    operator_clients = []

    try:
        operator_configs = await db.get_bots(user_id)
        if not operator_configs:
            raise ValueError("No Operator Bots/Userbots found.")

        await m.edit(f"`Step 1/4: Starting {len(operator_configs)} operator(s)...`")

        start_tasks = [resilient_start_clone(config) for config in operator_configs]
        results = await asyncio.gather(*start_tasks)

        successful_clients = []
        report = ["<b>Operator Startup Report:</b>"]
        for i, (client, error) in enumerate(results):
            name = operator_configs[i].get('name', f"#{i+1}")
            if client:
                successful_clients.append(client)
                report.append(f"✅ <code>{name}</code> - <b>Success!</b>")
            else:
                report.append(f"❌ <code>{name}</code> - <b>Failed:</b> <code>{error}</code>")

        await m.edit("\n".join(report))
        await asyncio.sleep(4)

        operator_clients = successful_clients
        if not operator_clients:
            raise ValueError("All operators failed to start.")

        await m.edit(f"`Step 2/4: Verifying channel access with {operator_clients[0].me.first_name}...`")
        try:
            await operator_clients[0].get_chat(session['from_chat_id'])
            await operator_clients[0].get_chat(session['to_chat_id'])
        except Exception as e:
            raise ValueError(f"Operator {operator_clients[0].me.first_name} could not access a required chat.\n\nError: {e}")

        sts = STS(frwd_id).store(From=session['from_chat_id'], to=session['to_chat_id'], start_id=session['start_id'], end_id=session['end_id'])

        await m.edit("`Step 3/4: Populating batch queue...`")
        start_id, end_id = min(sts.start_id, sts.end_id), max(sts.start_id, sts.end_id)

        job_queue = deque(
            list(range(i, min(i + BATCH_SIZE, end_id + 1)))
            for i in range(start_id, end_id + 1, BATCH_SIZE)
        )

        temp.ACTIVE_TASKS[user_id] = {frwd_id: {"process": m}}
        temp.lock[user_id] = True

        await m.edit(f"`Step 4/4: Deploying workers...`")

        reporter_task = asyncio.create_task(edit_progress(m, sts, sts.get('start')))

        user_settings = await db.get_configs(user_id)
        delay = user_settings.get('forward_delay', 0)
        manager = WorkerManager(operator_clients, job_queue, sts, delay)

        cancel_task = asyncio.create_task(cancel_checker(frwd_id, manager))
        await manager.start()

        if not reporter_task.done(): reporter_task.cancel()
        if not cancel_task.done(): cancel_task.cancel()
        await asyncio.sleep(0.1)
        await edit_progress(m, sts, sts.get('start'), done=True)

    except Exception as e:
        logger.error(f"Task failed: {e}", exc_info=True)
        await m.edit(f"**TASK FAILED**\n\n**Reason:** `{e}`")
    finally:
        logger.info("Cleaning up resources...")
        stop_tasks = [client.stop() for client in operator_clients if client.is_connected]
        await asyncio.gather(*stop_tasks, return_exceptions=True)

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
    if temp.lock.get(user_id): return await message.reply("A task is in progress.")
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

        if action == "confirm":
            await query.message.delete()
            await show_final_confirmation(bot, query, session_id)
        elif action == "all":
            await query.answer("Finding last message...", show_alert=False)
            bots = await db.get_bots(user_id)
            if not bots: return await query.answer("No bots to perform this action.", show_alert=True)
            try:
                async with CLIENT.client(bots[0]) as temp_client:
                    async for last_message in temp_client.get_chat_history(session['from_chat_id'], limit=1):
                        session.update({'start_id': 1, 'end_id': last_message.id})
                        await update_range_message(bot, session_id, message_to_edit=query.message)
                        return await query.answer(f"Range set: 1 -> {last_message.id}")
                await query.answer("No messages found.", show_alert=True)
            except Exception as e:
                await query.answer(f"Error: {e}", show_alert=True)
        elif action == "cancel":
            temp.RANGE_SESSIONS.pop(session_id, None)
            await query.message.delete()
            await bot.send_message(user_id, "Cancelled.")
        elif action == "edit":
            part = "start" if parts[2] == "start" else "end"
            prompt = await query.message.edit_text(f"Send the new **{part}** message ID.")
            temp.USER_STATES[user_id] = {"state": "awaiting_range_edit", "session_id": session_id, "part_to_edit": part, "prompt_message_id": prompt.id}
        elif action == "swap":
            session['start_id'], session['end_id'] = session['end_id'], session['start_id']
            await update_range_message(bot, session_id, message_to_edit=query.message)
            await query.answer("Swapped.")
    except Exception as e:
        await query.answer(f"Error: {e}", show_alert=True)

async def show_final_confirmation(bot, query, session_id):
    user_id = query.from_user.id
    session = temp.RANGE_SESSIONS.get(session_id)
    if not session: return await bot.send_message(user_id, "Session expired.")

    operators = await db.get_bots(user_id)
    to_title = (await db.get_channel_details(user_id, session['to_chat_id']))['title']

    forward_id = generate_short_id()
    temp.FORWARD_SESSIONS[forward_id] = temp.RANGE_SESSIONS.pop(session_id)
    STS(forward_id).store(From=session['from_chat_id'], to=session['to_chat_id'], start_id=session['start_id'], end_id=session['end_id'])

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

@Client.on_message(filters.private & filters.command(['settings']))
async def settings_entry(client, message):
    if temp.lock.get(message.from_user.id): return await message.reply("A task is in progress.")
    await message.reply_photo(
        photo=random.choice(SYD), caption="<b>֎ Settings ֎</b>",
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
        menu, *args = parts[1].split('_', 1)
        value = args[0] if args else None

        if menu == "main": await query.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('Bots & Userbots', callback_data='settings#bots'), InlineKeyboardButton('Channels', callback_data='settings#channels')]]))
        elif menu == "bots": await list_bots(bot, user_id, message=query.message)
        elif menu == "channels": await list_channels(bot, user_id, message=query.message)
        elif menu == "addbot": await prompt_for_input(bot, query, user_id, "awaiting_bot_token", "Send the bot token.")
        elif menu == "adduserbot": await prompt_for_input(bot, query, user_id, "awaiting_user_session", "Send the session string.")
        elif menu == "addbots": await prompt_for_input(bot, query, user_id, "awaiting_bots_bulk", "Send a list of bot tokens, separated by spaces.")
        elif menu == "addusers": await prompt_for_input(bot, query, user_id, "awaiting_users_bulk", "Send a list of session strings, separated by spaces.")
        elif menu == "addchannel": await prompt_for_input(bot, query, user_id, "awaiting_channel_forward", "Forward a message from the target chat.")
        elif menu == "editbot": await show_bot_details(query.message, user_id, int(value))
        elif menu == "removebot": await remove_and_go_back(bot, query, user_id, db.remove_bot, int(value), "Bot removed.", 'bots')
        elif menu == "editchannels": await show_channel_details(query.message, user_id, int(value))
        elif menu == "removechannel": await remove_and_go_back(bot, query, user_id, db.remove_channel, int(value), "Channel removed.", 'channels')
    except Exception as e:
        logger.error(f"Error in settings: {e}", exc_info=True)

@Client.on_message(filters.private & filters.command(["forwardelay", "fd"]))
async def forward_delay(client: Client, message: Message):
    user_id = message.from_user.id
    if (await db.get_ban_status(user_id))["is_banned"]: return await message.reply_text("Access denied.")
    user_configs = await db.get_configs(user_id)
    delay = user_configs.get('forward_delay', 0)
    if len(message.command) < 2:
        return await message.reply_text(f"<b>Batch Delay:</b> `{delay}s`\n\nTo change, use `/forwardelay [seconds]`.")
    try:
        new_delay = float(message.command[1])
        if new_delay < 0: return await message.reply_text("Delay must be a positive number.")
        await update_configs(user_id, 'forward_delay', new_delay)
        await message.reply_text(f"Batch delay updated to **{new_delay}s**.")
    except ValueError: await message.reply_text("Invalid number.")
    except Exception as e: await message.reply_text(f"Error: {e}")

# CORRECTED: The list of commands to ignore is now properly passed to the filter.
@Client.on_message(filters.private & filters.incoming & ~filters.command([
    "start", "restart", "r", "fwd", "forward", "settings", "forwardelay", "fd"
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

    if state_type in ["awaiting_bot_token", "awaiting_user_session", "awaiting_bots_bulk", "awaiting_users_bulk", "awaiting_channel_forward"]:
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
                await message.reply("Not a valid forwarded message.")
    elif state_type == 'awaiting_source':
        from_chat, end_id, error = parse_message_input(message)
        if error: return await message.reply(error)
        to_chat_id = state['to_chat_id']
        bots = await db.get_bots(user_id)
        from_title = "Private Chat"
        try:
            async with CLIENT.client(bots[0]) as temp_client:
                from_title = (await temp_client.get_chat(from_chat)).title
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

async def prompt_for_input(bot, query, user_id, state, text):
    prompt = await query.message.edit_text(f"{text}\n\n/cancel - to abort.")
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
        [InlineKeyboardButton('+ Add', callback_data="settings#addbot"), InlineKeyboardButton('+ Add User', callback_data="settings#adduserbot")],
        [InlineKeyboardButton('+ Add Many', callback_data="settings#addbots_bulk"), InlineKeyboardButton('+ Add Many Users', callback_data="settings#addusers_bulk")],
        [InlineKeyboardButton('« Back', callback_data="settings#main")]
    ])
    text = f"<b>֎ Bots & Userbots ({len(bots)}) ֎</b>"
    if as_new: await bot.send_photo(chat_id=user_id, photo=random.choice(SYD), caption=text, reply_markup=InlineKeyboardMarkup(buttons))
    else: await message.edit_caption(caption=text, reply_markup=InlineKeyboardMarkup(buttons))

async def list_channels(bot, user_id, message=None, as_new=False):
    channels = await db.get_user_channels(user_id)
    buttons = [[InlineKeyboardButton(f"● {c['title']}", callback_data=f"settings#editchannels_{c['chat_id']}")] for c in channels]
    buttons += [[InlineKeyboardButton('+ Add Channel', callback_data="settings#addchannel")], [InlineKeyboardButton('« Back', callback_data="settings#main")]]
    text = f"<b>֎ Target Channels ({len(channels)}) ֎</b>"
    if as_new: await bot.send_photo(chat_id=user_id, photo=random.choice(SYD), caption=text, reply_markup=InlineKeyboardMarkup(buttons))
    else: await message.edit_caption(caption=text, reply_markup=InlineKeyboardMarkup(buttons))

async def show_bot_details(message, user_id, bot_id):
    _bot = await db.get_bot(user_id, bot_id)
    uname = f"@{_bot['username']}" if _bot.get('username') else "N/A"
    buttons = [[InlineKeyboardButton('- Remove', callback_data=f"settings#removebot_{bot_id}")], [InlineKeyboardButton('« Back', callback_data="settings#bots")]]
    TEXT = Translation.BOT_DETAILS if _bot['is_bot'] else Translation.USER_DETAILS
    await message.edit_caption(caption=TEXT.format(_bot['name'], bot_id, uname), reply_markup=InlineKeyboardMarkup(buttons))

async def show_channel_details(message, user_id, chat_id):
    chat = await db.get_channel_details(user_id, int(chat_id))
    buttons = [[InlineKeyboardButton('- Remove', callback_data=f"settings#removechannel_{chat_id}")], [InlineKeyboardButton('« Back', callback_data="settings#channels")]]
    await message.edit_caption(caption=f"<b>Channel:</b> <code>{chat['title']}</code>\n<b>ID:</b> <code>{chat['chat_id']}</code>", reply_markup=InlineKeyboardMarkup(buttons))

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
