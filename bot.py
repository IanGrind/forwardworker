# MrSyD
# Telegram Channel @Bot_Cracker
# Developer @syd_xyz




import asyncio
import logging 
import logging.config
# The 'db' import is removed from here to prevent circular dependencies
from config import Config, temp
from database import db
from aiohttp import web
from plugins import web_server
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
            # workers=50, # This line is removed to revert to the stable single-threaded model.
            bot_token=Config.BOT_TOKEN
        )
        self.log = logging

    async def start(self):
        try:
            await super().start()
        except FloodWait as e:
            self.log.warning(f"FloodWait on start: waiting for {e.value} seconds.")
            await asyncio.sleep(e.value)
            await super().start() # Retry start after waiting
            
        me = await self.get_me()
        logging.info(f"{me.first_name} with for pyrogram v{__version__} (Layer {layer}) started on @{me.username}.")
        self.id = me.id
        self.username = me.username
        self.first_name = me.first_name
        self.set_parse_mode(ParseMode.DEFAULT)
        
        # Load banned users on start
        temp.BANNED_USERS = await db.get_banned()

        # Start the web server
        app = web.AppRunner(await web_server())
        await app.setup()
        bind_address = "0.0.0.0"
        await web.TCPSite(app, bind_address, PORT).start()
        
        # Keep the bot running
        await idle()
        logging.info("Bot has stopped.")

    async def stop(self, *args):
        msg = f"@{self.username} stopped. Bye."
        await super().stop()
        logging.info(msg)









# MrSyD
# Telegram Channel @Bot_Cracker
# Developer @syd_xyz
