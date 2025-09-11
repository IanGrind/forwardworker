import os
import re 
import sys
import asyncio 
import logging 
from uuid import uuid4
from database import db 
from config import Config, temp
from pyrogram import Client
from pyrogram.types import Message
from pyrogram.errors import FloodWait

logger = logging.getLogger(__name__)

SESSION_STRING_SIZE = 351

async def start_clone_bot(FwdBot, bot_data):
   await FwdBot.start()
   return FwdBot

class CLIENT: 
  def __init__(self):
     self.api_id = Config.API_ID
     self.api_hash = Config.API_HASH
    
  def client(self, data):
     name = str(uuid4())
     if not data.get('is_bot', True):
        return Client(name=name, api_id=self.api_id, api_hash=self.api_hash, session_string=data['session'], in_memory=True)
     return Client(name=name, api_id=self.api_id, api_hash=self.api_hash, bot_token=data['token'], in_memory=True)
  
  async def add_bot(self, bot, msg: Message):
     user_id = msg.from_user.id
     token = re.search(r'(\d{8,10}:[a-zA-Z0-9_-]{35})', msg.text)
     if not token: return await msg.reply_text("No valid bot token found.")
     try:
       async with Client(name=str(user_id), api_id=self.api_id, api_hash=self.api_hash, bot_token=token.group(1), in_memory=True) as _client:
          _bot = await _client.get_me()
     except Exception as e:
       return await msg.reply_text(f"<b>Bot Error:</b> `{e}`")
     
     if await db.is_bot_exist(user_id, _bot.id):
         return await msg.reply_text("This bot has already been added.")

     await db.add_bot({'id': _bot.id, 'is_bot': True, 'user_id': user_id, 'name': _bot.first_name, 'token': token.group(1), 'username': _bot.username})
     await msg.reply_text("Bot token added. ✓")

  async def add_worker_bot(self, bot, msg: Message):
     user_id = msg.from_user.id
     token = re.search(r'(\d{8,10}:[a-zA-Z0-9_-]{35})', msg.text)
     if not token: return await msg.reply_text("No valid bot token found.")
     try:
       async with Client(name=f"worker_{user_id}", api_id=self.api_id, api_hash=self.api_hash, bot_token=token.group(1), in_memory=True) as _client:
          _bot = await _client.get_me()
     except Exception as e:
       return await msg.reply_text(f"<b>Worker Bot Error:</b> `{e}`")
     
     if await db.is_worker_bot_exist(user_id, _bot.id):
         return await msg.reply_text("This worker bot has already been added.")

     await db.add_worker_bot({'id': _bot.id, 'is_bot': True, 'user_id': user_id, 'name': _bot.first_name, 'token': token.group(1), 'username': _bot.username})
     await msg.reply_text("Worker bot added. ✓")
    
  async def add_session(self, bot, msg: Message):
     user_id = msg.from_user.id
     if len(msg.text) < SESSION_STRING_SIZE: return await msg.reply('Not a valid session string.')
     try:
       async with Client(name=str(user_id), api_id=self.api_id, api_hash=self.api_hash, session_string=msg.text, in_memory=True) as client:
          user = await client.get_me()
     except Exception as e:
       return await msg.reply_text(f"<b>Userbot Error:</b> `{e}`")
     
     if await db.is_bot_exist(user_id, user.id):
         return await msg.reply_text("This userbot has already been added.")
     
     await db.add_bot({'id': user.id, 'is_bot': False, 'user_id': user_id, 'name': user.first_name, 'session': msg.text, 'username': user.username})
     await msg.reply_text("Session added. ✓")

async def update_configs(user_id, key, value):
    configs = await db.get_configs(user_id)
    configs[key] = value
    await db.update_configs(user_id, configs)
