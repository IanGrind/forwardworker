import os
import re 
import sys
import asyncio 
import logging 
from uuid import uuid4
from database import db 
from config import Config
from pyrogram import Client
from pyrogram.types import Message
from pyrogram.errors import FloodWait, AuthKeyUnregistered, ApiIdInvalid, SessionPasswordNeeded

logger = logging.getLogger(__name__)

SESSION_STRING_SIZE = 351

async def start_clone_bot(client_instance, bot_data):
    """Starts the client and performs a type-specific wake-up routine."""
    await client_instance.start()
    
    if not bot_data.get('is_bot', True):
        async for _ in client_instance.get_dialogs(limit=1):
            pass
    else:
        await client_instance.get_me()
        
    return client_instance

class CLIENT: 
  def __init__(self):
     self.api_id = Config.API_ID
     self.api_hash = Config.API_HASH
    
  def client(self, data):
     name = str(uuid4())
     if not data.get('is_bot', True):
        return Client(name=name, api_id=self.api_id, api_hash=self.api_hash, session_string=data['session'], in_memory=True)
     return Client(name=name, api_id=self.api_id, api_hash=self.api_hash, bot_token=data['token'], in_memory=True)
  
  async def add_bot(self, msg: Message):
    user_id = msg.from_user.id
    token_match = re.search(r'(\d{8,10}:[a-zA-Z0-9_-]{35})', msg.text)
    if not token_match:
        await msg.reply_text("That doesn't look like a valid bot token.")
        return False
    token = token_match.group(1)
    
    try:
        async with self.client({'token': token, 'is_bot': True}) as _client:
            _bot = await _client.get_me()
        
        if await db.is_bot_exist(user_id, _bot.id):
            await msg.reply_text("This bot has already been added.")
            return False

        await db.add_bot({'id': _bot.id, 'is_bot': True, 'user_id': user_id, 'name': _bot.first_name, 'token': token, 'username': _bot.username})
        await msg.reply_text(f"✅ Bot '{_bot.first_name}' added successfully.")
        return True
    except Exception as e:
        await msg.reply_text(f"<b>⚠️ Bot Error:</b>\n`{e}`\n\nPlease check the token and try again.")
        return False

  async def add_session(self, msg: Message):
    user_id = msg.from_user.id
    session_string = msg.text.strip()
    if len(session_string) < SESSION_STRING_SIZE:
        await msg.reply('That session string appears to be too short. Please send a valid Pyrogram v2 session string.')
        return False
        
    try:
        async with self.client({'session': session_string, 'is_bot': False}) as client:
            user = await client.get_me()

        if await db.is_bot_exist(user_id, user.id):
            await msg.reply_text("This userbot has already been added.")
            return False
        
        await db.add_bot({'id': user.id, 'is_bot': False, 'user_id': user_id, 'name': user.first_name, 'session': session_string, 'username': user.username})
        await msg.reply_text(f"✅ Userbot '{user.first_name}' added successfully.")
        return True
    except SessionPasswordNeeded:
        await msg.reply_text("<b>⚠️ Userbot Error:</b>\nThis session string requires a 2FA password, which is not supported. Please generate a new session string without a password.")
        return False
    except (AuthKeyUnregistered, ApiIdInvalid):
         await msg.reply_text("<b>⚠️ Userbot Error:</b>\nThe session string is invalid or has expired. Please generate a new one.")
         return False
    except Exception as e:
        await msg.reply_text(f"<b>⚠️ An unexpected error occurred:</b>\n`{e}`")
        logger.error(f"Error adding session string: {e}", exc_info=True)
        return False

  async def _add_single_bot_from_bulk(self, token, user_id):
    """Helper for add_bots_bulk to process one token."""
    try:
        async with self.client({'token': token, 'is_bot': True}) as _client:
            _bot = await _client.get_me()
        if await db.is_bot_exist(user_id, _bot.id):
            return "duplicate"
        await db.add_bot({'id': _bot.id, 'is_bot': True, 'user_id': user_id, 'name': _bot.first_name, 'token': token, 'username': _bot.username})
        return "success"
    except Exception:
        return "failed"

  async def add_bots_bulk(self, msg: Message):
    user_id = msg.from_user.id
    processing_msg = await msg.reply_text("`Processing tokens... This may take a moment.`")
    
    tokens = re.findall(r'(\d{8,10}:[a-zA-Z0-9_-]{35})', msg.text)
    if not tokens:
        await processing_msg.edit("No valid bot tokens found in your message.")
        return False

    tasks = [self._add_single_bot_from_bulk(token, user_id) for token in tokens]
    results = await asyncio.gather(*tasks)
    
    success = results.count("success")
    duplicate = results.count("duplicate")
    failed = results.count("failed")
    
    await processing_msg.edit(
        f"<b>Bulk Add Complete</b>\n\n"
        f"● **Total Processed:** `{len(tokens)}`\n"
        f"● **Successfully Added:** `{success}`\n"
        f"● **Duplicates Skipped:** `{duplicate}`\n"
        f"● **Failed:** `{failed}`"
    )
    return True

  async def _add_single_session_from_bulk(self, session_string, user_id):
    """Helper for add_sessions_bulk to process one session string."""
    if len(session_string) < SESSION_STRING_SIZE:
        return "failed"
    try:
        async with self.client({'session': session_string, 'is_bot': False}) as client:
            user = await client.get_me()
        if await db.is_bot_exist(user_id, user.id):
            return "duplicate"
        await db.add_bot({'id': user.id, 'is_bot': False, 'user_id': user_id, 'name': user.first_name, 'session': session_string, 'username': user.username})
        return "success"
    except Exception:
        return "failed"

  async def add_sessions_bulk(self, msg: Message):
    user_id = msg.from_user.id
    processing_msg = await msg.reply_text("`Processing session strings... This may take a moment.`")

    sessions = msg.text.strip().split()
    if not sessions:
        await processing_msg.edit("No session strings found in your message.")
        return False

    tasks = [self._add_single_session_from_bulk(session, user_id) for session in sessions]
    results = await asyncio.gather(*tasks)

    success = results.count("success")
    duplicate = results.count("duplicate")
    failed = results.count("failed")

    await processing_msg.edit(
        f"<b>Bulk Add Complete</b>\n\n"
        f"● **Total Processed:** `{len(sessions)}`\n"
        f"● **Successfully Added:** `{success}`\n"
        f"● **Duplicates Skipped:** `{duplicate}`\n"
        f"● **Failed:** `{failed}`"
    )
    return True

CLIENT = CLIENT()
