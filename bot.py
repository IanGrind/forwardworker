import asyncio
import logging 
import logging.config
from config import Config, temp
from database import db
from aiohttp import web
from plugins import web_server
from operators import start_operators, stop_operators # <-- NEW IMPORT
from pyrogram import Client, __version__, idle
from pyrogram.raw.all import layer 
from pyrogram.enums import ParseMode
from pyrogram.errors import FloodWait 

logging.config.fileConfig('logging.conf')
logging.getLogger().setLevel(logging.INFO)
logging.getLogger("pyrogram").setLevel(logging.ERROR)

PORT = Config.PORT

class Bot(Client): 
    def __init__(self):
        super().__init__(
            Config.BOT_SESSION,
            api_hash=Config.API_HASH,
            api_id=Config.API_ID,
            plugins={
                "root": "plugins"
            },
            bot_token=Config.BOT_TOKEN
        )
        self.log = logging

    async def start(self):
        await super().start()
        me = await self.get_me()
        logging.info(f"{me.first_name} with Pyrogram v{__version__} (Layer {layer}) started.")
        
        temp.BANNED_USERS = await db.get_banned()

        # NEW: Start all operator bots ONCE and keep them running
        await start_operators()

        # Start the web server
        app = web.AppRunner(await web_server())
        await app.setup()
        bind_address = "0.0.0.0"
        await web.TCPSite(app, bind_address, PORT).start()
        
        await idle()

    async def stop(self, *args):
        # NEW: Gracefully stop all running operators
        await stop_operators()
        await super().stop()
        logging.info("Bot has stopped.")
