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
    
    # Userbots use get_dialogs, regular bots use get_me()
    if not bot_data.get('is_bot', True):
        # This is a userbot, so we wake it up by fetching dialogs.
        async for _ in client_instance.get_dialogs(limit=1):
            pass
    else:
        # This is a regular bot, so we use the safe get_me() method.
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

  async def add_worker_bot(self, msg: Message):
    user_id = msg.from_user.id
    token_match = re.search(r'(\d{8,10}:[a-zA-Z0-9_-]{35})', msg.text)
    if not token_match:
        await msg.reply_text("That doesn't look like a valid worker bot token.")
        return False
    token = token_match.group(1)

    try:
        async with self.client({'token': token, 'is_bot': True}) as _client:
            _bot = await _client.get_me()
        
        if await db.is_worker_bot_exist(user_id, _bot.id):
            await msg.reply_text("This worker bot has already been added.")
            return False

        await db.add_worker_bot({'id': _bot.id, 'is_bot': True, 'user_id': user_id, 'name': _bot.first_name, 'token': token, 'username': _bot.username})
        await msg.reply_text(f"✅ Worker Bot '{_bot.first_name}' added successfully.")
        return True
    except Exception as e:
        await msg.reply_text(f"<b>⚠️ Worker Bot Error:</b>\n`{e}`\n\nPlease check the token and try again.")
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

CLIENT = CLIENT()

async def update_configs(user_id, key, value):
    configs = await db.get_configs(user_id)
    configs[key] = value
    await db.update_configs(user_id, configs)
